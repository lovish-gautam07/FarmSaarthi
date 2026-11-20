"""Free weather alerts as browser notifications (Web Push).

* A farmer taps "Get weather alerts" for a district; the browser hands us a
  push address, which is kept in Azure Table Storage.
* Once a day a free GitHub Actions schedule calls /tasks/send-alerts. For each
  subscribed district the forecast is checked, and a notification goes out only
  when there is an urgent card (heavy rain, heat, dry spell, rain before spraying).
* The same alert is never sent twice; dead push addresses are removed.
"""

import base64
import hashlib
import json
import os
import threading
from collections import defaultdict
from datetime import date, datetime, timezone
from urllib.parse import urlencode

PARTITION = "sub"
PUSH_TTL_SECONDS = 12 * 60 * 60


def endpoint_key(endpoint):
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:40]


def valid_subscription(subscription):
    """Check the shape of a browser PushSubscription.toJSON() object."""
    if not isinstance(subscription, dict):
        return False
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    return (
        isinstance(endpoint, str) and endpoint.startswith("https://") and len(endpoint) <= 1000
        and isinstance(keys.get("p256dh"), str) and 40 <= len(keys["p256dh"]) <= 200
        and isinstance(keys.get("auth"), str) and 10 <= len(keys["auth"]) <= 100
    )


class AlertStore:
    def __init__(self, table_client):
        self.table = table_client
        self._ready = False
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        if not connection_string:
            return None
        from azure.data.tables import TableServiceClient

        service = TableServiceClient.from_connection_string(connection_string)
        return cls(service.get_table_client(os.environ.get("AZURE_STORAGE_ALERTS_TABLE", "AlertSubscriptions")))

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

    def subscribe(self, subscription, state, district, language):
        self._ensure()
        entity = {
            "PartitionKey": PARTITION,
            "RowKey": endpoint_key(subscription["endpoint"]),
            "endpoint": subscription["endpoint"],
            "p256dh": subscription["keys"]["p256dh"],
            "auth": subscription["keys"]["auth"],
            "state": state,
            "district": district,
            "language": language,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_alert_key": "",
        }
        self.table.upsert_entity(entity)
        return entity["RowKey"]

    def unsubscribe(self, endpoint):
        self.delete(endpoint_key(endpoint))

    def delete(self, row_key):
        from azure.core.exceptions import ResourceNotFoundError

        self._ensure()
        try:
            self.table.delete_entity(PARTITION, row_key)
        except ResourceNotFoundError:
            pass

    def all(self):
        self._ensure()
        return list(self.table.query_entities("PartitionKey eq @pk", parameters={"pk": PARTITION}))

    def mark_sent(self, row_key, alert_key):
        from azure.data.tables import UpdateMode

        self._ensure()
        self.table.update_entity(
            {"PartitionKey": PARTITION, "RowKey": row_key, "last_alert_key": alert_key,
             "last_sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            mode=UpdateMode.MERGE,
        )


class PushSender:
    def __init__(self, public_key=None, private_key=None, subject=None):
        self.public_key = (public_key or "").strip()
        self.private_key = (private_key or "").strip()
        self.subject = (subject or "").strip()

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("VAPID_PUBLIC_KEY"), os.environ.get("VAPID_PRIVATE_KEY"), os.environ.get("VAPID_SUBJECT"))

    @property
    def enabled(self):
        return bool(self.public_key and self.private_key and self.subject)

    def send(self, subscription, payload):
        """Returns "ok", "gone" (the browser unsubscribed) or "error"."""
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info={"endpoint": subscription["endpoint"],
                                   "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                ttl=PUSH_TTL_SECONDS,
                timeout=10,
            )
            return "ok"
        except WebPushException as problem:
            status = getattr(problem.response, "status_code", None)
            return "gone" if status in (404, 410) else "error"
        except Exception:
            return "error"


def urgent_actions(result):
    return [action for action in result["actions"] if action["level"] == "alert" or action["icon"] == "no-spray"]


def run_alerts(engine, translator, store, sender, today=None):
    """Check every subscribed district and send any new urgent alerts."""
    today = today or date.today()
    subscriptions = store.all()
    by_district = defaultdict(list)
    for subscription in subscriptions:
        by_district[(subscription["state"], subscription["district"])].append(subscription)

    summary = {"subscriptions": len(subscriptions), "districts": len(by_district), "with_alerts": 0,
               "sent": 0, "skipped_repeat": 0, "removed": 0, "failed": 0}
    for (state, district), group in by_district.items():
        result = engine.recommend(district, "en", "", state, today=today)
        if result.get("error"):
            continue
        actions = urgent_actions(result)
        if not actions or result["district"]["sources"]["weather"] != "live":
            continue  # only alert on real forecasts, never on estimates
        summary["with_alerts"] += 1
        alert_key = hashlib.sha1(f"{today.isoformat()}|{'|'.join(a['title'] for a in actions)}".encode()).hexdigest()[:16]
        first = actions[0]
        extra = f" (+{len(actions) - 1})" if len(actions) > 1 else ""

        for subscription in group:
            if subscription.get("last_alert_key") == alert_key:
                summary["skipped_repeat"] += 1
                continue
            language = subscription.get("language") or "en"
            title, text = first["title"], first["text"]
            if language != "en" and translator is not None:
                translated = translator.translate([title, text], language)
                if translated:
                    title, text = translated
            payload = {
                "title": f"{district}: {title}",
                "body": text + extra,
                "url": "/?" + urlencode({"language": language, "state": state, "district": district}),
                "tag": f"farmsaarthi-{endpoint_key(state + district)[:12]}",
            }
            status = sender.send(subscription, payload)
            if status == "ok":
                store.mark_sent(subscription["RowKey"], alert_key)
                summary["sent"] += 1
            elif status == "gone":
                store.delete(subscription["RowKey"])
                summary["removed"] += 1
            else:
                summary["failed"] += 1
    return summary


def make_vapid_keys():
    """Return (public, private) keys in the base64url form browsers and pywebpush expect."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    return b64(public), b64(private)
