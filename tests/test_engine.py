import pytest

from conftest import KHARIF_DAY, RABI_DAY, FakeWeather
from engine import FarmSaarthiEngine, season_for


@pytest.mark.parametrize(
    "month, season",
    [(1, "rabi"), (2, "rabi"), (3, "zaid"), (5, "zaid"), (6, "kharif"), (10, "kharif"), (11, "rabi"), (12, "rabi")],
)
def test_season_follows_the_calendar(month, season):
    from datetime import date

    assert season_for(date(2026, month, 15)) == season


def test_training_data_combines_public_dataset_and_generated_crops(engine):
    assert engine.dataset_rows == 2200
    assert engine.generated_rows == 300
    assert len(engine.model.classes_) == 25
    assert {"wheat", "sugarcane", "millets", "rice", "chickpea"} <= set(engine.model.classes_)
    assert engine.model_accuracy > 0.9


def test_every_district_has_coordinates_or_a_state_fallback(engine):
    for item in engine.list_districts():
        lat, lon = engine._coordinates(item)
        assert lat is not None and lon is not None, item["district"]
        assert 6 <= lat <= 37.5 and 68 <= lon <= 97.5


def test_unknown_district_returns_an_error(engine):
    assert engine.recommend("Atlantis", "en", "", "Punjab")["error"]
    # A real district under the wrong state is also refused.
    assert engine.recommend("Ludhiana", "en", "", "Kerala")["error"]


def test_offline_weather_is_marked_as_estimate(engine):
    result = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)
    assert result["district"]["sources"] == {"weather": "estimate", "soil": "estimate", "market": "estimate"}
    assert result["district"]["forecast"] is None
    assert len(result["top_crops"]) == 3


def test_live_weather_feeds_the_model_and_advice():
    weather = FakeWeather(rain_next_7_days_mm=0.2)
    live_engine = FarmSaarthiEngine(weather_client=weather)
    result = live_engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)

    district = result["district"]
    assert district["sources"]["weather"] == "live"
    assert district["temperature"] == 18.0
    assert district["rainfall_monthly_mm"] == 22
    assert district["season"] == "rabi"
    assert weather.calls and weather.calls[0][2] == RABI_DAY
    assert result["irrigation_advice"].startswith("No rain is forecast")
    # In the rabi season the top suggestions should be crops that are actually sown now.
    assert all(crop["in_season"] for crop in result["top_crops"])


def test_heavy_rain_and_heat_alerts_use_the_forecast():
    weather = FakeWeather(max_daily_rain_next_7_days_mm=90.0, max_temperature_next_7_days=41.0)
    alerts = FarmSaarthiEngine(weather_client=weather).recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)["alerts"]
    assert any(alert.startswith("Heavy rainfall alert") for alert in alerts)
    assert any(alert.startswith("Heat alert") for alert in alerts)


def test_dry_spell_alert_only_in_kharif():
    dry = dict(rain_past_30_days_mm=5.0, rain_next_14_days_mm=1.0)
    kharif = FarmSaarthiEngine(weather_client=FakeWeather(**dry)).recommend("Jaipur", "en", "", "Rajasthan", today=KHARIF_DAY)
    rabi = FarmSaarthiEngine(weather_client=FakeWeather(**dry)).recommend("Jaipur", "en", "", "Rajasthan", today=RABI_DAY)
    assert any(a.startswith("Dry spell alert") for a in kharif["alerts"])
    assert not any(a.startswith("Dry spell alert") for a in rabi["alerts"])


@pytest.mark.parametrize(
    "interest, crop",
    [
        ("cotton, rice, maize", "cotton"),
        ("cotton, rice, maize", "rice"),
        ("Wheat", "wheat"),
        ("गेहूं", "wheat"),
        ("bajra", "millets"),
        ("green gram", "mungbean"),
        ("arhar dal", "pigeonpeas"),
    ],
)
def test_crop_interest_matches_names_and_local_aliases(engine, interest, crop):
    assert engine._matches_interest(crop, engine._interest_terms(interest))


def test_crop_interest_does_not_match_partial_words(engine):
    assert not engine._matches_interest("rice", engine._interest_terms("licorice"))
    assert not engine._matches_interest("rice", engine._interest_terms(""))


def test_stated_interest_raises_the_score(engine):
    plain = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)
    with_interest = engine.recommend("Ludhiana", "en", "wheat", "Punjab", today=RABI_DAY)
    score = lambda result: next((c["score"] for c in result["top_crops"] if c["crop_id"] == "wheat"), 0)
    assert score(with_interest) > score(plain)


def test_fertilizer_advice_says_soil_values_are_estimates(engine):
    advice = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)["fertilizer_advice"]
    assert "Soil Health Card" in advice
