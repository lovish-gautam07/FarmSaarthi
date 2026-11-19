import io

from app import UPLOADS_PER_WINDOW, create_app
from photos import MAX_UPLOAD_BYTES


def upload(client, image_bytes, **fields):
    data = {"state": "Punjab", "district": "Ludhiana", "language": "en", **fields}
    if image_bytes is not None:
        data["photo"] = (io.BytesIO(image_bytes), "field.jpg", "image/jpeg")
    return client.post("/photos", data=data, content_type="multipart/form-data")


def test_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "FarmSaarthi" in page
    assert 'id="photo-form"' in page
    assert "Assamese (AS)" in page and "Urdu (UR)" in page and "Odia (OR)" in page


def test_home_page_in_urdu_is_right_to_left(client):
    page = client.get("/?language=ur").get_data(as_text=True)
    assert 'dir="rtl"' in page


def test_language_bundle_endpoint(client):
    data = client.get("/i18n/ta").get_json()
    assert data["language"] == "ta"
    assert data["speech_locale"] == "ta-IN"


def test_analyze_ok_and_reports_translation(client):
    response = client.post("/analyze", json={"language": "hi", "state": "Punjab", "district": "Ludhiana", "crop_interest": "wheat"})
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["top_crops"]) == 3
    assert data["translation"] == {"requested": True, "applied": False}  # no Translator key in tests


def test_analyze_unknown_district(client):
    response = client.post("/analyze", json={"state": "Punjab", "district": "Nowhere"})
    assert response.status_code == 400


def test_upload_saves_photo_and_lists_it(client, make_image):
    response = upload(client, make_image(), crop="wheat", note="  yellow spots\n")
    assert response.status_code == 201
    record = response.get_json()
    assert record["crop"] == "Wheat"
    assert record["note"] == "yellow spots"
    assert record["district"] == "Ludhiana"

    listed = client.get("/photos?state=Punjab&district=Ludhiana").get_json()["photos"]
    assert [p["id"] for p in listed] == [record["id"]]
    assert client.get("/photos?state=Kerala").get_json()["photos"] == []


def test_upload_refuses_bad_input(client, make_image):
    assert upload(client, None).get_json()["error_code"] == "no_file"
    assert upload(client, b"not an image").get_json()["error_code"] == "wrong_type"
    assert upload(client, make_image(), district="Nowhere").get_json()["error_code"] == "bad_district"
    assert upload(client, make_image(), crop="dragonfruit").get_json()["error_code"] == "bad_crop"
    assert client.store.saved == []


def test_upload_over_the_size_limit_gets_413(client):
    response = upload(client, b"0" * (MAX_UPLOAD_BYTES + 512 * 1024))
    assert response.status_code == 413
    assert response.get_json()["error_code"] == "too_large"


def test_uploads_are_rate_limited(client, make_image):
    image = make_image(size=(200, 150))
    for _ in range(UPLOADS_PER_WINDOW):
        assert upload(client, image).status_code == 201
    response = upload(client, image)
    assert response.status_code == 429
    assert response.get_json()["error_code"] == "rate_limited"


def test_photo_routes_without_storage(engine, i18n_offline, make_image):
    app = create_app(engine=engine, i18n=i18n_offline, photo_store=None)
    client = app.test_client()
    assert client.get("/photos").status_code == 503
    assert upload(client, make_image()).get_json()["error_code"] == "storage_off"
    assert "photosEnabled = false" in client.get("/").get_data(as_text=True)


def test_health(client):
    assert client.get("/healthz").get_json() == {"status": "ok", "photo_storage": True, "translator": False}
