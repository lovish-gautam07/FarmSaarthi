"""Read advice aloud with Azure AI Speech (free F0 tier: 0.5 million characters a month).

* Audio for each unique sentence is made once and kept (in Blob Storage when
  it is configured), so playing the same advice again costs nothing.
* Only text the server produced itself can be spoken: every piece of advice
  carries an HMAC signature, so the free quota can't be used for other text.
* If no key is set, the page falls back to the phone's built-in voice.
"""

import hashlib
import hmac
import os
import threading
import time
from collections import OrderedDict
from xml.sax.saxutils import escape, quoteattr

import requests

MAX_SPEECH_CHARS = 1500
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
REQUEST_TIMEOUT = 15
RETRY_AFTER_FAILURE_SECONDS = 300

# Used only if the voice list can't be fetched. Azure picks these up by name.
FALLBACK_VOICES = {
    "en-IN": "en-IN-NeerjaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "bn-IN": "bn-IN-TanishaaNeural",
    "gu-IN": "gu-IN-DhwaniNeural",
    "kn-IN": "kn-IN-SapnaNeural",
    "ml-IN": "ml-IN-SobhanaNeural",
    "mr-IN": "mr-IN-AarohiNeural",
    "ta-IN": "ta-IN-PallaviNeural",
    "te-IN": "te-IN-ShrutiNeural",
    "ur-IN": "ur-IN-GulNeural",
    "pa-IN": "pa-IN-VaaniNeural",
    "as-IN": "as-IN-YashicaNeural",
    "or-IN": "or-IN-SubhasiniNeural",
}


class SpeechSigner:
    def __init__(self, secret):
        self._secret = secret if isinstance(secret, bytes) else secret.encode("utf-8")

    def sign(self, language, text):
        message = f"{language}\n{text}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:32]

    def verify(self, language, text, signature):
        return isinstance(signature, str) and hmac.compare_digest(self.sign(language, text), signature)


class MemoryAudioCache:
    def __init__(self, max_items=200):
        self._items = OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        return None

    def put(self, key, audio):
        with self._lock:
            self._items[key] = audio
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)


class BlobAudioCache:
    """Keeps generated audio in a private blob container next to the photos."""

    def __init__(self, blob_service, container="speech-cache"):
        self.container = blob_service.get_container_client(container)
        self._ready = False
        self._lock = threading.Lock()

    def _ensure(self):
        if self._ready:
            return
        from azure.core.exceptions import ResourceExistsError

        with self._lock:
            if not self._ready:
                try:
                    self.container.create_container()
                except ResourceExistsError:
                    pass
                self._ready = True

    def get(self, key):
        from azure.core.exceptions import ResourceNotFoundError

        self._ensure()
        try:
            return self.container.download_blob(f"{key}.mp3").readall()
        except ResourceNotFoundError:
            return None

    def put(self, key, audio):
        from azure.storage.blob import ContentSettings

        self._ensure()
        self.container.upload_blob(
            f"{key}.mp3", audio, overwrite=True, content_settings=ContentSettings(content_type="audio/mpeg")
        )


class AzureSpeech:
    def __init__(self, key=None, region=None, session=None, cache=None):
        self.key = (key or "").strip()
        self.region = (region or "").strip()
        self.session = session or requests.Session()
        self.cache = cache or MemoryAudioCache()
        self._voices = None
        self._lock = threading.Lock()
        self._failed_until = 0.0

    @classmethod
    def from_env(cls, cache=None):
        return cls(os.environ.get("AZURE_SPEECH_KEY"), os.environ.get("AZURE_SPEECH_REGION"), cache=cache)

    @property
    def enabled(self):
        return bool(self.key and self.region)

    @property
    def _base(self):
        return f"https://{self.region}.tts.speech.microsoft.com"

    def _headers(self, **extra):
        return {"Ocp-Apim-Subscription-Key": self.key, "User-Agent": "FarmSaarthi", **extra}

    def voice_for(self, locale):
        with self._lock:
            if self._voices is None:
                self._voices = self._load_voices()
            voices = self._voices
        candidates = [v for v in voices if v.get("Locale") == locale]
        candidates.sort(key=lambda v: (v.get("Gender") != "Female", "Neural" not in v.get("ShortName", "")))
        if candidates:
            return candidates[0]["ShortName"]
        return FALLBACK_VOICES.get(locale)

    def _load_voices(self):
        try:
            response = self.session.get(f"{self._base}/cognitiveservices/voices/list", headers=self._headers(), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            return []

    def synthesize(self, text, locale):
        """Return MP3 bytes, or None if the service is off, failing or has no voice."""
        if not self.enabled or not text or len(text) > MAX_SPEECH_CHARS:
            return None
        voice = self.voice_for(locale)
        if not voice:
            return None

        key = hashlib.sha256(f"{voice}\n{text}".encode("utf-8")).hexdigest()
        try:
            cached = self.cache.get(key)
        except Exception:
            cached = None
        if cached:
            return cached
        if time.monotonic() < self._failed_until:
            return None

        ssml = (
            f"<speak version='1.0' xml:lang={quoteattr(locale)}>"
            f"<voice name={quoteattr(voice)}><prosody rate='-8%'>{escape(text)}</prosody></voice></speak>"
        )
        try:
            response = self.session.post(
                f"{self._base}/cognitiveservices/v1",
                headers=self._headers(**{"Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": OUTPUT_FORMAT}),
                data=ssml.encode("utf-8"),
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            audio = response.content
        except requests.RequestException:
            self._failed_until = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
            return None

        try:
            self.cache.put(key, audio)
        except Exception:
            pass  # caching is a saving, not a requirement
        return audio
