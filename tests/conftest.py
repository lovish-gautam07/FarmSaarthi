import io
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Tests must never pick up a developer's real keys from .env.
for name in ("AZURE_STORAGE_CONNECTION_STRING", "AZURE_TRANSLATOR_KEY", "AZURE_SPEECH_KEY", "SECRET_KEY",
             "DATA_GOV_IN_API_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT", "TASK_KEY"):
    os.environ[name] = ""

from alerts import PushSender  # noqa: E402
from datagov import KCC_RESOURCE, MANDI_RESOURCE, DataGovClient  # noqa: E402
from engine import FarmSaarthiEngine  # noqa: E402
from events import new_row_key, summarize  # noqa: E402
from speech import AzureSpeech  # noqa: E402
from translations import AzureTranslator, I18n  # noqa: E402

RABI_DAY = date(2026, 11, 19)
KHARIF_DAY = date(2026, 7, 15)


class FakeWeather:
    """Stands in for Open-Meteo so tests never touch the network."""

    def __init__(self, **overrides):
        self.calls = []
        self.summary = {
            "temperature": 18.0,
            "humidity": 58.0,
            "rainfall_monthly_mm": 22.0,
            "rain_past_30_days_mm": 4.0,
            "rain_next_7_days_mm": 3.0,
            "rain_next_14_days_mm": 6.0,
            "max_daily_rain_next_7_days_mm": 2.0,
            "max_temperature_next_7_days": 26.0,
            "next_days": [
                {"date": (RABI_DAY.replace(day=19 + offset)).isoformat(), "rain_mm": 0.0, "max_temp": 26.0}
                for offset in range(7)
            ],
            "fetched_at": "2026-11-19T06:00:00+00:00",
        }
        self.summary.update(overrides)

    def get(self, lat, lon, today=None):
        self.calls.append((lat, lon, today))
        return dict(self.summary)


class OfflineWeather:
    def get(self, lat, lon, today=None):
        return None


class FakeTranslator(AzureTranslator):
    """Pretends to translate by tagging each string with the language code."""

    def __init__(self, fail=False):
        super().__init__(key="test-key", region="centralindia")
        self.fail = fail
        self.requests = []

    def _call(self, chunk, to_lang, from_lang):
        self.requests.append((to_lang, list(chunk)))
        if self.fail:
            return None
        return [f"[{to_lang}] {text}" for text in chunk]


class FakePhotoStore:
    def __init__(self):
        self.saved = []

    def save(self, processed, details):
        record = {
            "id": f"photo{len(self.saved) + 1}",
            **details,
            "uploaded_at": "2026-11-19T06:00:00+00:00",
            "width": processed["width"],
            "height": processed["height"],
            "url": "https://example.invalid/full.jpg?sig=x",
            "thumb_url": "https://example.invalid/thumb.jpg?sig=x",
        }
        self.saved.append(record)
        return record

    def list_recent(self, limit=12, state=None, district=None):
        rows = [r for r in reversed(self.saved) if (not state or r["state"] == state) and (not district or r["district"] == district)]
        return rows[:limit]


class FakeEvents:
    """In-memory stand-in for the Azure Table event log."""

    def __init__(self):
        self.rows = {}

    def record(self, kind, data, row_key=None):
        row_key = row_key or new_row_key()
        existing = self.rows.get((kind, row_key), {})
        self.rows[(kind, row_key)] = {**existing, **data, "PartitionKey": kind, "RowKey": row_key,
                                      "recorded_at": "2026-11-19T06:00:00+00:00"}
        return row_key

    def of_kind(self, kind):
        return [row for (k, _), row in self.rows.items() if k == kind]

    def summary(self):
        return summarize(self.of_kind("analysis"), self.of_kind("feedback"))


class FakeSpeech(AzureSpeech):
    """Azure Speech that returns fake MP3 bytes without any network call."""

    def __init__(self, enabled=True):
        super().__init__(key="test-key" if enabled else "", region="centralindia")
        self.spoken = []

    def synthesize(self, text, locale):
        self.spoken.append((text, locale))
        return b"ID3-fake-mp3"


MANDI_ROWS = {
    ("Punjab", "Wheat"): [
        {"state": "Punjab", "district": "Patiala", "market": "Rajpura APMC", "commodity": "Wheat", "modal_price": "2500", "min_price": "2400", "max_price": "2550", "arrival_date": "19/11/2026"},
        {"state": "Punjab", "district": "Ludhiana", "market": "Khanna APMC", "commodity": "Wheat", "modal_price": "2425", "min_price": "2400", "max_price": "2450", "arrival_date": "19/11/2026"},
        {"state": "Punjab", "district": "Amritsar", "market": "Amritsar APMC", "commodity": "Wheat", "modal_price": "2450", "min_price": "2400", "max_price": "2500", "arrival_date": "19/11/2026"},
    ],
}

