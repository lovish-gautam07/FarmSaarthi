from conftest import RABI_DAY, FakeTranslator
from translations import AzureTranslator, I18n


def test_translator_without_key_is_off():
    translator = AzureTranslator(key="")
    assert not translator.enabled
    assert translator.translate(["Hello"], "hi") is None
    assert translator.translate(["Hello"], "en") == ["Hello"]


def test_translator_caches_and_skips_repeats():
    translator = FakeTranslator()
    assert translator.translate(["Rice", "Wheat", "Rice"], "hi") == ["[hi] Rice", "[hi] Wheat", "[hi] Rice"]
    assert translator.translate(["Rice"], "hi") == ["[hi] Rice"]
    assert len(translator.requests) == 1
    assert translator.requests[0] == ("hi", ["Rice", "Wheat"])


def test_translator_splits_large_batches():
    translator = FakeTranslator()
    texts = [f"line {i}" for i in range(250)]
    assert len(translator.translate(texts, "ta")) == 250
    assert [len(chunk) for _, chunk in translator.requests] == [100, 100, 50]


def test_translator_failure_backs_off():
    translator = FakeTranslator(fail=True)
    assert translator.translate(["Rice"], "hi") is None
    assert translator.translate(["Wheat"], "hi") is None
    assert len(translator.requests) == 1  # second call skipped during back-off


def test_hand_written_text_wins_and_gaps_are_translated():
    i18n = I18n(FakeTranslator())
    hindi = i18n.bundle("hi")
    assert hindi["labels"]["title"] == "फार्मसारथी"                     # hand-written
    assert hindi["ui"]["photoTitle"] == "फसल की फोटो अपलोड करें"         # hand-written
    marathi = i18n.bundle("mr")
    assert marathi["labels"]["title"] == "फार्मसारथी"                   # hand-written label
    assert marathi["ui"]["photoTitle"] == "[mr] Upload a crop photo"   # filled by Translator
    assert marathi["complete"]


def test_without_translator_gaps_fall_back_to_english(i18n_offline):
    bundle = i18n_offline.bundle("mr")
    assert bundle["ui"]["photoTitle"] == "Upload a crop photo"
    assert not bundle["complete"]


def test_urdu_is_right_to_left_and_unknown_language_is_english(i18n_offline):
    assert i18n_offline.bundle("ur")["dir"] == "rtl"
    assert i18n_offline.bundle("klingon")["language"] == "en"


def test_every_language_has_all_labels(i18n_offline):
    english = set(i18n_offline.labels["en"])
    for language in i18n_offline.language_names:
        assert set(i18n_offline.labels[language]) == english, language


def test_analysis_text_is_translated(engine):
    i18n = I18n(FakeTranslator())
    result = engine.recommend("Ludhiana", "hi", "", "Punjab", today=RABI_DAY)
    assert i18n.translate_analysis(result, "hi")
    assert result["irrigation_advice"].startswith("[hi] ")
    assert all(alert.startswith("[hi] ") for alert in result["alerts"])
    assert all(crop["crop"].startswith("[hi] ") for crop in result["top_crops"])
    assert result["district"]["season"].startswith("[hi] ")
    # Machine-readable ids stay untouched.
    assert not result["top_crops"][0]["crop_id"].startswith("[")


def test_action_cards_and_notes_are_translated(engine):
    i18n = I18n(FakeTranslator())
    result = engine.recommend("Nagpur", "ta", "cotton", "Maharashtra", today=RABI_DAY)
    assert i18n.translate_analysis(result, "ta")
    assert all(a["title"].startswith("[ta] ") and a["text"].startswith("[ta] ") for a in result["actions"])
    assert all(n["text"].startswith("[ta] ") for n in result["interest_notes"])
