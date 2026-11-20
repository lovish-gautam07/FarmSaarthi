"""Check data/market_names.json against the live data.gov.in API.

    DATA_GOV_IN_API_KEY=... python scripts/check_market_names.py

For every crop it reports how many Kisan Call Centre records exist for that
crop name in a state that grows it, and whether today's mandi prices list it.
A count of 0 means the name is probably spelled differently in that dataset.
The API is slow, so this takes a few minutes.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datagov import KCC_RESOURCE, MANDI_RESOURCE, DataGovClient, load_names  # noqa: E402

# A state where each crop is common, so a zero count really means "wrong name".
TEST_STATE = {
    "rice": "Punjab", "wheat": "Punjab", "maize": "Karnataka", "chickpea": "Madhya Pradesh", "kidneybeans": "Himachal Pradesh",
    "pigeonpeas": "Maharashtra", "mothbeans": "Rajasthan", "mungbean": "Rajasthan", "blackgram": "Tamil Nadu",
    "lentil": "Madhya Pradesh", "pomegranate": "Maharashtra", "banana": "Kerala", "mango": "Uttar Pradesh",
    "grapes": "Maharashtra", "watermelon": "Uttar Pradesh", "muskmelon": "Uttar Pradesh", "apple": "Himachal Pradesh",
    "orange": "Maharashtra", "papaya": "Gujarat", "coconut": "Kerala", "cotton": "Maharashtra", "jute": "West Bengal",
    "coffee": "Karnataka", "sugarcane": "Uttar Pradesh", "millets": "Rajasthan",
}


def total(client, resource, filters):
    params = {"api-key": client.api_key, "format": "json", "limit": 1}
    params.update({f"filters[{k}]": v for k, v in filters.items()})
    try:
        response = client.session.get(f"https://api.data.gov.in/resource/{resource}", params=params, timeout=90)
        response.raise_for_status()
        return response.json().get("total")
    except Exception as error:  # noqa: BLE001 - report and carry on
        return f"error: {error}"


def main():
    client = DataGovClient.from_env()
    if not client.enabled:
        sys.exit("Set DATA_GOV_IN_API_KEY first.")
    names = load_names()
    for crop, entry in names["crops"].items():
        state = TEST_STATE[crop]
        kcc_state = names["kcc_states"].get(state, state.upper())
        mandi_state = names["agmarknet_states"].get(state, state)
        for name in entry["kcc"]:
            print(f"KCC    {crop:12} {kcc_state:17} {name:40} {total(client, KCC_RESOURCE, {'StateName': kcc_state, 'Crop': name})}")
        for name in entry["agmarknet"]:
            print(f"MANDI  {crop:12} {'(all India today)':17} {name:40} {total(client, MANDI_RESOURCE, {'commodity.keyword': name})}")


if __name__ == "__main__":
    main()
