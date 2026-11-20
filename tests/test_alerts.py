from conftest import BROWSER_SUBSCRIPTION, FakeAlertStore, FakePush, FakeTranslator, FakeWeather, RABI_DAY
from alerts import make_vapid_keys, run_alerts, valid_subscription
from engine import FarmSaarthiEngine

HEAVY_RAIN = [{"date": f"2026-11-{19 + i}", "rain_mm": 90.0 if i == 1 else 0.0, "max_temp": 25.0} for i in range(7)]


def subscribed(language="en", district="Ludhiana", endpoint=None):
    store = FakeAlertStore()
    sub = dict(BROWSER_SUBSCRIPTION, endpoint=endpoint or BROWSER_SUBSCRIPTION["endpoint"])
    store.subscribe(sub, "Punjab", district, language)
    return store


def test_valid_subscription_shape():
    assert valid_subscription(BROWSER_SUBSCRIPTION)
    assert not valid_subscription({"endpoint": "http://plain.example", "keys": BROWSER_SUBSCRIPTION["keys"]})
    assert not valid_subscription({"endpoint": BROWSER_SUBSCRIPTION["endpoint"], "keys": {}})
    assert not valid_subscription("nope")


def test_urgent_weather_sends_once_per_alert():
    engine = FarmSaarthiEngine(weather_client=FakeWeather(next_days=HEAVY_RAIN, max_daily_rain_next_7_days_mm=90.0, rain_next_7_days_mm=90.0))
    store, push = subscribed(), FakePush()

    first = run_alerts(engine, None, store, push, today=RABI_DAY)
    assert first["sent"] == 1 and first["with_alerts"] == 1
    endpoint, payload = push.sent[0]
    assert payload["title"] == "Ludhiana: Heavy rain tomorrow"
    assert payload["url"] == "/?language=en&state=Punjab&district=Ludhiana"

    again = run_alerts(engine, None, store, push, today=RABI_DAY)
    assert again["sent"] == 0 and again["skipped_repeat"] == 1


def test_calm_weather_sends_nothing():
    engine = FarmSaarthiEngine(weather_client=FakeWeather())
    push = FakePush()
    assert run_alerts(engine, None, subscribed(), push, today=RABI_DAY)["sent"] == 0
    assert push.sent == []


def test_estimated_weather_never_triggers_alerts(engine):
    push = FakePush()
    run_alerts(engine, None, subscribed(), push, today=RABI_DAY)  # session engine has no live weather
    assert push.sent == []


def test_alert_is_translated_and_dead_subscriptions_removed():
    engine = FarmSaarthiEngine(weather_client=FakeWeather(next_days=HEAVY_RAIN, max_daily_rain_next_7_days_mm=90.0))
    store, push = subscribed(language="pa"), FakePush()
    run_alerts(engine, FakeTranslator(), store, push, today=RABI_DAY)
    assert push.sent[0][1]["title"] == "Ludhiana: [pa] Heavy rain tomorrow"

    gone = FakePush(result="gone")
    store = subscribed(endpoint="https://fcm.googleapis.com/fcm/send/another-endpoint")
    summary = run_alerts(engine, None, store, gone, today=RABI_DAY)
    assert summary["removed"] == 1 and store.all() == []


def test_vapid_key_format():
    public, private = make_vapid_keys()
    assert len(public) == 87 and public.startswith("B")  # 65-byte uncompressed P-256 point
    assert len(private) == 43
