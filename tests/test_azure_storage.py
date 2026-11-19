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
