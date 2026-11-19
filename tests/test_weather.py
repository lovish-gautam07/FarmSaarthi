from datetime import date, timedelta

import requests

from weather import WeatherClient, summarize

TODAY = date(2026, 11, 19)


def fake_daily(past_rain=2.0, future_rain=5.0, temp=20.0, humidity=60.0):
    days = [TODAY + timedelta(days=offset) for offset in range(-92, 16)]
    return {
        "time": [d.isoformat() for d in days],
        "temperature_2m_mean": [temp] * len(days),
        "temperature_2m_max": [temp + 8] * len(days),
        "relative_humidity_2m_mean": [humidity] * len(days),
        "precipitation_sum": [past_rain if d < TODAY else future_rain for d in days],
    }


def test_summary_numbers():
    summary = summarize(fake_daily(), TODAY)
    assert summary["temperature"] == 20.0
    assert summary["humidity"] == 60.0
    assert summary["rain_past_30_days_mm"] == 60.0          # 30 days x 2 mm
    assert summary["rain_next_7_days_mm"] == 35.0           # 7 days x 5 mm
    assert summary["rain_next_14_days_mm"] == 70.0
    assert summary["max_daily_rain_next_7_days_mm"] == 5.0
    assert summary["max_temperature_next_7_days"] == 28.0
    # 92 days x 2 mm spread over ~3 months = about 60.8 mm a month
    assert 60 <= summary["rainfall_monthly_mm"] <= 61


def test_missing_values_are_skipped():
    daily = fake_daily()
    daily["precipitation_sum"][-1] = None
    daily["temperature_2m_mean"][0] = None
    assert summarize(daily, TODAY)["temperature"] == 20.0


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class CountingSession:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return FakeResponse(self.payload)


def test_client_caches_per_place():
    session = CountingSession({"daily": fake_daily()})
    client = WeatherClient(session=session)
    first = client.get(30.9, 75.85, TODAY)
    second = client.get(30.9001, 75.8501, TODAY)  # same place after rounding
    assert first == second
    assert session.calls == 1


def test_client_returns_none_when_offline():
    client = WeatherClient(session=CountingSession(error=requests.ConnectionError("offline")))
    assert client.get(30.9, 75.85, TODAY) is None


def test_client_returns_none_on_bad_payload():
    client = WeatherClient(session=CountingSession({"unexpected": True}))
    assert client.get(30.9, 75.85, TODAY) is None


def test_summary_lists_the_next_seven_days():
    days = summarize(fake_daily(future_rain=4.0), TODAY)["next_days"]
    assert len(days) == 7
    assert days[0]["date"] == TODAY.isoformat()
    assert all(day["rain_mm"] == 4.0 for day in days)
