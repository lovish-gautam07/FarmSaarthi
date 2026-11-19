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
for name in ("AZURE_STORAGE_CONNECTION_STRING", "AZURE_TRANSLATOR_KEY"):
    os.environ.pop(name, None)
os.environ["AZURE_STORAGE_CONNECTION_STRING"] = ""
os.environ["AZURE_TRANSLATOR_KEY"] = ""

from engine import FarmSaarthiEngine  # noqa: E402
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

    store = FakePhotoStore()
    app = create_app(engine=engine, i18n=i18n_offline, photo_store=store)
    app.config["TESTING"] = True
    test_client = app.test_client()
    test_client.store = store
    return test_client
