import requests

from conftest import KCC_ROWS
from datagov import DataGovClient, MANDI_RESOURCE, rank_answers


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_client_sends_filters_and_sort_and_caches():
    session = Session([FakeResponse({"status": "ok", "records": [{"a": 1}]})])
    client = DataGovClient("key", session=session)
    rows = client.records(MANDI_RESOURCE, {"state.keyword": "Punjab"}, limit=5, sort={"arrival_date": "desc"})
    assert rows == [{"a": 1}]
    assert client.records(MANDI_RESOURCE, {"state.keyword": "Punjab"}, limit=5, sort={"arrival_date": "desc"}) == [{"a": 1}]
    assert len(session.calls) == 1
    url, params = session.calls[0]
    assert url.endswith(MANDI_RESOURCE)
    assert params["filters[state.keyword]"] == "Punjab" and params["sort[arrival_date]"] == "desc" and params["limit"] == 5


def test_client_backs_off_after_errors():
    session = Session([requests.ConnectionError("down")])
    client = DataGovClient("key", session=session)
    assert client.records(MANDI_RESOURCE, {"x": "1"}, limit=1) is None
    assert client.records(MANDI_RESOURCE, {"x": "2"}, limit=1) is None
    assert len(session.calls) == 1


def test_client_treats_rate_limit_and_bad_status_as_unavailable():
    assert DataGovClient("key", session=Session([FakeResponse({}, status=429)])).records(MANDI_RESOURCE, {}, 1) is None
    assert DataGovClient("key", session=Session([FakeResponse({"status": "error"})])).records(MANDI_RESOURCE, {}, 1) is None
    assert DataGovClient("").records(MANDI_RESOURCE, {}, 1) is None


def test_ranking_skips_weather_and_duplicates():
    rows = KCC_ROWS + [dict(KCC_ROWS[0], CreatedOn="2025-01-01T00:00:00")]
    answers = rank_answers("yellow rust wheat", rows, district="Ludhiana")
    assert [a["question"] for a in answers] == ["How to control yellow rust in wheat?"]
    assert answers[0]["date"] == "2026-01-10"


def test_ranking_returns_nothing_for_unrelated_questions():
    assert rank_answers("tractor loan interest", KCC_ROWS) == []
    assert rank_answers("the and of", KCC_ROWS) == []
    assert rank_answers("urea", []) == []


def test_kcc_falls_back_to_the_whole_state_when_crop_has_no_rows():
    from conftest import FakeDataGov
    from datagov import KccSearch

    client = FakeDataGov()
    search = KccSearch(client, names={"crops": {"maize": {"kcc": ["Maize (Makka)"]}}, "kcc_states": {}})
    result = search.search("urea dose for wheat", "Punjab", crop_id="maize")
    assert result["status"] == "ok" and result["answers"][0]["question"].startswith("Information regarding urea")
    assert [filters.get("Crop") for _, filters in client.calls] == ["Maize (Makka)", None]


def test_client_names_itself_so_data_gov_in_answers():
    from datagov import USER_AGENT

    client = DataGovClient("key")
    assert client.session.headers["User-Agent"] == USER_AGENT
    assert "python-requests" not in client.session.headers["User-Agent"]
