"""One-time helper: add latitude/longitude to data/india_districts.json.

Looks up each district headquarters (then the district name) with the free
Open-Meteo geocoding API and keeps only matches inside the right state.
Districts that cannot be matched are left without coordinates; the app then
falls back to the average position of the other districts in that state.

Run from the project root:  python scripts/geocode_districts.py
"""

import json
import re
import sys
import time
from pathlib import Path

import requests

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "india_districts.json"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# GeoNames sometimes uses older or shorter names for these states / UTs.
STATE_ALIASES = {
    "National Capital Territory of Delhi": ["delhi", "nationalcapitalterritoryofdelhi"],
    "Andaman and Nicobar": ["andamanandnicobar", "andamanandnicobarislands"],
    "Dadra and Nagar Haveli and Daman and Diu": ["dadraandnagarhavelianddamananddiu", "dadraandnagarhaveli", "damananddiu"],
    "Ladakh": ["ladakh", "jammuandkashmir"],
    "Telangana": ["telangana", "andhrapradesh"],
}

PLACE_RANK = {"PPLC": 0, "PPLA": 1, "PPLA2": 2, "PPLA3": 3, "PPLA4": 4, "PPL": 5}


def squash(text):
    return re.sub(r"[^a-z]", "", (text or "").lower())


def state_matches(state, admin1):
    allowed = STATE_ALIASES.get(state, [squash(state)])
    return squash(admin1) in allowed


def search(name, session):
    response = session.get(
        GEOCODE_URL,
        params={"name": name, "count": 20, "countryCode": "IN", "language": "en", "format": "json"},
        timeout=15,
    )
    response.raise_for_status()
    time.sleep(0.12)  # stay well inside the free-tier rate limit
    return response.json().get("results", [])


def best_match(results, state, district):
    candidates = [
        r for r in results
        if state_matches(state, r.get("admin1")) and 6 <= r["latitude"] <= 37.5 and 68 <= r["longitude"] <= 97.5
    ]
    if not candidates:
        return None
    district_key = squash(district)

    def rank(r):
        admin2_hit = 0 if district_key and district_key[:5] in squash(r.get("admin2")) else 1
        return (admin2_hit, PLACE_RANK.get(r.get("feature_code"), 9))

    return sorted(candidates, key=rank)[0]


def search_osm(item, session):
    """Fallback for spellings GeoNames does not know (OpenStreetMap Nominatim, max 1 request/second)."""
    for query in (f"{item['district']} district, {item['state']}, India", f"{item.get('headquarters')}, {item['state']}, India"):
        response = session.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5, "countrycodes": "in"},
            timeout=20,
        )
        response.raise_for_status()
        time.sleep(1.1)
        for result in response.json():
            address = result.get("address", {})
            state_name = address.get("state") or address.get("state_district") or address.get("territory") or ""
            lat, lon = float(result["lat"]), float(result["lon"])
            if state_matches(item["state"], state_name) and 6 <= lat <= 37.5 and 68 <= lon <= 97.5:
                return {"latitude": lat, "longitude": lon}
    return None


def main():
    with DATA_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)

    session = requests.Session()
    session.headers["User-Agent"] = "FarmSaarthi-geocoder"
    resolved = 0

    for index, item in enumerate(payload["districts"], start=1):
        if "lat" in item and "lon" in item:
            resolved += 1
            continue
        match, source = None, None
        for query, label in ((item.get("headquarters"), "headquarters"), (item["district"], "district")):
            if not query:
                continue
            match = best_match(search(query, session), item["state"], item["district"])
            if match:
                source = label
                break
        if not match:
            try:
                match = search_osm(item, session)
            except requests.RequestException as error:
                print(f"  openstreetmap lookup failed for {item['district']}: {error}", file=sys.stderr)
                time.sleep(3)
            source = "openstreetmap" if match else None
        if match:
            item["lat"] = round(match["latitude"], 4)
            item["lon"] = round(match["longitude"], 4)
            item["geo_source"] = source
            resolved += 1
        else:
            print(f"  no match: {item['district']} ({item['state']})", file=sys.stderr)
        if index % 100 == 0:
            print(f"{index} districts checked, {resolved} located")

    with DATA_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent="\t")
        file.write("\n")

    print(f"Done: {resolved} of {len(payload['districts'])} districts have coordinates.")


if __name__ == "__main__":
    main()
