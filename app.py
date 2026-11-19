import logging
import os
import threading
import time
from collections import defaultdict, deque

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()  # reads .env for local development; real servers use app settings

from engine import FarmSaarthiEngine  # noqa: E402
from photos import MAX_NOTE_LENGTH, MAX_UPLOAD_BYTES, PhotoError, PhotoStore, clean_text, process_image  # noqa: E402
from translations import AzureTranslator, I18n  # noqa: E402
from weather import WeatherClient  # noqa: E402

log = logging.getLogger("farmsaarthi")

UPLOADS_PER_WINDOW = 10
UPLOAD_WINDOW_SECONDS = 10 * 60
MAX_GALLERY_ITEMS = 24


class RateLimiter:
    """Small in-memory limit on uploads per client address."""

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


def create_app(engine=None, i18n=None, photo_store="from_env"):
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
        except ValueError as error:
            log.error("Photo storage disabled: %s", error)
            photo_store = None
    store = photo_store
    upload_limiter = RateLimiter(UPLOADS_PER_WINDOW, UPLOAD_WINDOW_SECONDS)
    crop_choices = sorted(({"id": key, "name": meta["name"]} for key, meta in engine.crop_meta.items()), key=lambda c: c["name"])
    crop_ids = {crop["id"] for crop in crop_choices}

    def error(code, message, status):
        return jsonify({"error": message, "error_code": code}), status

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
            max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
        )

    @app.get("/i18n/<language>")
    def language_bundle(language):
        return jsonify(i18n.bundle(language))

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

        translated = i18n.translate_analysis(result, language)
        result["translation"] = {"requested": language != "en", "applied": language != "en" and translated}
        return jsonify(result)

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
        if not upload_limiter.allow(request.remote_addr or "unknown"):
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
        })

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