KCC_ROWS = [
    {"StateName": "PUNJAB", "DistrictName": "LUDHIANA", "Crop": "Wheat", "QueryType": "Plant Protection",
     "QueryText": "How to control yellow rust in wheat?", "KccAns": "ਪੀਲੀ ਕੁੰਗੀ ਦੀ ਰੋਕਥਾਮ ਲਈ ਮਾਹਿਰ ਦੀ ਸਲਾਹ ਲਵੋ", "CreatedOn": "2026-01-10T10:00:00"},
    {"StateName": "PUNJAB", "DistrictName": "PATIALA", "Crop": "Wheat", "QueryType": "Weather",
     "QueryText": "Farmer asked query on Weather", "KccAns": "Light rain expected", "CreatedOn": "2026-01-11T10:00:00"},
    {"StateName": "PUNJAB", "DistrictName": "MOGA", "Crop": "Wheat", "QueryType": "Nutrient Management",
     "QueryText": "Information regarding urea dose for wheat", "KccAns": "Apply urea in two splits", "CreatedOn": "2026-01-09T10:00:00"},
]


class FakeDataGov(DataGovClient):
    """data.gov.in stand-in. Set `down = True` to simulate the API failing."""

    def __init__(self):
        super().__init__(api_key="test-key")
        self.calls = []
        self.down = False

    def records(self, resource, filters, limit, sort=None, cache_seconds=0):
        self.calls.append((resource, dict(filters)))
        if self.down:
            return None
        if resource == MANDI_RESOURCE:
            return MANDI_ROWS.get((filters.get("state.keyword"), filters.get("commodity.keyword")), [])
        if resource == KCC_RESOURCE:
            rows = [row for row in KCC_ROWS if row["StateName"] == filters.get("StateName")]
            if "Crop" in filters:
                rows = [row for row in rows if row["Crop"] == filters["Crop"]]
            return rows
        return []


class FakeAlertStore:
    def __init__(self):
        self.subs = {}

    def subscribe(self, subscription, state, district, language):
        from alerts import endpoint_key

        key = endpoint_key(subscription["endpoint"])
        self.subs[key] = {"RowKey": key, "endpoint": subscription["endpoint"], "p256dh": subscription["keys"]["p256dh"],
                          "auth": subscription["keys"]["auth"], "state": state, "district": district,
                          "language": language, "last_alert_key": ""}
        return key

    def unsubscribe(self, endpoint):
        from alerts import endpoint_key

        self.subs.pop(endpoint_key(endpoint), None)

    def delete(self, row_key):
        self.subs.pop(row_key, None)

    def all(self):
        return [dict(sub) for sub in self.subs.values()]

    def mark_sent(self, row_key, alert_key):
        self.subs[row_key]["last_alert_key"] = alert_key


class FakePush(PushSender):
    def __init__(self, result="ok"):
        super().__init__("BPublicKeyForTests", "private", "https://farmsaarthi.example")
        self.result = result
        self.sent = []

    def send(self, subscription, payload):
        self.sent.append((subscription["endpoint"], payload))
        return self.result


BROWSER_SUBSCRIPTION = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-123",
    "keys": {"p256dh": "B" + "x" * 86, "auth": "a" * 22},
}


@pytest.fixture(scope="session")
def engine():
    """One trained engine for the whole run (training takes well under a second)."""
    return FarmSaarthiEngine(weather_client=OfflineWeather())


@pytest.fixture
def make_image():
    def _make(size=(1200, 900), fmt="JPEG", mode="RGB", exif=None):
        image = Image.new(mode, size, (40, 140, 60, 255)[: len(mode)] if mode != "P" else 0)
        buffer = io.BytesIO()
        kwargs = {"exif": exif} if exif is not None else {}
        image.save(buffer, fmt, **kwargs)
        return buffer.getvalue()

    return _make


@pytest.fixture
def i18n_offline():
    return I18n(AzureTranslator())  # no key: English fallback


@pytest.fixture
def client(engine, i18n_offline):
    from app import create_app

    store, events, speech = FakePhotoStore(), FakeEvents(), FakeSpeech()
    datagov, alert_store, push = FakeDataGov(), FakeAlertStore(), FakePush()
    app = create_app(engine=engine, i18n=i18n_offline, photo_store=store, speech=speech, events=events,
                     secret_key="test-secret", inline_background=True, datagov=datagov,
                     alert_store=alert_store, push=push, task_key="task-secret")
    app.config["TESTING"] = True
    test_client = app.test_client()
    test_client.store, test_client.events, test_client.speech = store, events, speech
    test_client.datagov, test_client.alert_store, test_client.push = datagov, alert_store, push
    return test_client
