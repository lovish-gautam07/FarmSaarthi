"""Real round trip through Azure Blob + Table Storage, using the Azurite emulator.

Runs only when FARMSAARTHI_TEST_AZURITE=1 (the GitHub workflow starts Azurite
and sets it). Locally: start Azurite, then
    FARMSAARTHI_TEST_AZURITE=1 pytest tests/test_azure_storage.py
"""

import io
import os
import uuid

import pytest
import requests
from PIL import ExifTags, Image

from photos import PhotoStore, process_image

AZURITE = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)

pytestmark = pytest.mark.skipif(os.environ.get("FARMSAARTHI_TEST_AZURITE") != "1", reason="Azurite not requested")


@pytest.fixture
def store():
    suffix = uuid.uuid4().hex[:8]
    store = PhotoStore(AZURITE, container=f"test-photos-{suffix}", table=f"TestPhotos{suffix}")
    yield store
    store.container.delete_container()
    store.table.delete_table()


def test_round_trip(store, make_image):
    exif = Image.Exif()
    exif[ExifTags.IFD.GPSInfo] = {1: "N", 2: (18.0, 5.0, 0.0), 3: "E", 4: (82.0, 40.0, 0.0)}
    processed = process_image(make_image(size=(2400, 1800), exif=exif.tobytes()))

    first = store.save(processed, {"state": "Andhra Pradesh", "district": "Alluri Sitharama Raju", "crop": "Rice", "note": "", "language": "te"})
    second = store.save(processed, {"state": "Punjab", "district": "Ludhiana", "crop": "Wheat", "note": "leaf spots", "language": "pa"})

    # Newest first, and filtering by district works.
    assert [p["id"] for p in store.list_recent()] == [second["id"], first["id"]]
    assert [p["id"] for p in store.list_recent(district="Ludhiana")] == [second["id"]]

    # The signed link works; the same address without the signature does not.
    signed = requests.get(second["url"], timeout=10)
    assert signed.status_code == 200
    assert signed.headers["Content-Type"] == "image/jpeg"
    assert len(Image.open(io.BytesIO(signed.content)).getexif()) == 0
    assert requests.get(second["url"].split("?")[0], timeout=10).status_code in (403, 404)


def test_event_log_and_audio_cache_round_trip():
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient

    from events import EventLog
    from speech import BlobAudioCache

    suffix = uuid.uuid4().hex[:8]
    table = TableServiceClient.from_connection_string(AZURITE).get_table_client(f"TestEvents{suffix}")
    log = EventLog(table)
    try:
        log.record("analysis", {"state": "Punjab", "district": "Ludhiana", "language": "pa", "top_crop": "wheat", "weather": "live"})
        feedback_id = log.record("feedback", {"useful": True, "language": "pa"})
        assert log.record("feedback", {"useful": True, "comment": "Clear advice"}, row_key=feedback_id) == feedback_id
        summary = log.summary()
        assert summary["analyses"] == 1 and summary["feedback"] == 1 and summary["comments"] == 1
        assert log.rows("feedback")[0]["language"] == "pa"  # merge kept the first write's fields
    finally:
        table.delete_table()

    blob_service = BlobServiceClient.from_connection_string(AZURITE)
    cache = BlobAudioCache(blob_service, container=f"test-speech-{suffix}")
    try:
        assert cache.get("missing") is None
        cache.put("abc", b"ID3-audio")
        assert cache.get("abc") == b"ID3-audio"
    finally:
        cache.container.delete_container()


def test_alert_store_round_trip():
    from azure.data.tables import TableServiceClient

    from alerts import AlertStore
    from conftest import BROWSER_SUBSCRIPTION

    table = TableServiceClient.from_connection_string(AZURITE).get_table_client(f"TestAlerts{uuid.uuid4().hex[:8]}")
    store = AlertStore(table)
    try:
        key = store.subscribe(BROWSER_SUBSCRIPTION, "Punjab", "Ludhiana", "pa")
        store.subscribe(BROWSER_SUBSCRIPTION, "Punjab", "Patiala", "pa")  # same browser moves district
        rows = store.all()
        assert len(rows) == 1 and rows[0]["district"] == "Patiala"
        store.mark_sent(key, "abc123")
        assert store.all()[0]["last_alert_key"] == "abc123"
        store.unsubscribe(BROWSER_SUBSCRIPTION["endpoint"])
        assert store.all() == []
    finally:
        table.delete_table()
