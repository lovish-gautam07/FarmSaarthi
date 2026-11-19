import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split


DATA_DIR = Path(__file__).parent / "data"
DATASET_PATH = DATA_DIR / "crop_recommendation.csv"
CROP_PROFILE_PATH = DATA_DIR / "farm_data.json"
CROP_META_PATH = DATA_DIR / "crop_meta.json"
LOCATION_DATA_PATH = DATA_DIR / "india_districts.json"

FEATURES = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
GENERATED_ROWS_PER_CROP = 100  # same as each crop in the public dataset

PEST_MAP = {
    "low": 0,
    "medium": 1,
    "high": 2
}

CHECK_WEIGHTS = {"soil": 12, "temperature": 10, "ph": 8, "rainfall": 10, "humidity": 8}
OUT_OF_SEASON_FACTOR = 0.55
OUT_OF_REGION_FACTOR = 0.5

SEASON_MONTHS = {"kharif": "June–October", "rabi": "November–February", "zaid": "March–May"}
SPRAY_RAIN_MM = 5          # rain that would wash off a spray
MAX_ACTIONS = 6
ACTION_ORDER = {"alert": 0, "warn": 1, "good": 2, "info": 3}

# IMD calls 64.5 mm or more of rain in one day "heavy rainfall".
HEAVY_RAIN_MM_PER_DAY = 64.5
HEAT_ALERT_CELSIUS = 38

