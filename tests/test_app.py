import io

from app import UPLOADS_PER_WINDOW, create_app
from alerts import PushSender
from conftest import BROWSER_SUBSCRIPTION, FakeSpeech
from datagov import DataGovClient
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


def bare_app(engine, i18n_offline):
    """An app with no outside services configured at all."""
    return create_app(engine=engine, i18n=i18n_offline, photo_store=None, speech=FakeSpeech(enabled=False),
                      events=None, secret_key="x", inline_background=True, datagov=DataGovClient(""),
                      alert_store=None, push=PushSender(), task_key="")


def test_photo_routes_without_storage(engine, i18n_offline, make_image):
    app = bare_app(engine, i18n_offline)
    client = app.test_client()
    assert client.get("/photos").status_code == 503
    assert upload(client, make_image()).get_json()["error_code"] == "storage_off"
    page = client.get("/").get_data(as_text=True)
    assert "photosEnabled = false" in page
    assert 'id="feedback-bar"' not in page  # no storage, no feedback buttons
    assert client.post("/feedback", json={"useful": True}).get_json()["error_code"] == "storage_off"
    assert client.get("/stats.json").status_code == 503
    assert "not set up" in client.get("/stats").get_data(as_text=True)


def test_health(client):
    assert client.get("/healthz").get_json() == {
        "status": "ok", "photo_storage": True, "translator": False, "speech": True, "feedback": True,
        "prices": True, "ask": True, "alerts": True,
    }


# ----- action cards and read-aloud ----------------------------------------------

def analyze(client, **payload):
    body = {"language": "en", "state": "Punjab", "district": "Ludhiana", "crop_interest": "", **payload}
    return client.post("/analyze", json=body).get_json()


def test_analysis_has_action_cards_with_speech_signatures(client):
    data = analyze(client)
    assert data["actions"], "expected at least one action card"
    for action in data["actions"]:
        assert action["level"] in {"alert", "warn", "good", "info"}
        assert action["speak"]["text"].startswith(action["title"])
        assert len(action["speak"]["sig"]) == 32
    for crop in data["top_crops"]:
        assert crop["speak"]["sig"]
    assert data["speak_all"]["text"]


def test_analysis_is_recorded_for_stats(client):
    data = analyze(client, language="hi")
    rows = client.events.of_kind("analysis")
    assert len(rows) == 1
    assert rows[0]["RowKey"] == data["analysis_id"]
    assert rows[0]["language"] == "hi" and rows[0]["district"] == "Ludhiana"
    assert rows[0]["top_crop"] == data["top_crops"][0]["crop_id"]


def test_speak_returns_audio_for_signed_text(client):
    item = analyze(client)["actions"][0]["speak"]
    response = client.post("/speak", json={"text": item["text"], "sig": item["sig"], "language": "en"})
    assert response.status_code == 200
    assert response.mimetype == "audio/mpeg"
    assert client.speech.spoken == [(item["text"], "en-IN")]


def test_speak_refuses_unsigned_or_changed_text(client):
    item = analyze(client)["actions"][0]["speak"]
    tampered = client.post("/speak", json={"text": item["text"] + " Buy now!", "sig": item["sig"], "language": "en"})
    assert tampered.status_code == 403
    other_language = client.post("/speak", json={"text": item["text"], "sig": item["sig"], "language": "hi"})
    assert other_language.status_code == 403
    assert client.post("/speak", json={"text": "hello"}).status_code == 403
    assert client.speech.spoken == []


def test_speak_when_speech_is_off(engine, i18n_offline):
    app = bare_app(engine, i18n_offline)
    client = app.test_client()
    item = client.post("/analyze", json={"state": "Punjab", "district": "Ludhiana"}).get_json()["actions"][0]["speak"]
    response = client.post("/speak", json={"text": item["text"], "sig": item["sig"], "language": "en"})
    assert response.get_json()["error_code"] == "speech_off"


# ----- feedback and stats ---------------------------------------------------------

