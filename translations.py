"""Interface text and Azure Translator.

Hand-written text in data/i18n.json always wins. Anything missing for a
language (and all advice the engine writes) is translated with Azure
Translator when AZURE_TRANSLATOR_KEY is set; otherwise English is shown.
"""

import json
import os
import threading
import time
from pathlib import Path

import requests

I18N_PATH = Path(__file__).parent / "data" / "i18n.json"
DEFAULT_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
MAX_ITEMS_PER_CALL = 100       # Azure Translator limit per request
MAX_CHARS_PER_CALL = 45000     # limit is 50,000; keep a margin
MAX_CACHE_ENTRIES = 20000
REQUEST_TIMEOUT = 8
RETRY_AFTER_FAILURE_SECONDS = 300


class AzureTranslator:
    def __init__(self, key=None, region=None, endpoint=None, session=None):
        self.key = (key or "").strip()
        self.region = (region or "").strip()
        self.endpoint = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self.session = session or requests.Session()
        self._cache = {}
        self._lock = threading.Lock()
        self._failed_until = 0.0

    @classmethod
    def from_env(cls):
        return cls(
            key=os.environ.get("AZURE_TRANSLATOR_KEY"),
            region=os.environ.get("AZURE_TRANSLATOR_REGION"),
            endpoint=os.environ.get("AZURE_TRANSLATOR_ENDPOINT"),
        )

    @property
    def enabled(self):
        return bool(self.key)

    def translate(self, texts, to_lang, from_lang="en"):
        """Translate a list of strings. Returns None if the service is off or fails."""
        texts = list(texts)
        if to_lang == from_lang or not texts:
            return texts
        if not self.enabled or time.monotonic() < self._failed_until:
            return None

        with self._lock:
            missing = list(dict.fromkeys(t for t in texts if t and (to_lang, t) not in self._cache))

        for chunk in self._chunks(missing):
            translated = self._call(chunk, to_lang, from_lang)
            if translated is None:
                self._failed_until = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
                return None
            with self._lock:
                if len(self._cache) > MAX_CACHE_ENTRIES:
                    self._cache.clear()
                self._cache.update({(to_lang, src): dst for src, dst in zip(chunk, translated)})

        with self._lock:
            return [self._cache.get((to_lang, t), t) if t else t for t in texts]

    @staticmethod
    def _chunks(items):
        chunk, size = [], 0
        for item in items:
            if chunk and (len(chunk) >= MAX_ITEMS_PER_CALL or size + len(item) > MAX_CHARS_PER_CALL):
                yield chunk
                chunk, size = [], 0
            chunk.append(item)
            size += len(item)
        if chunk:
            yield chunk

    def _call(self, chunk, to_lang, from_lang):
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        try:
            response = self.session.post(
                f"{self.endpoint}/translate",
                params={"api-version": "3.0", "from": from_lang, "to": to_lang, "textType": "plain"},
                headers=headers,
                json=[{"Text": text} for text in chunk],
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return [item["translations"][0]["text"] for item in response.json()]
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
            return None


class I18n:
    def __init__(self, translator=None):
        with I18N_PATH.open(encoding="utf-8") as file:
            data = json.load(file)
        self.language_names = data["language_names"]
        self.labels = data["labels"]
        self.ui = data["ui"]
        self.rtl = set(data["rtl"])
        self.speech_locales = data["speech_locales"]
        self.translator = translator or AzureTranslator()
        self._bundles = {}
        self._lock = threading.Lock()

    def normalize(self, language):
        return language if language in self.language_names else "en"

    def bundle(self, language):
        """Everything the page needs for one language."""
        language = self.normalize(language)
        with self._lock:
            if language in self._bundles:
                return self._bundles[language]

        labels, labels_done = self._fill(self.labels["en"], self.labels.get(language, {}), language)
        ui, ui_done = self._fill(self.ui["en"], self.ui.get(language, {}), language)
        bundle = {
            "language": language,
            "labels": labels,
            "ui": ui,
            "dir": "rtl" if language in self.rtl else "ltr",
            "speech_locale": self.speech_locales.get(language, "en-IN"),
            "complete": labels_done and ui_done,
        }
        # Only cache once nothing is left untranslated, so a later fix to the
        # Translator settings (or a brief outage) is picked up.
        if bundle["complete"]:
            with self._lock:
                self._bundles[language] = bundle
        return bundle

    def _fill(self, english, own, language):
        merged = {**english, **own}
        missing = [key for key in english if key not in own]
        if language == "en" or not missing:
            return merged, True
        translated = self.translator.translate([english[key] for key in missing], language)
        if translated is None:
            return merged, False
        merged.update(dict(zip(missing, translated)))
        return merged, True

    def translate_analysis(self, result, language):
        """Translate the engine's English sentences in place. Returns True if translated."""
        language = self.normalize(language)
        if language == "en":
            return True

        slots = []  # (container, key) pairs whose string value should be translated
        for crop in result["top_crops"]:
            slots.append((crop, "crop"))
            slots.extend((crop["reasons"], i) for i in range(len(crop["reasons"])))
        slots.append((result, "irrigation_advice"))
        slots.append((result, "fertilizer_advice"))
        slots.extend((result["alerts"], i) for i in range(len(result["alerts"])))
        slots.append((result["district"], "soil"))
        slots.append((result["district"], "season"))
        slots.append((result["model_summary"], "accuracy_note"))

        translated = self.translator.translate([container[key] for container, key in slots], language)
        if translated is None:
            return False
        for (container, key), text in zip(slots, translated):
            container[key] = text
        return True