STATE_PROFILES = {
    "Andhra Pradesh": {"soils": ["black", "loamy"], "temperature": (27, 34), "humidity": (58, 82), "rainfall": (65, 120), "ph": (6.2, 7.8), "season": "kharif", "nitrogen": (62, 88), "phosphorus": (30, 52), "potassium": (34, 58)},
    "Arunachal Pradesh": {"soils": ["loamy", "clay"], "temperature": (16, 26), "humidity": (68, 90), "rainfall": (140, 260), "ph": (5.2, 6.8), "season": "kharif", "nitrogen": (56, 82), "phosphorus": (26, 46), "potassium": (32, 54)},
    "Assam": {"soils": ["loamy", "clay"], "temperature": (22, 31), "humidity": (72, 90), "rainfall": (120, 240), "ph": (5.1, 6.9), "season": "kharif", "nitrogen": (58, 84), "phosphorus": (28, 48), "potassium": (32, 52)},
    "Bihar": {"soils": ["loamy", "clay"], "temperature": (20, 33), "humidity": (55, 82), "rainfall": (70, 150), "ph": (6.3, 7.9), "season": "rabi", "nitrogen": (60, 82), "phosphorus": (30, 50), "potassium": (28, 48)},
    "Chandigarh": {"soils": ["loamy"], "temperature": (18, 32), "humidity": (45, 72), "rainfall": (55, 110), "ph": (6.7, 7.8), "season": "rabi", "nitrogen": (54, 76), "phosphorus": (28, 46), "potassium": (26, 42)},
    "Chhattisgarh": {"soils": ["black", "loamy"], "temperature": (23, 34), "humidity": (55, 80), "rainfall": (90, 170), "ph": (6.0, 7.4), "season": "kharif", "nitrogen": (58, 82), "phosphorus": (26, 46), "potassium": (32, 56)},
    "Dadra and Nagar Haveli and Daman and Diu": {"soils": ["sandy", "loamy"], "temperature": (24, 33), "humidity": (62, 86), "rainfall": (85, 170), "ph": (6.1, 7.5), "season": "zaid", "nitrogen": (56, 82), "phosphorus": (26, 44), "potassium": (30, 50)},
    "Goa": {"soils": ["sandy", "loamy"], "temperature": (24, 32), "humidity": (70, 88), "rainfall": (160, 280), "ph": (5.6, 6.8), "season": "kharif", "nitrogen": (52, 76), "phosphorus": (24, 42), "potassium": (28, 48)},
    "Gujarat": {"soils": ["black", "sandy"], "temperature": (24, 36), "humidity": (38, 72), "rainfall": (35, 120), "ph": (6.8, 8.2), "season": "zaid", "nitrogen": (48, 76), "phosphorus": (22, 42), "potassium": (34, 60)},
    "Haryana": {"soils": ["loamy", "sandy"], "temperature": (19, 34), "humidity": (38, 68), "rainfall": (35, 95), "ph": (6.9, 8.1), "season": "rabi", "nitrogen": (50, 74), "phosphorus": (24, 44), "potassium": (26, 46)},
    "Himachal Pradesh": {"soils": ["loamy", "clay"], "temperature": (10, 24), "humidity": (48, 76), "rainfall": (60, 130), "ph": (5.6, 7.0), "season": "rabi", "nitrogen": (54, 78), "phosphorus": (26, 44), "potassium": (28, 48)},
    "Jammu and Kashmir": {"soils": ["loamy", "clay"], "temperature": (9, 24), "humidity": (46, 74), "rainfall": (45, 105), "ph": (6.1, 7.5), "season": "rabi", "nitrogen": (50, 76), "phosphorus": (24, 44), "potassium": (26, 46)},
    "Jharkhand": {"soils": ["loamy", "sandy"], "temperature": (21, 33), "humidity": (52, 78), "rainfall": (80, 150), "ph": (5.4, 7.0), "season": "kharif", "nitrogen": (54, 80), "phosphorus": (24, 42), "potassium": (28, 48)},
    "Karnataka": {"soils": ["black", "sandy"], "temperature": (21, 33), "humidity": (44, 74), "rainfall": (45, 125), "ph": (6.0, 7.8), "season": "kharif", "nitrogen": (50, 76), "phosphorus": (24, 42), "potassium": (30, 52)},
    "Kerala": {"soils": ["clay", "loamy"], "temperature": (24, 32), "humidity": (74, 92), "rainfall": (160, 300), "ph": (5.0, 6.5), "season": "kharif", "nitrogen": (58, 82), "phosphorus": (24, 42), "potassium": (30, 52)},
    "Ladakh": {"soils": ["sandy", "loamy"], "temperature": (4, 19), "humidity": (20, 42), "rainfall": (10, 40), "ph": (7.0, 8.4), "season": "zaid", "nitrogen": (34, 56), "phosphorus": (18, 34), "potassium": (22, 38)},
    "Lakshadweep": {"soils": ["sandy"], "temperature": (26, 32), "humidity": (75, 90), "rainfall": (140, 240), "ph": (6.5, 7.8), "season": "zaid", "nitrogen": (44, 66), "phosphorus": (20, 36), "potassium": (24, 42)},
    "Madhya Pradesh": {"soils": ["black", "loamy"], "temperature": (20, 34), "humidity": (42, 72), "rainfall": (50, 135), "ph": (6.3, 7.9), "season": "kharif", "nitrogen": (54, 80), "phosphorus": (26, 46), "potassium": (30, 54)},
    "Maharashtra": {"soils": ["black", "loamy"], "temperature": (22, 34), "humidity": (46, 74), "rainfall": (45, 150), "ph": (6.2, 7.8), "season": "kharif", "nitrogen": (52, 80), "phosphorus": (24, 46), "potassium": (30, 56)},
    "Manipur": {"soils": ["clay", "loamy"], "temperature": (18, 28), "humidity": (64, 86), "rainfall": (95, 180), "ph": (5.2, 6.8), "season": "kharif", "nitrogen": (56, 80), "phosphorus": (24, 42), "potassium": (30, 48)},
    "Meghalaya": {"soils": ["clay", "loamy"], "temperature": (15, 25), "humidity": (72, 92), "rainfall": (180, 320), "ph": (4.8, 6.5), "season": "kharif", "nitrogen": (54, 78), "phosphorus": (22, 40), "potassium": (28, 46)},
    "Mizoram": {"soils": ["loamy", "clay"], "temperature": (17, 28), "humidity": (68, 88), "rainfall": (160, 260), "ph": (5.0, 6.6), "season": "kharif", "nitrogen": (54, 78), "phosphorus": (22, 40), "potassium": (28, 46)},
    "Nagaland": {"soils": ["loamy", "clay"], "temperature": (17, 28), "humidity": (64, 86), "rainfall": (120, 220), "ph": (5.2, 6.8), "season": "kharif", "nitrogen": (54, 78), "phosphorus": (22, 40), "potassium": (28, 46)},
    "National Capital Territory of Delhi": {"soils": ["loamy", "sandy"], "temperature": (18, 35), "humidity": (35, 70), "rainfall": (30, 95), "ph": (6.7, 8.0), "season": "rabi", "nitrogen": (46, 68), "phosphorus": (22, 40), "potassium": (24, 42)},
    "Odisha": {"soils": ["clay", "loamy"], "temperature": (23, 34), "humidity": (60, 86), "rainfall": (85, 180), "ph": (5.4, 7.1), "season": "kharif", "nitrogen": (56, 82), "phosphorus": (24, 42), "potassium": (28, 48)},
    "Puducherry": {"soils": ["clay", "loamy"], "temperature": (25, 33), "humidity": (68, 88), "rainfall": (75, 165), "ph": (6.0, 7.4), "season": "zaid", "nitrogen": (54, 76), "phosphorus": (24, 42), "potassium": (28, 48)},
    "Punjab": {"soils": ["loamy", "sandy"], "temperature": (18, 32), "humidity": (40, 68), "rainfall": (35, 95), "ph": (6.8, 8.0), "season": "rabi", "nitrogen": (52, 76), "phosphorus": (24, 46), "potassium": (24, 44)},
    "Rajasthan": {"soils": ["sandy", "loamy"], "temperature": (22, 38), "humidity": (22, 55), "rainfall": (12, 70), "ph": (7.0, 8.4), "season": "zaid", "nitrogen": (34, 58), "phosphorus": (16, 34), "potassium": (20, 40)},
    "Sikkim": {"soils": ["loamy", "clay"], "temperature": (10, 22), "humidity": (62, 84), "rainfall": (100, 220), "ph": (5.0, 6.6), "season": "kharif", "nitrogen": (52, 74), "phosphorus": (22, 40), "potassium": (28, 46)},
    "Tamil Nadu": {"soils": ["clay", "loamy"], "temperature": (24, 34), "humidity": (52, 78), "rainfall": (35, 110), "ph": (6.1, 7.7), "season": "zaid", "nitrogen": (52, 78), "phosphorus": (24, 44), "potassium": (30, 52)},
    "Telangana": {"soils": ["black", "loamy"], "temperature": (24, 35), "humidity": (42, 70), "rainfall": (45, 110), "ph": (6.4, 7.9), "season": "kharif", "nitrogen": (50, 76), "phosphorus": (24, 42), "potassium": (30, 56)},
    "Tripura": {"soils": ["clay", "loamy"], "temperature": (23, 31), "humidity": (70, 88), "rainfall": (120, 210), "ph": (5.2, 6.8), "season": "kharif", "nitrogen": (56, 80), "phosphorus": (22, 40), "potassium": (28, 46)},
    "Uttar Pradesh": {"soils": ["loamy", "clay"], "temperature": (19, 34), "humidity": (42, 74), "rainfall": (45, 120), "ph": (6.5, 8.0), "season": "rabi", "nitrogen": (50, 76), "phosphorus": (24, 44), "potassium": (24, 44)},
    "Uttarakhand": {"soils": ["loamy", "clay"], "temperature": (11, 24), "humidity": (48, 78), "rainfall": (70, 160), "ph": (5.4, 7.0), "season": "rabi", "nitrogen": (52, 76), "phosphorus": (22, 40), "potassium": (26, 44)},
    "West Bengal": {"soils": ["clay", "loamy"], "temperature": (22, 33), "humidity": (62, 86), "rainfall": (95, 210), "ph": (5.5, 7.2), "season": "kharif", "nitrogen": (56, 80), "phosphorus": (24, 42), "potassium": (28, 48)},
    "Andaman and Nicobar": {"soils": ["sandy", "clay"], "temperature": (25, 31), "humidity": (74, 90), "rainfall": (160, 290), "ph": (5.5, 7.0), "season": "zaid", "nitrogen": (50, 74), "phosphorus": (20, 38), "potassium": (26, 44)}
}