def test_feedback_then_comment_updates_the_same_row(client):
    data = analyze(client)
    base = {"state": "Punjab", "district": "Ludhiana", "language": "en", "analysis_id": data["analysis_id"], "top_crop": "wheat"}

    first = client.post("/feedback", json={**base, "useful": True})
    assert first.status_code == 201
    feedback_id = first.get_json()["feedback_id"]

    second = client.post("/feedback", json={**base, "useful": True, "feedback_id": feedback_id, "comment": "  Very\nclear  "})
    assert second.get_json()["feedback_id"] == feedback_id

    rows = client.events.of_kind("feedback")
    assert len(rows) == 1
    assert rows[0]["useful"] is True and rows[0]["comment"] == "Very clear"
    assert rows[0]["analysis_id"] == data["analysis_id"]


def test_feedback_validation(client):
    base = {"state": "Punjab", "district": "Ludhiana"}
    assert client.post("/feedback", json={**base, "useful": "yes"}).status_code == 400
    assert client.post("/feedback", json={"state": "Punjab", "district": "Nowhere", "useful": True}).status_code == 400
    assert client.post("/feedback", json={**base, "useful": True, "feedback_id": "../../etc"}).status_code == 400
    assert client.events.of_kind("feedback") == []


def test_stats(client):
    analyze(client, language="hi")
    analyze(client, language="ta")
    base = {"state": "Punjab", "district": "Ludhiana", "language": "hi"}
    client.post("/feedback", json={**base, "useful": True})
    client.post("/feedback", json={**base, "useful": False})

    stats = client.get("/stats.json").get_json()
    assert stats["analyses"] == 2
    assert stats["feedback"] == 2 and stats["useful_percent"] == 50
    assert dict(stats["languages"]) == {"hi": 1, "ta": 1}
    assert stats["photos"] is None or isinstance(stats["photos"], int)
    page = client.get("/stats").get_data(as_text=True)
    assert "Usage and feedback" in page and "Hindi" in page


# ----- install and offline ---------------------------------------------------------

def test_service_worker_and_manifest(client):
    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert worker.mimetype == "application/javascript"
    assert "farmsaarthi-" in worker.get_data(as_text=True)

    manifest = client.get("/manifest.webmanifest")
    assert manifest.mimetype == "application/manifest+json"
    data = manifest.get_json(force=True)
    assert data["display"] == "standalone"
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200

    page = client.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in page and "serviceWorker" in page



# ----- round 2: soil card, prices, questions, alerts, schemes ----------------------

def test_analysis_with_soil_health_card(client):
    soil = {"type": "black", "n": 210, "p": 32, "k": 150, "ph": 8.1, "oc": 0.4}
    data = analyze(client, soil=soil)
    district = data["district"]
    assert district["sources"]["soil"] == "card"
    assert district["soil"] == "black"
    assert district["soil_card"]["n"] == {"value": 210, "rating": "low"}
    assert "Based on your Soil Health Card" in data["fertilizer_advice"]


def test_bad_soil_values_are_refused(client):
    response = client.post("/analyze", json={"state": "Punjab", "district": "Ludhiana", "soil": {"ph": 15}})
    assert response.status_code == 400 and response.get_json()["error_code"] == "bad_soil"


def test_prices_for_suggested_crops(client):
    data = client.get("/prices?state=Punjab&district=Ludhiana&crops=wheat,coffee,maize").get_json()["prices"]
    wheat = data["wheat"]
    assert wheat["status"] == "ok"
    assert wheat["markets"][0]["market"] == "Khanna APMC"   # the farmer's own district comes first
    assert wheat["markets"][1]["modal_price"] == 2500       # then the best price
    assert wheat["state_median"] == 2450
    assert data["coffee"]["status"] == "not_traded"
    assert data["maize"]["status"] == "none"


def test_prices_when_the_api_is_down(client):
    client.datagov.down = True
    data = client.get("/prices?state=Punjab&district=Ludhiana&crops=wheat").get_json()["prices"]
    assert data["wheat"]["status"] == "unavailable"


def test_prices_validation(client):
    assert client.get("/prices?state=Punjab&district=Nowhere&crops=wheat").status_code == 400
    assert client.get("/prices?state=Punjab&district=Ludhiana&crops=dragonfruit").status_code == 400


