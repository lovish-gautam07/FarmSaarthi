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


# ----- growing regions and the farmer's own crop ----------------------------------

def test_regional_crops_stay_in_their_states(engine):
    bengal = engine.recommend("Purba Bardhaman", "en", "", "West Bengal", today=RABI_DAY)
    assert all(crop["in_region"] for crop in bengal["top_crops"])
    assert "coffee" not in {crop["crop_id"] for crop in bengal["top_crops"]}
    assert engine._checks(bengal["district"], "coffee")["region"] is False

    kerala = engine.recommend("Wayanad", "en", "", "Kerala", today=RABI_DAY)
    assert engine._checks(kerala["district"], "coffee")["region"] is True


def test_asked_for_crop_out_of_season_gets_a_note(engine):
    result = engine.recommend("Nagpur", "en", "cotton", "Maharashtra", today=RABI_DAY)
    assert "cotton" not in {crop["crop_id"] for crop in result["top_crops"]}
    note = next(n for n in result["interest_notes"] if n["crop_id"] == "cotton")
    assert "kharif (June–October)" in note["text"]


def test_asked_for_crop_outside_its_region_gets_a_note(engine):
    result = engine.recommend("Purba Bardhaman", "en", "coffee", "West Bengal", today=RABI_DAY)
    note = next(n for n in result["interest_notes"] if n["crop_id"] == "coffee")
    assert "not commonly grown in West Bengal" in note["text"]


def test_no_interest_notes_when_nothing_was_asked(engine):
    assert engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)["interest_notes"] == []


# ----- action cards ------------------------------------------------------------------

def actions_for(weather, today=RABI_DAY):
    return FarmSaarthiEngine(weather_client=weather).recommend("Ludhiana", "en", "", "Punjab", today=today)["actions"]


def test_cards_are_sorted_by_urgency_and_limited():
    order = {"alert": 0, "warn": 1, "good": 2, "info": 3}
    days = [{"date": f"2026-11-{19 + i}", "rain_mm": 80.0 if i == 2 else 0.0, "max_temp": 40.0} for i in range(7)]
    cards = actions_for(FakeWeather(next_days=days, max_temperature_next_7_days=40.0, humidity=88.0, rain_next_7_days_mm=80.0))
    assert [order[c["level"]] for c in cards] == sorted(order[c["level"]] for c in cards)
    assert len(cards) <= 6
    titles = [c["title"] for c in cards]
    assert any(t.startswith("Heavy rain on Saturday") for t in titles)  # 21 Nov 2026 is a Saturday
    assert any(t.startswith("Heat: up to 40") for t in titles)


def test_rain_tomorrow_means_do_not_spray():
    days = [{"date": f"2026-11-{19 + i}", "rain_mm": 12.0 if i == 1 else 0.0, "max_temp": 25.0} for i in range(7)]
    cards = actions_for(FakeWeather(next_days=days, rain_next_7_days_mm=12.0))
    spray = next(c for c in cards if c["icon"] == "no-spray")
    assert "tomorrow" in spray["text"]


def test_irrigation_card_follows_the_forecast():
    dry = actions_for(FakeWeather(rain_next_7_days_mm=0.0))
    wet = actions_for(FakeWeather(rain_next_7_days_mm=70.0))
    assert any(c["title"] == "Irrigate this week" and c["level"] == "warn" for c in dry)
    assert any(c["title"] == "Skip irrigation this week" for c in wet)


def test_best_crops_card_names_in_season_crops(engine):
    result = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)
    card = next(c for c in result["actions"] if c["icon"] == "seed")
    assert card["title"].startswith("Best crops now:")
    for crop in result["top_crops"]:
        if crop["in_season"] and crop["in_region"]:
            assert crop["crop"] in card["title"]


def test_offline_cards_are_marked_as_estimates(engine):
    cards = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)["actions"]
    weather_cards = [c for c in cards if c["icon"] in {"water", "rain", "heat"}]
    assert weather_cards and all(c["estimate"] for c in weather_cards)


# ----- Soil Health Card -----------------------------------------------------------

def test_soil_input_validation():
    from engine import clean_soil_input

    assert clean_soil_input(None) == (None, None)
    assert clean_soil_input({"n": "", "type": ""}) == (None, None)
    soil, problem = clean_soil_input({"type": "loamy", "n": "300", "ph": 6.8})
    assert problem is None and soil == {"type": "loamy", "n": 300.0, "ph": 6.8}
    assert clean_soil_input({"n": "lots"})[1]
    assert clean_soil_input({"ph": 2})[1]
    assert clean_soil_input({"type": "rocky"})[1]
    assert clean_soil_input(["n", 1])[1]


def test_card_values_replace_estimates(engine):
    plain = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)
    card = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY,
                            soil={"n": 600, "p": 5, "k": 300, "ph": 5.2, "oc": 0.9})
    district = card["district"]
    assert plain["district"]["sources"]["soil"] == "estimate"
    assert district["sources"]["soil"] == "card"
    assert district["nitrogen"] == round(engine.nutrient_scale["N"]["high"])
    assert district["phosphorus"] == round(engine.nutrient_scale["P"]["low"])
    assert district["ph"] == 5.2
    tips = card["fertilizer_advice"]
    assert "Nitrogen is high (600 kg/ha)" in tips and "Phosphorus is low (5 kg/ha)" in tips
    assert "acidic (pH 5.2)" in tips
    soil_card = next(a for a in card["actions"] if a["icon"] == "soil")
    assert soil_card["estimate"] is False


def test_soil_type_alone_is_the_farmers_choice(engine):
    result = engine.recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY, soil={"type": "clay"})
    assert result["district"]["soil"] == "clay"
    assert result["district"]["sources"]["soil"] == "farmer"
    assert result["district"]["soil_card"] is None


# ----- India date and the 7-day strip ------------------------------------------------

def test_india_today_is_ahead_of_utc_after_6_30_pm():
    from datetime import date, datetime, timezone

    from engine import india_today

    assert india_today(datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)) == date(2026, 11, 20)
    assert india_today(datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)) == date(2026, 11, 19)


def test_day_flags_follow_the_spray_rule_without_changing_the_cache():
    from engine import with_day_flags

    rows = [{"date": f"2026-11-{20 + i}", "rain_mm": rain, "max_temp": 30.0} for i, rain in enumerate([0, 0, 0, 6, 0, 0, 80])]
    cached = {"max_temperature_next_7_days": 30.0, "next_days": rows}
    flagged = with_day_flags(cached)
    assert [d["spray"] for d in flagged["next_days"]] == ["ok", "no", "no", "no", "no", None, None]
    assert flagged["next_days"][6]["heavy_rain"] is True
    assert "spray" not in rows[0]  # the shared cached forecast is untouched

    hot = with_day_flags({**cached, "max_temperature_next_7_days": 40.0})
    assert hot["next_days"][0]["spray"] == "early"


def test_analysis_carries_flagged_days():
    result = FarmSaarthiEngine(weather_client=FakeWeather()).recommend("Ludhiana", "en", "", "Punjab", today=RABI_DAY)
    days = result["district"]["forecast"]["next_days"]
    assert len(days) == 7 and all("spray" in d and "heavy_rain" in d for d in days)
