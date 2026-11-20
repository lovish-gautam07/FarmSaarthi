import requests

from speech import MAX_SPEECH_CHARS, AzureSpeech, MemoryAudioCache, SpeechSigner


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self.payload, self.content = payload, content

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, voices=None, fail=False):
        self.voices = voices if voices is not None else [
            {"ShortName": "hi-IN-MadhurNeural", "Locale": "hi-IN", "Gender": "Male"},
            {"ShortName": "hi-IN-SwaraNeural", "Locale": "hi-IN", "Gender": "Female"},
        ]
        self.fail = fail
        self.posts = []

    def get(self, url, **kwargs):
        return FakeResponse(self.voices)

    def post(self, url, headers=None, data=None, **kwargs):
        self.posts.append((url, headers, data.decode("utf-8")))
        if self.fail:
            raise requests.ConnectionError("down")
        return FakeResponse(content=b"ID3-audio")


def test_signatures_bind_text_and_language():
    signer = SpeechSigner("secret")
    sig = signer.sign("hi", "पानी दें")
    assert signer.verify("hi", "पानी दें", sig)
    assert not signer.verify("hi", "पानी दें!", sig)
    assert not signer.verify("ta", "पानी दें", sig)
    assert not signer.verify("hi", "पानी दें", None)
    assert not SpeechSigner("other").verify("hi", "पानी दें", sig)


def test_disabled_without_key_or_region():
    assert not AzureSpeech(key="", region="centralindia").enabled
    assert not AzureSpeech(key="k", region="").enabled
    assert AzureSpeech(key="", region="").synthesize("hello", "en-IN") is None


def test_synthesis_picks_a_voice_escapes_text_and_caches():
    session = FakeSession()
    speech = AzureSpeech(key="k", region="centralindia", session=session, cache=MemoryAudioCache())

    assert speech.synthesize("Rain <tomorrow> & wind", "hi-IN") == b"ID3-audio"
    assert speech.synthesize("Rain <tomorrow> & wind", "hi-IN") == b"ID3-audio"
    assert len(session.posts) == 1  # second time comes from the cache

    url, headers, ssml = session.posts[0]
    assert url == "https://centralindia.tts.speech.microsoft.com/cognitiveservices/v1"
    assert headers["Ocp-Apim-Subscription-Key"] == "k"
    assert "hi-IN-SwaraNeural" in ssml                  # female voice preferred
    assert "Rain &lt;tomorrow&gt; &amp; wind" in ssml   # text is escaped inside the SSML


def test_falls_back_to_known_voice_names_when_the_list_is_empty():
    session = FakeSession(voices=[])
    speech = AzureSpeech(key="k", region="centralindia", session=session)
    speech.synthesize("ନମସ୍କାର", "or-IN")
    assert "or-IN-SubhasiniNeural" in session.posts[0][2]


def test_failure_backs_off_and_long_text_is_refused():
    session = FakeSession(fail=True)
    speech = AzureSpeech(key="k", region="centralindia", session=session)
    assert speech.synthesize("one", "en-IN") is None
    assert speech.synthesize("two", "en-IN") is None
    assert len(session.posts) == 1
    assert speech.synthesize("x" * (MAX_SPEECH_CHARS + 1), "en-IN") is None


def test_memory_cache_keeps_the_newest_items():
    cache = MemoryAudioCache(max_items=2)
    cache.put("a", b"1")
    cache.put("b", b"2")
    cache.get("a")
    cache.put("c", b"3")
    assert cache.get("b") is None and cache.get("a") == b"1" and cache.get("c") == b"3"