def test_ask_finds_similar_kcc_question(client):
    response = client.post("/ask", json={"question": "yellow rust in my wheat crop", "state": "Punjab",
                                         "district": "Ludhiana", "crop": "wheat", "language": "en"})
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["answers"][0]["question"] == "How to control yellow rust in wheat?"
    assert data["answers"][0]["district"] == "Ludhiana"
    assert all("Weather" not in a["question"] for a in data["answers"])
    assert data["answers"][0]["speak"]["sig"]
    # Without Translator, the Punjabi answer stays as the expert wrote it and the page says so.
    assert data["translated"] is False


def test_ask_validation_and_outage(client):
    base = {"state": "Punjab", "district": "Ludhiana"}
    assert client.post("/ask", json={**base, "question": "hi"}).get_json()["error_code"] == "bad_question"
    assert client.post("/ask", json={"question": "urea dose", "state": "Punjab", "district": "Nowhere"}).status_code == 400
    client.datagov.down = True
    assert client.post("/ask", json={**base, "question": "urea dose for wheat"}).get_json()["status"] == "unavailable"


def test_alert_subscription_lifecycle(client):
    body = {"subscription": BROWSER_SUBSCRIPTION, "state": "Punjab", "district": "Ludhiana", "language": "pa"}
    assert client.post("/alerts/subscribe", json=body).status_code == 201
    assert len(client.alert_store.all()) == 1 and client.alert_store.all()[0]["language"] == "pa"
    bad = {**body, "subscription": {"endpoint": "http://insecure.example", "keys": {}}}
    assert client.post("/alerts/subscribe", json=bad).status_code == 400
    assert client.post("/alerts/unsubscribe", json={"endpoint": BROWSER_SUBSCRIPTION["endpoint"]}).status_code == 200
    assert client.alert_store.all() == []


def test_send_alerts_task_needs_the_key(client):
    assert client.post("/tasks/send-alerts").status_code == 403
    assert client.post("/tasks/send-alerts", headers={"X-Task-Key": "wrong"}).status_code == 403
    response = client.post("/tasks/send-alerts", headers={"X-Task-Key": "task-secret"})
    assert response.status_code == 200 and response.get_json()["subscriptions"] == 0


def test_round_two_features_hidden_when_not_configured(engine, i18n_offline):
    client = bare_app(engine, i18n_offline).test_client()
    page = client.get("/").get_data(as_text=True)
    assert 'id="ask-panel"' not in page and 'id="alert-bar"' not in page
    assert client.get("/prices?state=Punjab&district=Ludhiana&crops=wheat").get_json()["error_code"] == "prices_off"
    assert client.post("/ask", json={"question": "urea dose"}).get_json()["error_code"] == "ask_off"
    assert client.post("/alerts/subscribe", json={}).get_json()["error_code"] == "alerts_off"
    assert client.post("/tasks/send-alerts", headers={"X-Task-Key": ""}).status_code == 404


def test_page_shows_round_two_sections_when_configured(client):
    page = client.get("/?state=Punjab&district=Ludhiana").get_data(as_text=True)
    assert 'id="ask-panel"' in page and 'id="alert-bar"' in page and 'id="soil-panel"' in page
    assert '"district": "Ludhiana"' in page  # notification links open the right district
    assert 'href="/schemes?language=en"' in page


def test_schemes_page(client):
    page = client.get("/schemes").get_data(as_text=True)
    for name in ("PM-KISAN", "Crop insurance", "Kisan Credit Card", "Soil Health Card", "e-NAM", "PM-KUSUM", "Kisan Call Centre"):
        assert name in page
    assert 'href="https://pmkisan.gov.in"' in page and 'href="tel:18001801551"' in page
    assert page.count('data-speak="') == 7
    assert "Shown in English" not in page
    hindi = client.get("/schemes?language=hi").get_data(as_text=True)
    assert "सरकारी योजनाएँ" in hindi and "अंग्रेज़ी में दिखाया गया है" in hindi  # no Translator key in tests
