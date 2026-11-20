"""Today's mandi prices (Agmarknet) and Kisan Call Centre answers, from the free data.gov.in API.

Both datasets come from api.data.gov.in with one free API key (DATA_GOV_IN_API_KEY).
The API can be slow (several seconds) and sometimes returns errors, so:
* results are cached for hours,
* a failure pauses calls for a short while instead of piling up requests,
* the page loads these separately, so the main advice never waits for them.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

API_ROOT = "https://api.data.gov.in/resource"
MANDI_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"   # Current daily price of commodities (Agmarknet)
KCC_RESOURCE = "cef25fe2-9231-4128-8aec-2c948fedd43f"     # Kisan Call Centre queries and answers
NAMES_PATH = Path(__file__).parent / "data" / "market_names.json"
REQUEST_TIMEOUT = 25
RETRY_AFTER_FAILURE_SECONDS = 120
PRICE_CACHE_SECONDS = 3 * 60 * 60
KCC_CACHE_SECONDS = 12 * 60 * 60
WEATHER_QUERY = re.compile(r"\bweather\b", re.IGNORECASE)
# data.gov.in silently drops requests that carry Python's default User-Agent
# ("python-requests/..."); any named client is answered normally.
USER_AGENT = "FarmSaarthi/1.0 (crop advice web app)"


def make_session():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


class DataGovClient:
    def __init__(self, api_key=None, session=None):
        self.api_key = (api_key or "").strip()
        self.session = session or make_session()
        self._cache = {}
        self._lock = threading.Lock()
        self._failed_until = 0.0

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("DATA_GOV_IN_API_KEY"))

    @property
    def enabled(self):
        return bool(self.api_key)

    def records(self, resource, filters, limit, sort=None, cache_seconds=PRICE_CACHE_SECONDS):
        """Return a list of records, or None if the API could not be reached."""
        key = (resource, tuple(sorted(filters.items())), limit, tuple(sorted((sort or {}).items())))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < cache_seconds:
                return cached[1]
        if not self.enabled or now < self._failed_until:
            return None

        params = {"api-key": self.api_key, "format": "json", "limit": limit}
        params.update({f"filters[{field}]": value for field, value in filters.items()})
        params.update({f"sort[{field}]": order for field, order in (sort or {}).items()})
        try:
            response = self.session.get(f"{API_ROOT}/{resource}", params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "ok":
                raise ValueError(payload.get("message", "data.gov.in error"))
            rows = payload.get("records") or []
        except (requests.RequestException, ValueError):
            self._failed_until = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
            return None

        with self._lock:
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[key] = (now, rows)
        return rows


def load_names():
    with NAMES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _number(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


class MandiPrices:
    def __init__(self, client, names=None):
        self.client = client
        names = names or load_names()
        self.crop_names = {crop: entry.get("agmarknet", []) for crop, entry in names["crops"].items()}
        self.state_names = names.get("agmarknet_states", {})

    @property
    def enabled(self):
        return self.client.enabled

    def for_crops(self, state, district, crop_ids, limit_markets=3):
        crop_ids = [crop for crop in crop_ids if crop in self.crop_names][:4]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda crop: self._for_crop(state, district, crop, limit_markets), crop_ids))
        return dict(zip(crop_ids, results))

    def _for_crop(self, state, district, crop_id, limit_markets):
        names = self.crop_names.get(crop_id) or []
        if not names:
            return {"status": "not_traded"}
        state_name = self.state_names.get(state, state)
        rows, reached = [], False
        for name in names:
            found = self.client.records(MANDI_RESOURCE, {"state.keyword": state_name, "commodity.keyword": name}, limit=100)
            if found is not None:
                reached = True
                rows.extend(found)
        if not reached:
            return {"status": "unavailable"}

        markets = []
        for row in rows:
            modal = _number(row.get("modal_price"))
            if not modal:
                continue
            markets.append({
                "market": (row.get("market") or "").strip(),
                "district": (row.get("district") or "").strip(),
                "commodity": row.get("commodity"),
                "variety": row.get("variety"),
                "modal_price": modal,
                "min_price": _number(row.get("min_price")),
                "max_price": _number(row.get("max_price")),
                "date": row.get("arrival_date"),
            })
        if not markets:
            return {"status": "none", "state": state}

        home = (district or "").lower()
        markets.sort(key=lambda m: (m["district"].lower() != home, -m["modal_price"]))
        modal_prices = sorted(m["modal_price"] for m in markets)
        return {
            "status": "ok",
            "unit": "₹/quintal",
            "state_median": modal_prices[len(modal_prices) // 2],
            "market_count": len(markets),
            "markets": markets[:limit_markets],
        }


class KccSearch:
    """Find answered Kisan Call Centre questions similar to the farmer's question."""

    def __init__(self, client, names=None, fetch_limit=None):
        self.client = client
        names = names or load_names()
        self.crop_names = {crop: entry.get("kcc", []) for crop, entry in names["crops"].items()}
        self.state_names = names.get("kcc_states", {})
        self.fetch_limit = int(fetch_limit or os.environ.get("KCC_FETCH_LIMIT", 200))

    @property
    def enabled(self):
        return self.client.enabled

    def _fetch(self, state_key, crop_name=None):
        filters = {"StateName": state_key}
        if crop_name:
            filters["Crop"] = crop_name
        return self.client.records(KCC_RESOURCE, filters, limit=self.fetch_limit, sort={"CreatedOn": "desc"},
                                   cache_seconds=KCC_CACHE_SECONDS)

    def search(self, question_en, state, crop_id=None, district=None, limit=3):
        state_key = self.state_names.get(state, state.upper())
        rows, reached = [], False
        for name in self.crop_names.get(crop_id, []) if crop_id else []:
            found = self._fetch(state_key, name)
            if found is not None:
                reached = True
                rows.extend(found)
        if not rows:
            found = self._fetch(state_key)
            if found is not None:
                reached = True
                rows.extend(found)
        if not reached:
            return {"status": "unavailable", "answers": []}
        return {"status": "ok", "answers": rank_answers(question_en, rows, district, limit)}


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def rank_answers(question, rows, district=None, limit=3, min_score=0.12):
    """TF-IDF similarity between the question and past KCC questions (no paid AI)."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    seen, items = set(), []
    for row in rows:
        asked, answer = _clean(row.get("QueryText")), _clean(row.get("KccAns"))
        if not asked or not answer or WEATHER_QUERY.search(asked) or str(row.get("QueryType", "")).lower() == "weather":
            continue
        key = asked.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "question": asked,
            "answer": answer,
            "crop": _clean(row.get("Crop")),
            "district": _clean(row.get("DistrictName")).title(),
            "date": str(row.get("CreatedOn", ""))[:10],
        })
    if not items or not question.strip():
        return []

    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    try:
        matrix = vectorizer.fit_transform([question] + [f"{item['question']} {item['crop']}" for item in items])
    except ValueError:  # only stop words
        return []
    scores = cosine_similarity(matrix[0], matrix[1:])[0]
    home = (district or "").lower()
    ranked = []
    for item, score in zip(items, scores):
        score = float(score) + (0.03 if home and item["district"].lower() == home else 0.0)
        if score >= min_score:
            ranked.append({**item, "score": round(score, 3)})
    ranked.sort(key=lambda item: -item["score"])
    return ranked[:limit]
