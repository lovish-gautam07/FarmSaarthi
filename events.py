"""Usage and feedback records in Azure Table Storage, plus the numbers for /stats.

Two kinds of rows, both without personal data:
* "analysis": state, district, language, top crop, weather source
* "feedback": useful yes/no, optional comment, the same context
"""

import os
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

MAX_TIMESTAMP_MS = 10 ** 13
SUMMARY_CACHE_SECONDS = 60


def new_row_key(now=None):
    now = now or datetime.now(timezone.utc)
    return f"{MAX_TIMESTAMP_MS - int(now.timestamp() * 1000):013d}_{uuid.uuid4().hex}"


class EventLog:
    def __init__(self, table_client):
        self.table = table_client
        self._ready = False
        self._lock = threading.Lock()
        self._summary = None

    @classmethod
    def from_env(cls):
        connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        if not connection_string:
            return None
        from azure.data.tables import TableServiceClient

        service = TableServiceClient.from_connection_string(connection_string)
        return cls(service.get_table_client(os.environ.get("AZURE_STORAGE_EVENTS_TABLE", "AppEvents")))

    def _ensure(self):
        if self._ready:
            return
        from azure.core.exceptions import ResourceExistsError

        with self._lock:
            if not self._ready:
                try:
                    self.table.create_table()
                except ResourceExistsError:
                    pass
                self._ready = True

    def record(self, kind, data, row_key=None):
        """Write one row and return its id (the RowKey)."""
        from azure.data.tables import UpdateMode

        self._ensure()
        now = datetime.now(timezone.utc)
        entity = {"PartitionKey": kind, "RowKey": row_key or new_row_key(now), "recorded_at": now.isoformat(timespec="seconds"), **data}
        self.table.upsert_entity(entity, mode=UpdateMode.MERGE)
        self._summary = None
        return entity["RowKey"]

    def rows(self, kind):
        self._ensure()
        return list(self.table.query_entities("PartitionKey eq @kind", parameters={"kind": kind}))

    def summary(self):
        cached = self._summary
        if cached and time.monotonic() - cached[0] < SUMMARY_CACHE_SECONDS:
            return cached[1]
        result = summarize(self.rows("analysis"), self.rows("feedback"))
        self._summary = (time.monotonic(), result)
        return result


def summarize(analyses, feedback, today=None):
    today = today or datetime.now(timezone.utc).date()
    days = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
    per_day = Counter(row.get("recorded_at", "")[:10] for row in analyses)

    useful = [row for row in feedback if row.get("useful") is True]
    by_language = {}
    for row in feedback:
        entry = by_language.setdefault(row.get("language", "?"), {"answers": 0, "useful": 0})
        entry["answers"] += 1
        entry["useful"] += 1 if row.get("useful") is True else 0

    return {
        "analyses": len(analyses),
        "feedback": len(feedback),
        "useful": len(useful),
        "useful_percent": round(100 * len(useful) / len(feedback)) if feedback else None,
        "comments": sum(1 for row in feedback if row.get("comment")),
        "live_weather_percent": round(100 * sum(1 for r in analyses if r.get("weather") == "live") / len(analyses)) if analyses else None,
        "languages": Counter(row.get("language", "?") for row in analyses).most_common(),
        "states": Counter(row.get("state", "?") for row in analyses).most_common(10),
        "top_crops": Counter(row.get("top_crop", "?") for row in analyses).most_common(10),
        "feedback_by_language": sorted(by_language.items(), key=lambda item: -item[1]["answers"]),
        "per_day": [{"date": day.isoformat(), "analyses": per_day.get(day.isoformat(), 0)} for day in days],
    }
