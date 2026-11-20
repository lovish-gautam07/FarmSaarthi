from datetime import date

from events import new_row_key, summarize

TODAY = date(2026, 11, 19)


def row(day, **data):
    return {"recorded_at": f"{day}T10:00:00+00:00", **data}


def test_summary_numbers():
    analyses = [
        row("2026-11-19", language="hi", state="Punjab", top_crop="wheat", weather="live"),
        row("2026-11-19", language="hi", state="Punjab", top_crop="wheat", weather="live"),
        row("2026-11-18", language="ta", state="Tamil Nadu", top_crop="rice", weather="estimate"),
        row("2026-10-01", language="en", state="Kerala", top_crop="banana", weather="live"),  # older than 14 days
    ]
    feedback = [
        row("2026-11-19", useful=True, language="hi"),
        row("2026-11-19", useful=False, language="hi", comment="too long"),
        row("2026-11-19", useful=True, language="ta"),
    ]
    s = summarize(analyses, feedback, today=TODAY)
    assert s["analyses"] == 4 and s["feedback"] == 3
    assert s["useful"] == 2 and s["useful_percent"] == 67
    assert s["comments"] == 1
    assert s["live_weather_percent"] == 75
    assert s["languages"][0] == ("hi", 2)
    assert dict(s["feedback_by_language"])["hi"] == {"answers": 2, "useful": 1}
    assert len(s["per_day"]) == 14
    assert s["per_day"][-1] == {"date": "2026-11-19", "analyses": 2}
    assert s["per_day"][-2] == {"date": "2026-11-18", "analyses": 1}


def test_empty_summary():
    s = summarize([], [], today=TODAY)
    assert s["useful_percent"] is None and s["live_weather_percent"] is None
    assert all(day["analyses"] == 0 for day in s["per_day"])


def test_row_keys_sort_newest_first():
    from datetime import datetime, timezone

    older = new_row_key(datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = new_row_key(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert newer < older
