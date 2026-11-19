import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()  # reads .env for local development; real servers use app settings

from engine import FarmSaarthiEngine  # noqa: E402
from events import EventLog, new_row_key  # noqa: E402
from photos import MAX_NOTE_LENGTH, MAX_UPLOAD_BYTES, PhotoError, PhotoStore, clean_text, process_image  # noqa: E402
from speech import MAX_SPEECH_CHARS, AzureSpeech, BlobAudioCache, SpeechSigner  # noqa: E402
from translations import AzureTranslator, I18n  # noqa: E402
from weather import WeatherClient  # noqa: E402

log = logging.getLogger("farmsaarthi")

ROOT = Path(__file__).parent
UPLOADS_PER_WINDOW = 10
SPEECH_PER_WINDOW = 60
FEEDBACK_PER_WINDOW = 20
WINDOW_SECONDS = 10 * 60
MAX_GALLERY_ITEMS = 24
MAX_COMMENT_LENGTH = 280
ROW_KEY = re.compile(r"^[0-9]{13}_[0-9a-f]{32}$")


class RateLimiter:
    """Small in-memory limit on requests per client address."""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def asset_version():
    """Changes whenever the page, styles, icons or text change, so phones refresh their offline copy."""
    digest = hashlib.sha256()
    paths = [*ROOT.glob("static/**/*"), *ROOT.glob("templates/*"), ROOT / "data" / "i18n.json"]
    for path in sorted(p for p in paths if p.is_file()):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def create_app(engine=None, i18n=None, photo_store="from_env", speech="from_env", events="from_env",
               secret_key=None, inline_background=False):
    app = Flask(__name__)
    # Room for a 5 MB photo plus the other form fields.
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 256 * 1024
    # Azure App Service and Render sit behind one proxy; trust its client address.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    engine = engine or FarmSaarthiEngine(weather_client=WeatherClient())
    i18n = i18n or I18n(AzureTranslator.from_env())

    if photo_store == "from_env":
        try:
            photo_store = PhotoStore.from_env()
        except ValueError as problem:
            log.error("Photo storage disabled: %s", problem)
            photo_store = None
    store = photo_store

    if speech == "from_env":
        blob_service = getattr(store, "blob_service", None)
        cache = BlobAudioCache(blob_service) if blob_service is not None else None
        speech = AzureSpeech.from_env(cache=cache)

    if events == "from_env":
        try:
            events = EventLog.from_env()
        except Exception:
            log.exception("Usage and feedback records disabled")
            events = None

    secret = secret_key or os.environ.get("SECRET_KEY", "").strip()
    if not secret:
        secret = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set: read-aloud signatures change on every restart (fine for one worker).")
    signer = SpeechSigner(secret)

    background = None if inline_background else ThreadPoolExecutor(max_workers=2)
    version = asset_version()
    upload_limiter = RateLimiter(UPLOADS_PER_WINDOW, WINDOW_SECONDS)
    speech_limiter = RateLimiter(SPEECH_PER_WINDOW, WINDOW_SECONDS)
    feedback_limiter = RateLimiter(FEEDBACK_PER_WINDOW, WINDOW_SECONDS)
    crop_choices = sorted(({"id": key, "name": meta["name"]} for key, meta in engine.crop_meta.items()), key=lambda c: c["name"])
    crop_ids = {crop["id"] for crop in crop_choices}

    def error(code, message, status):
        return jsonify({"error": message, "error_code": code}), status

    def client_address():
        return request.remote_addr or "unknown"

    def run_later(task, *args):
        def safe():
            try:
                task(*args)
            except Exception:
                log.exception("Background task failed")

        if background is None:
            safe()
        else:
            background.submit(safe)

    def speakable(text, language):
        text = text[:MAX_SPEECH_CHARS]
        return {"text": text, "sig": signer.sign(language, text)}

    def attach_speech(result, language):
        for action in result["actions"]:
            action["speak"] = speakable(f"{action['title']}. {action['text']}", language)
        for crop in result["top_crops"]:
            crop["speak"] = speakable(f"{crop['crop']}. {' '.join(crop['reasons'])}", language)
        for note in result["interest_notes"]:
            note["speak"] = speakable(note["text"], language)
        result["speak_all"] = speakable(" ".join(action["speak"]["text"] for action in result["actions"]), language)

    # ----- pages -------------------------------------------------------------

    @app.get("/")
    def index():
        language = i18n.normalize(request.args.get("language", "en"))
        return render_template(
            "index.html",
            districts=engine.district_options(),
            states=engine.list_states(),
            bundle=i18n.bundle(language),
            language_names=i18n.language_names,
            selected_language=language,
            crop_choices=crop_choices,
            photos_enabled=store is not None,
            speech_enabled=speech.enabled,
            feedback_enabled=events is not None,
            max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
            asset_version=version,
        )

    @app.get("/i18n/<language>")
    def language_bundle(language):
        return jsonify(i18n.bundle(language))

    @app.get("/sw.js")
    def service_worker():
        response = Response(render_template("sw.js", version=version), mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/manifest.webmanifest")
    def manifest():
        data = {
            "name": "FarmSaarthi",
            "short_name": "FarmSaarthi",
            "description": "Crop advice, weather alerts and read-aloud guidance in 13 Indian languages.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f7f2e7",
            "theme_color": "#1d7a58",
            "icons": [
                {"src": f"/static/icons/icon-192.png?v={version}", "sizes": "192x192", "type": "image/png"},
                {"src": f"/static/icons/icon-512.png?v={version}", "sizes": "512x512", "type": "image/png"},
                {"src": f"/static/icons/maskable-512.png?v={version}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        }
        return Response(json.dumps(data), mimetype="application/manifest+json")

    # ----- analysis ----------------------------------------------------------

    @app.post("/analyze")
    def analyze():
        payload = request.get_json(silent=True) or request.form
        language = i18n.normalize(payload.get("language", "en"))

        result = engine.recommend(
            payload.get("district", ""),
            language,
            payload.get("crop_interest", ""),
            payload.get("state", ""),
        )
        if result.get("error"):
            return jsonify(result), 400

        top_crop = result["top_crops"][0]["crop_id"]
        translated = i18n.translate_analysis(result, language)
        result["translation"] = {"requested": language != "en", "applied": language != "en" and translated}
        attach_speech(result, language)

        result["analysis_id"] = new_row_key()
        if events is not None:
            run_later(events.record, "analysis", {
                "state": result["district"]["state"],
                "district": result["district"]["name"],
                "language": language,
                "top_crop": top_crop,
                "weather": result["district"]["sources"]["weather"],
            }, result["analysis_id"])
        return jsonify(result)

    # ----- read aloud --------------------------------------------------------

    @app.post("/speak")
    def speak():
        payload = request.get_json(silent=True) or {}
        language = i18n.normalize(payload.get("language", "en"))
        text = payload.get("text")
        if not isinstance(text, str) or not signer.verify(language, text, payload.get("sig")):
            return error("bad_signature", "This text can't be read aloud.", 403)
        if not speech.enabled:
            return error("speech_off", "Read-aloud is not set up on this server.", 503)
        if not speech_limiter.allow(client_address()):
            return error("rate_limited", "Too many requests. Please wait a few minutes.", 429)

        audio = speech.synthesize(text, i18n.speech_locales.get(language, "en-IN"))
        if not audio:
            return error("speech_failed", "Read-aloud is not available right now.", 502)
        return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})

    # ----- feedback and stats ------------------------------------------------

    @app.post("/feedback")
    def feedback():
        if events is None:
            return error("storage_off", "Feedback storage is not set up on this server.", 503)
        payload = request.get_json(silent=True) or {}

        useful = payload.get("useful")
        if not isinstance(useful, bool):
            return error("bad_feedback", "Answer yes or no.", 400)
        state, district = payload.get("state", ""), payload.get("district", "")
        if not engine.get_district(district, state):
            return error("bad_district", "Select a valid state and district.", 400)
        feedback_id = payload.get("feedback_id")
        if feedback_id is not None and not ROW_KEY.match(str(feedback_id)):
            return error("bad_feedback", "Unknown feedback.", 400)
        analysis_id = payload.get("analysis_id")
        if analysis_id is not None and not ROW_KEY.match(str(analysis_id)):
            analysis_id = None
        if not feedback_limiter.allow(client_address()):
            return error("rate_limited", "Too many requests. Please wait a few minutes.", 429)

        data = {
            "useful": useful,
            "state": state,
            "district": district,
            "language": i18n.normalize(payload.get("language", "en")),
            "top_crop": clean_text(payload.get("top_crop"), 40),
            "analysis_id": analysis_id or "",
        }
        comment = clean_text(payload.get("comment"), MAX_COMMENT_LENGTH)
        if comment:
            data["comment"] = comment
        try:
            row_key = events.record("feedback", data, row_key=feedback_id)
        except Exception:
            log.exception("Could not save feedback")
            return error("storage_error", "Feedback could not be saved.", 502)
        return jsonify({"feedback_id": row_key}), 201

    def stats_summary():
        if events is None:
            return None
        summary = events.summary()
        if store is not None:
            try:
                summary["photos"] = store.count()
            except Exception:
                summary["photos"] = None
        return summary

    @app.get("/stats.json")
    def stats_json():
        if events is None:
            return error("storage_off", "Usage records are not set up on this server.", 503)
        try:
            return jsonify(stats_summary())
        except Exception:
            log.exception("Could not build stats")
            return error("storage_error", "Could not reach storage.", 502)

    @app.get("/stats")
    def stats_page():
        summary, failed = None, False
        try:
            summary = stats_summary()
        except Exception:
            log.exception("Could not build stats")
            failed = True
        return render_template("stats.html", summary=summary, failed=failed,
                               language_names=i18n.language_names, crop_meta=engine.crop_meta, asset_version=version)

    # ----- photos ------------------------------------------------------------

    @app.get("/photos")
    def list_photos():
        if store is None:
            return error("storage_off", "Photo storage is not set up on this server.", 503)
        try:
            limit = max(1, min(int(request.args.get("limit", 12)), MAX_GALLERY_ITEMS))
        except ValueError:
            limit = 12
        try:
            photos = store.list_recent(
                limit=limit,
                state=request.args.get("state") or None,
                district=request.args.get("district") or None,
            )
        except Exception:
            log.exception("Could not list photos")
            return error("storage_error", "Could not reach photo storage.", 502)
        return jsonify({"photos": photos})

    @app.post("/photos")
    def upload_photo():
        if store is None:
            return error("storage_off", "Photo storage is not set up on this server.", 503)
        if not upload_limiter.allow(client_address()):
            return error("rate_limited", "Too many uploads. Please wait a few minutes.", 429)

        state = request.form.get("state", "")
        district = request.form.get("district", "")
        if not engine.get_district(district, state):
            return error("bad_district", "Select a valid state and district.", 400)

        crop = request.form.get("crop", "")
        if crop and crop not in crop_ids:
            return error("bad_crop", "Unknown crop.", 400)

        upload = request.files.get("photo")
        if upload is None or not upload.filename:
            return error("no_file", "Choose a photo first.", 400)

        try:
            processed = process_image(upload.read(MAX_UPLOAD_BYTES + 1))
        except PhotoError as problem:
            return error(problem.code, problem.message, 400)

        details = {
            "state": state,
            "district": district,
            "crop": engine.crop_meta[crop]["name"] if crop else "",
            "note": clean_text(request.form.get("note"), MAX_NOTE_LENGTH),
            "language": i18n.normalize(request.form.get("language", "en")),
        }
        try:
            record = store.save(processed, details)
        except Exception:
            log.exception("Could not save photo")
            return error("storage_error", "The photo could not be saved to cloud storage.", 502)
        return jsonify(record), 201

    @app.errorhandler(413)
    def too_large(_):
        return error("too_large", "The photo is larger than 5 MB.", 413)

    @app.get("/healthz")
    def health():
        return jsonify({
            "status": "ok",
            "photo_storage": store is not None,
            "translator": i18n.translator.enabled,
            "speech": speech.enabled,
            "feedback": events is not None,
        })

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