STATE_LANGUAGES = {
    "Andhra Pradesh": ["en", "hi", "te"],
    "Arunachal Pradesh": ["en", "hi"],
    "Assam": ["en", "hi", "as", "bn"],
    "Bihar": ["en", "hi"],
    "Chandigarh": ["en", "hi", "pa"],
    "Chhattisgarh": ["en", "hi"],
    "Dadra and Nagar Haveli and Daman and Diu": ["en", "hi", "gu"],
    "Goa": ["en", "hi", "mr", "kn"],
    "Gujarat": ["en", "hi", "gu"],
    "Haryana": ["en", "hi"],
    "Himachal Pradesh": ["en", "hi"],
    "Jammu and Kashmir": ["en", "hi", "ur"],
    "Jharkhand": ["en", "hi"],
    "Karnataka": ["en", "hi", "kn"],
    "Kerala": ["en", "hi", "ml"],
    "Ladakh": ["en", "hi"],
    "Lakshadweep": ["en", "ml"],
    "Madhya Pradesh": ["en", "hi"],
    "Maharashtra": ["en", "hi", "mr"],
    "Manipur": ["en", "hi"],
    "Meghalaya": ["en", "hi"],
    "Mizoram": ["en", "hi"],
    "Nagaland": ["en", "hi"],
    "National Capital Territory of Delhi": ["en", "hi", "pa", "ur"],
    "Odisha": ["en", "hi", "or"],
    "Puducherry": ["en", "ta"],
    "Punjab": ["en", "hi", "pa"],
    "Rajasthan": ["en", "hi"],
    "Sikkim": ["en", "hi"],
    "Tamil Nadu": ["en", "hi", "ta"],
    "Telangana": ["en", "hi", "te"],
    "Tripura": ["en", "hi", "bn"],
    "Uttar Pradesh": ["en", "hi"],
    "Uttarakhand": ["en", "hi"],
    "West Bengal": ["en", "hi", "bn"],
    "Andaman and Nicobar": ["en", "hi", "bn"]
}

