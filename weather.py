"""Live weather for a district from the free Open-Meteo API (no key needed).

One request returns the last 92 days and the next 16 days of daily weather,
which gives both the recent-season conditions the crop model needs and the
short-term forecast used for alerts and irrigation advice.
"""

import threading
import time
from datetime import date, datetime, timezone

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_FIELDS = "temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean,precipitation_sum"
CACHE_SECONDS = 3 * 60 * 60  # weather for a place is reused for 3 hours
REQUEST_TIMEOUT = 6


class WeatherClient:
    def __init__(self, session=None, cache_seconds=CACHE_SECONDS):
        self.session = session or requests.Session()
        self.cache_seconds = cache_seconds
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, lat, lon, today=None):
        """Return a weather summary dict, or None if the API cannot be reached."""
        key = (round(lat, 2), round(lon, 2))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.cache_seconds:
                return cached[1]

        try:
            response = self.session.get(
                FORECAST_URL,
                params={
                    "latitude": key[0],
                    "longitude": key[1],
                    "daily": DAILY_FIELDS,
                    "past_days": 92,
                    "forecast_days": 16,
                    "timezone": "Asia/Kolkata",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            summary = summarize(response.json()["daily"], today or date.today())
        except (requests.RequestException, KeyError, ValueError, TypeError):
            return None

        with self._lock:
            self._cache[key] = (now, summary)
        return summary


def _values(daily, field, days):
    return [daily[field][i] for i in days if daily[field][i] is not None]


def _mean(values):
    return sum(values) / len(values) if values else None


def summarize(daily, today):
    """Turn Open-Meteo daily arrays into the numbers FarmSaarthi uses."""
    dates = [date.fromisoformat(item) for item in daily["time"]]
    past = [i for i, d in enumerate(dates) if d < today]
    past_30 = [i for i, d in enumerate(dates) if 0 < (today - d).days <= 30]
    next_7 = [i for i, d in enumerate(dates) if 0 <= (d - today).days < 7]
    next_14 = [i for i, d in enumerate(dates) if 0 <= (d - today).days < 14]
    season_window = past_30 + [i for i, d in enumerate(dates) if 0 <= (d - today).days < 16]

    if not past or not next_7:
        raise ValueError("Weather response does not cover the needed dates")

    rain_past = _values(daily, "precipitation_sum", past)
    rain_next_7 = _values(daily, "precipitation_sum", next_7)
    temperature = _mean(_values(daily, "temperature_2m_mean", season_window))
    humidity = _mean(_values(daily, "relative_humidity_2m_mean", season_window))
    if temperature is None or humidity is None:
        raise ValueError("Weather response is missing temperature or humidity")

    return {
        # Average of the last 30 days and the next 16: the conditions a crop sown now would meet.
        "temperature": round(temperature, 1),
        "humidity": round(humidity, 1),
        # Average monthly rainfall over the last ~3 months, the same scale as the training data.
        "rainfall_monthly_mm": round(sum(rain_past) / (len(rain_past) / 30.4), 1),
        "rain_past_30_days_mm": round(sum(_values(daily, "precipitation_sum", past_30)), 1),
        "rain_next_7_days_mm": round(sum(rain_next_7), 1),
        "rain_next_14_days_mm": round(sum(_values(daily, "precipitation_sum", next_14)), 1),
        "max_daily_rain_next_7_days_mm": round(max(rain_next_7, default=0.0), 1),
        "max_temperature_next_7_days": round(max(_values(daily, "temperature_2m_max", next_7), default=temperature), 1),
        "next_days": [
            {
                "date": daily["time"][i],
                "rain_mm": round(daily["precipitation_sum"][i] or 0.0, 1),
                "max_temp": daily["temperature_2m_max"][i],
            }
            for i in next_7
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