DEFAULT_PROFILE = {
    "soils": ["loamy", "clay"],
    "temperature": (20, 32),
    "humidity": (48, 78),
    "rainfall": (50, 140),
    "ph": (6.0, 7.5),
    "season": "kharif",
    "nitrogen": (52, 78),
    "phosphorus": (24, 44),
    "potassium": (28, 48)
}


def season_for(today):
    """Indian cropping season for a date: kharif (Jun-Oct), rabi (Nov-Feb), zaid (Mar-May)."""
    if 6 <= today.month <= 10:
        return "kharif"
    if today.month in (11, 12, 1, 2):
        return "rabi"
    return "zaid"


class FarmSaarthiEngine:
    def __init__(self, weather_client=None):
        with CROP_PROFILE_PATH.open("r", encoding="utf-8") as file:
            self.crop_profiles = json.load(file)["crops"]
        with CROP_META_PATH.open("r", encoding="utf-8") as file:
            self.crop_meta = json.load(file)["crops"]
        with LOCATION_DATA_PATH.open("r", encoding="utf-8") as file:
            self.location_data = json.load(file)["districts"]

        self.weather = weather_client
        self.state_centers = self._state_centers()
        self.dataset_rows = 0
        self.generated_rows = 0
        self.training_data = self._build_training_dataset()
        self.crop_ranges = self._crop_ranges()
        self.model_accuracy = 0.0
        self.model = self._train_model()

    # ----- locations -------------------------------------------------------

    def list_states(self):
        return sorted({item["state"] for item in self.location_data})

    def list_districts(self):
        return self.location_data

    def district_options(self):
        """Just what the page needs to fill the state and district pickers."""
        return [{"state": item["state"], "district": item["district"]} for item in self.location_data]

    def get_district(self, district_name, state_name=None):
        for district in self.location_data:
            if district["district"].lower() == (district_name or "").lower():
                if state_name and district["state"] != state_name:
                    continue
                return district
        return None

    def _state_centers(self):
        sums = {}
        for item in self.location_data:
            if "lat" in item and "lon" in item:
                total = sums.setdefault(item["state"], [0.0, 0.0, 0])
                total[0] += item["lat"]
                total[1] += item["lon"]
                total[2] += 1
        return {state: (lat / n, lon / n) for state, (lat, lon, n) in sums.items()}

    def _coordinates(self, raw_district):
        if "lat" in raw_district and "lon" in raw_district:
            return raw_district["lat"], raw_district["lon"]
        return self.state_centers.get(raw_district["state"], (None, None))

    # ----- recommendation --------------------------------------------------

    def recommend(self, district_name, preferred_language, crop_interest="", state_name=None, today=None):
        raw_district = self.get_district(district_name, state_name)
        if not raw_district:
            return {"error": "District not found for the selected state."}

        today = today or date.today()
        district = self._build_district_context(raw_district, today)
        features = pd.DataFrame([self._district_features(district)], columns=FEATURES)
        probabilities = self.model.predict_proba(features)[0]
        interest_terms = self._interest_terms(crop_interest)
        prediction_rows = []

        for label, probability in zip(self.model.classes_, probabilities):
            checks = self._checks(district, label)
            interested = self._matches_interest(label, interest_terms)
            prediction_rows.append(
                {
                    "crop": self.crop_meta[label]["name"],
                    "crop_id": label,
                    "score": self._blend_score(float(probability), checks, interested),
                    "in_season": checks["season"],
                    "in_region": checks["region"],
                    "interested": interested,
                    "reasons": self._build_reasons(district, label, checks, interested)
                }
            )

        # Crops grown in this state and sown now come first; the rest only fill gaps.
        prediction_rows.sort(key=lambda item: (item["in_region"], item["in_season"], item["score"]), reverse=True)
        top_crops = prediction_rows[:3]

        return {
            "district": district,
            "language": preferred_language,
            "top_crops": top_crops,
            "interest_notes": self._interest_notes(district, prediction_rows, top_crops),
            "actions": self._actions(district, top_crops),
            "irrigation_advice": self._irrigation_advice(district),
            "fertilizer_advice": self._fertilizer_advice(district),
            "alerts": self._generate_alerts(district),
            "decision_score": self._decision_score(district, preferred_language, top_crops[0]["score"]),
            "model_summary": {
                "algorithm": "Random Forest Classifier",
                "training_rows": int(len(self.training_data)),
                "dataset_rows": self.dataset_rows,
                "generated_rows": self.generated_rows,
                "crops": len(self.model.classes_),
                "accuracy": round(self.model_accuracy * 100, 1),
                "accuracy_note": "Measured on held-out rows of the training data, not tested in real fields.",
                "districts_loaded": len(self.location_data)
            }
        }

    # ----- training --------------------------------------------------------

    def _build_training_dataset(self):
        dataset = pd.read_csv(DATASET_PATH)
        known = set(dataset["label"])
        rng = np.random.default_rng(42)
        generated = []

        # Crops missing from the public dataset (wheat, sugarcane, millets) are
        # generated from the hand-written growing ranges in farm_data.json.
        for crop in self.crop_profiles:
            label = crop["name"].lower()
            if label in known:
                continue
            for _ in range(GENERATED_ROWS_PER_CROP):
                generated.append(
                    {
                        "N": int(rng.integers(crop["nitrogen"][0], crop["nitrogen"][1] + 1)),
                        "P": int(rng.integers(crop["phosphorus"][0], crop["phosphorus"][1] + 1)),
                        "K": int(rng.integers(crop["potassium"][0], crop["potassium"][1] + 1)),
                        "temperature": round(float(rng.uniform(crop["temp_min"], crop["temp_max"])), 1),
                        "humidity": round(float(rng.uniform(crop["humidity_min"], crop["humidity_max"])), 1),
                        "ph": round(float(rng.uniform(crop["ph_min"], crop["ph_max"])), 1),
                        "rainfall": round(float(rng.uniform(crop["rainfall_min"], crop["rainfall_max"])), 1),
                        "label": label
                    }
                )

        missing_meta = (known | {row["label"] for row in generated}) - set(self.crop_meta)
        if missing_meta:
            raise ValueError(f"crop_meta.json has no entry for: {sorted(missing_meta)}")

        self.dataset_rows = int(len(dataset))
        self.generated_rows = len(generated)
        return pd.concat([dataset, pd.DataFrame(generated)], ignore_index=True)

    def _crop_ranges(self):
        """Comfortable range per crop: 5th-95th percentile of its training rows, widened a little.

        The public dataset's ranges are very narrow (e.g. rice only between 20 and 27 °C),
        so without widening almost no real district would ever "fit".
        """
        ranges = {}
        for label, group in self.training_data.groupby("label"):
            low = group[["temperature", "humidity", "ph", "rainfall"]].quantile(0.05)
            high = group[["temperature", "humidity", "ph", "rainfall"]].quantile(0.95)
            ranges[label] = {
                "temperature": (low["temperature"] - 3, high["temperature"] + 3),
                "humidity": (max(0.0, low["humidity"] - 10), min(100.0, high["humidity"] + 10)),
                "ph": (low["ph"] - 0.3, high["ph"] + 0.3),
                "rainfall": (low["rainfall"] * 0.7, high["rainfall"] * 1.3),
            }
        return ranges

    def _train_model(self):
        features = self.training_data[FEATURES]
        labels = self.training_data["label"]

        X_train, X_test, y_train, y_test = train_test_split(
            features,
            labels,
            test_size=0.2,
            random_state=42,
            stratify=labels
        )

        model = RandomForestClassifier(
            n_estimators=240,
            max_depth=12,
            min_samples_split=4,
            random_state=42
        )
        model.fit(X_train, y_train)
        predictions = model.predict(X_test)
        self.model_accuracy = accuracy_score(y_test, predictions)
        return model

    # ----- district conditions ---------------------------------------------

    def _build_district_context(self, raw_district, today):
        profile = STATE_PROFILES.get(raw_district["state"], DEFAULT_PROFILE)
        seed = sum(ord(char) for char in f"{raw_district['state']}-{raw_district['district']}")

        # Soil and market values are state-level estimates: there is no free
        # district-level soil or mandi-price feed wired in yet.
        soil = profile["soils"][seed % len(profile["soils"])]
        ph = round(self._spread(profile["ph"], seed, 4, decimal=True), 1)
        nitrogen = int(self._spread(profile["nitrogen"], seed, 5))
        phosphorus = int(self._spread(profile["phosphorus"], seed, 6))
        potassium = int(self._spread(profile["potassium"], seed, 7))
        market_price_index = int(62 + (seed % 27))

        lat, lon = self._coordinates(raw_district)
        forecast = None
        if self.weather is not None and lat is not None:
            forecast = self.weather.get(lat, lon, today)

        if forecast:
            temperature = forecast["temperature"]
            humidity = forecast["humidity"]
            rainfall = forecast["rainfall_monthly_mm"]
        else:
            temperature = self._spread(profile["temperature"], seed, 1)
            humidity = self._spread(profile["humidity"], seed, 2)
            rainfall = self._spread(profile["rainfall"], seed, 3)

        humidity_risk = humidity >= 80 or rainfall >= 140
        if humidity_risk and temperature > 28:
            pest_risk = "high"
        elif humidity > 62 or rainfall > 85:
            pest_risk = "medium"
        else:
            pest_risk = "low"

        return {
            "name": raw_district["district"],
            "state": raw_district["state"],
            "state_code": raw_district["stateCode"],
            "district_code": raw_district["districtCode"],
            "lat": lat,
            "lon": lon,
            "soil": soil,
            "temperature": round(float(temperature), 1),
            "humidity": int(round(humidity)),
            "rainfall_monthly_mm": int(round(rainfall)),
            "season": season_for(today),
            "market_price_index": market_price_index,
            "pest_risk": pest_risk,
            "nitrogen": nitrogen,
            "phosphorus": phosphorus,
            "potassium": potassium,
            "ph": ph,
            "languages": STATE_LANGUAGES.get(raw_district["state"], ["en", "hi"]),
            "forecast": forecast,
            "sources": {
                "weather": "live" if forecast else "estimate",
                "soil": "estimate",
                "market": "estimate"
            }
        }

    def _spread(self, bounds, seed, offset, decimal=False):
        low, high = bounds
        span = high - low
        value = low + ((seed * (offset * 13 + 7)) % 1000) / 1000 * span
        return round(value, 1) if decimal else value

    def _district_features(self, district):
        return {
            "N": district["nitrogen"],
            "P": district["phosphorus"],
            "K": district["potassium"],
            "temperature": district["temperature"],
            "humidity": district["humidity"],
            "ph": district["ph"],
            "rainfall": district["rainfall_monthly_mm"]
        }

    # ----- scoring ---------------------------------------------------------

    def _checks(self, district, label):
        meta = self.crop_meta[label]
        ranges = self.crop_ranges[label]

        def within(name, value):
            low, high = ranges[name]
            return low <= value <= high

        return {
            "soil": district["soil"] in meta["soils"],
            "temperature": within("temperature", district["temperature"]),
            "ph": within("ph", district["ph"]),
            "rainfall": within("rainfall", district["rainfall_monthly_mm"]),
            "humidity": within("humidity", district["humidity"]),
            "season": "perennial" in meta["seasons"] or district["season"] in meta["seasons"],
            "region": not meta.get("states") or district["state"] in meta["states"],
        }

    def _blend_score(self, probability, checks, interested):
        score = probability * 62 + sum(weight for name, weight in CHECK_WEIGHTS.items() if checks[name])
        if interested:
            score += 8
        if not checks["season"]:
            score *= OUT_OF_SEASON_FACTOR
        if not checks["region"]:
            score *= OUT_OF_REGION_FACTOR
        return round(min(score, 99.5), 1)

    @staticmethod
    def _interest_terms(crop_interest):
        text = (crop_interest or "").strip().lower()
        if not text:
            return set(), ""
        tokens = {token for token in re.split(r"[\s,/;।|]+", text) if token}
        return tokens, f" {text} "

    def _matches_interest(self, label, interest_terms):
        tokens, text = interest_terms
        if not tokens:
            return False
        meta = self.crop_meta[label]
        for term in [label, meta["name"].lower(), *meta["aliases"]]:
            term = term.lower()
            if term in tokens or (" " in term and f" {term} " in text):
                return True
        return False

    def _build_reasons(self, district, label, checks, interested):
        meta = self.crop_meta[label]
        reasons = []

        if interested:
            reasons.append("Matches the farmer's stated crop interest.")
        if not checks["region"]:
            reasons.append(f"Not commonly grown in {district['state']}.")
        if not checks["season"]:
            reasons.append(f"Usually sown in {self._season_names(meta['seasons'])}, not the current {district['season']} season.")
        elif "perennial" in meta["seasons"]:
            reasons.append("Orchard or plantation crop, not tied to one sowing season.")
        else:
            reasons.append(f"Suits the current {district['season']} season.")
        if checks["soil"]:
            reasons.append(f"Grows well in {district['soil']} soil.")
        if checks["temperature"]:
            reasons.append("Temperature is within the crop's comfortable range.")
        if checks["rainfall"]:
            reasons.append("Recent rainfall suits this crop.")
        if checks["ph"]:
            reasons.append("Soil pH supports healthy growth.")
        if checks["humidity"]:
            reasons.append("Humidity suits this crop.")

        return reasons[:3]

    @staticmethod
    def _season_names(seasons):
        return " or ".join(f"{season} ({SEASON_MONTHS[season]})" for season in seasons if season in SEASON_MONTHS)

    def _interest_notes(self, district, rows, top_crops):
        """Explain crops the farmer asked about that did not make the top 3."""
        shown = {row["crop_id"] for row in top_crops}
        notes = []
        for row in rows:
            if not row["interested"] or row["crop_id"] in shown:
                continue
            meta = self.crop_meta[row["crop_id"]]
            if not row["in_region"]:
                text = f"{meta['name']} is not commonly grown in {district['state']}. Ask your local Krishi Vigyan Kendra before trying it."
            elif not row["in_season"]:
                text = f"{meta['name']} is usually sown in {self._season_names(meta['seasons'])}, not now in the {district['season']} season."
            else:
                text = f"{meta['name']} can be grown now, but current conditions suit the crops above better (score {row['score']}/100)."
            notes.append({"crop": meta["name"], "crop_id": row["crop_id"], "score": row["score"], "text": text})
        return notes[:3]

    # ----- action cards ----------------------------------------------------

    @staticmethod
    def _day_name(day, index):
        if index == 0:
            return "today"
        if index == 1:
            return "tomorrow"
        return "on " + date.fromisoformat(day["date"]).strftime("%A")

    def _actions(self, district, top_crops):
        """Short "what to do now" cards, most urgent first."""
        cards = []

        def add(level, icon, title, text, estimate=False):
            cards.append({"level": level, "icon": icon, "title": title, "text": text, "estimate": estimate})

        forecast = district["forecast"]
        season = district["season"]

        sow_now = [crop["crop"] for crop in top_crops if crop["in_season"] and crop["in_region"]]
        if sow_now:
            add("good", "seed", f"Best crops now: {', '.join(sow_now)}",
                f"Best matches for {district['name']} in the {season} season ({SEASON_MONTHS[season]}).")
        else:
            add("warn", "seed", "No strong crop match this season",
                "Talk to your local Krishi Vigyan Kendra before sowing.")

        if forecast:
            days = forecast.get("next_days", [])
            heavy = next(((i, d) for i, d in enumerate(days) if d["rain_mm"] >= HEAVY_RAIN_MM_PER_DAY), None)
            if heavy:
                index, day = heavy
                add("alert", "rain", f"Heavy rain {self._day_name(day, index)}",
                    f"Up to {day['rain_mm']:.0f} mm in one day. Clear drainage channels and delay fertilizer.")
            if forecast["max_temperature_next_7_days"] >= HEAT_ALERT_CELSIUS:
                add("alert", "heat", f"Heat: up to {forecast['max_temperature_next_7_days']:.0f} °C this week",
                    "Water in the early morning or evening. Avoid afternoon field work and spraying.")
            if season == "kharif" and forecast["rain_past_30_days_mm"] < 30 and forecast["rain_next_14_days_mm"] < 10:
                add("alert", "sun", "Dry spell",
                    "Very little monsoon rain recently and none expected for two weeks. Save water and mulch the soil.")

            rain_soon = next(((i, d) for i, d in enumerate(days[:3]) if d["rain_mm"] >= SPRAY_RAIN_MM), None)
            if rain_soon:
                index, day = rain_soon
                add("warn", "no-spray", "Don't spray now",
                    f"About {day['rain_mm']:.0f} mm of rain is expected {self._day_name(day, index)}. Spray after the rain so it is not washed off.")
            elif district["pest_risk"] != "low":
                add("good", "spray", "Dry days ahead: good for spraying",
                    "No rain expected for 3 days. If you need to spray, do it early morning or evening when it is calm.")

            rain = forecast["rain_next_7_days_mm"]
            if rain >= 50:
                add("info", "rain", "Skip irrigation this week",
                    f"About {rain:.0f} mm of rain is expected in the next 7 days. Keep drainage channels clear.")
            elif rain >= 15:
                add("info", "water", "Check soil before watering",
                    f"About {rain:.0f} mm of rain is expected in the next 7 days. Water only if the top soil is dry.")
            elif rain < 1:
                add("warn", "water", "Irrigate this week",
                    "No rain is expected in the next 7 days. Water early in the morning and mulch to save moisture.")
            else:
                add("warn", "water", "Irrigate this week",
                    f"Only {rain:.0f} mm of rain is expected in the next 7 days. Water early in the morning and mulch to save moisture.")
        else:
            rainfall = district["rainfall_monthly_mm"]
            if district["temperature"] >= 34:
                add("alert", "heat", "Hot weather",
                    "Water in the early morning or evening. Avoid afternoon field work and spraying.", estimate=True)
            if rainfall < 45:
                add("warn", "water", "Plan irrigation",
                    "Rainfall is usually low at this time. Use drip or mulching to save water.", estimate=True)
            elif rainfall > 150:
                add("info", "rain", "Keep drainage clear",
                    "Rainfall is usually high at this time. Avoid waterlogging around roots.", estimate=True)
            else:
                add("info", "water", "Check soil before watering",
                    "Moderate rain is usual now. Water only if the top soil is dry.", estimate=True)

        if district["humidity"] > 82:
            add("warn", "fungus", "High fungal disease risk",
                "Humid weather favours leaf spots and mildew. Check the underside of leaves and remove infected ones.")
        if district["pest_risk"] == "high":
            add("warn", "bug", "Check for pests twice a week", "Look at leaves and stems for insects, eggs and holes.")
        elif district["pest_risk"] == "medium":
            add("info", "bug", "Check for pests every week", "Look at leaves and stems for insects, eggs and holes.")

        add("info", "soil", "Fertilizer tip", self._fertilizer_tips(district)[0], estimate=True)

        cards.sort(key=lambda card: ACTION_ORDER[card["level"]])
        return cards[:MAX_ACTIONS]

    # ----- advice ----------------------------------------------------------

    def _irrigation_advice(self, district):
        forecast = district["forecast"]
        if forecast:
            rain = forecast["rain_next_7_days_mm"]
            if rain >= 50:
                return f"About {rain:.0f} mm of rain is forecast in the next 7 days. Skip extra irrigation and keep drainage channels clear to prevent waterlogging."
            if rain >= 15:
                return f"About {rain:.0f} mm of rain is forecast in the next 7 days. Check field moisture before irrigating and avoid unnecessary watering."
            if rain < 1:
                return "No rain is forecast in the next 7 days. Plan irrigation; drip, mulching and early-morning watering reduce moisture loss."
            return f"Only {rain:.0f} mm of rain is forecast in the next 7 days. Plan irrigation; drip, mulching and early-morning watering reduce moisture loss."

        rainfall = district["rainfall_monthly_mm"]
        if rainfall < 45:
            return "Low rainfall is expected. Use drip irrigation, mulching, and early-morning watering to reduce moisture loss."
        if rainfall < 95:
            return "Moderate rainfall is expected. Schedule irrigation after checking field moisture and avoid unnecessary watering."
        return "High rainfall is expected. Reduce extra irrigation and keep field drainage channels clear to prevent waterlogging."

    def _fertilizer_advice(self, district):
        tips = self._fertilizer_tips(district)[:2]
        tips.append("These soil values are state-level estimates; a Soil Health Card test gives exact values for your field.")
        return " ".join(tips)

    def _fertilizer_tips(self, district):
        tips = []

        if district["nitrogen"] < 55:
            tips.append("Nitrogen is on the lower side, so split nitrogen application can improve vegetative growth.")
        if district["phosphorus"] < 30:
            tips.append("Phosphorus is limited. Basal phosphorus placement near roots may improve crop establishment.")
        if district["potassium"] < 35:
            tips.append("Potassium is low. Add potash support to strengthen stress tolerance and grain filling.")
        if district["ph"] > 7.6:
            tips.append("Soil is slightly alkaline. Organic compost can improve micronutrient availability.")
        if district["ph"] < 5.6:
            tips.append("Soil is mildly acidic. Liming may help balance soil reaction for sensitive crops.")

        if not tips:
            tips.append("NPK balance looks stable. Prefer soil-test-based micronutrient correction and avoid over-application.")
        return tips

    def _generate_alerts(self, district):
        alerts = []
        forecast = district["forecast"]

        if forecast:
            if forecast["max_temperature_next_7_days"] >= HEAT_ALERT_CELSIUS:
                alerts.append(f"Heat alert: up to {forecast['max_temperature_next_7_days']:.0f} °C is forecast this week. Protect young plants, avoid afternoon spraying, and irrigate during cooler hours.")
            if forecast["max_daily_rain_next_7_days_mm"] >= HEAVY_RAIN_MM_PER_DAY:
                alerts.append(f"Heavy rainfall alert: up to {forecast['max_daily_rain_next_7_days_mm']:.0f} mm in a single day is forecast this week. Clear drainage and delay spraying and fertilizer.")
            if district["season"] == "kharif" and forecast["rain_past_30_days_mm"] < 30 and forecast["rain_next_14_days_mm"] < 10:
                alerts.append("Dry spell alert: very little monsoon rain in the last 30 days and none expected for two weeks. Conserve water and protect soil moisture.")
        else:
            if district["temperature"] >= 34:
                alerts.append("Heat alert: protect young plants, avoid afternoon spraying, and irrigate during cooler hours.")
            if district["rainfall_monthly_mm"] < 35:
                alerts.append("Dry spell alert: conserve water and improve soil moisture retention.")
            if district["rainfall_monthly_mm"] > 150:
                alerts.append("Heavy rainfall alert: monitor drainage and prevent root-zone waterlogging.")

        if district["humidity"] > 82:
            alerts.append("Disease alert: humidity is high, so fungal risk may increase.")
        if PEST_MAP[district["pest_risk"]] >= 1:
            alerts.append("Pest alert: inspect leaves and stems regularly for early infestation signs.")
        if district["market_price_index"] >= 84:
            alerts.append("Market alert (estimated index, not live mandi prices): conditions look favorable for near-term selling.")
        if not alerts:
            alerts.append("No major risk flag today. Continue normal crop monitoring.")

        return alerts

    def _decision_score(self, district, preferred_language, top_score):
        score = 48
        if preferred_language in district["languages"]:
            score += 18
        if district["market_price_index"] >= 76:
            score += 8
        if district["pest_risk"] == "low":
            score += 10
        elif district["pest_risk"] == "medium":
            score += 5
        if top_score >= 78:
            score += 10
        elif top_score >= 62:
            score += 6
        if self.model_accuracy >= 0.9:
            score += 5
        return min(score, 100)
