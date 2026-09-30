"""Network-free source diagnostics, rejection and previous-pointer regression tests."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import capture_common as cc
import fetch_odds_sportingbet as sb


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(sb, "PROX", None)
    monkeypatch.setattr(sb, "_TRANSPORT", {"requests": 0, "http_statuses": {}, "routes": {},
                         "last_error": None, "last_failure_kind": None, "last_path": None,
                         "access_refused": False})
    monkeypatch.setattr(sb, "_LISTING_FAILURES", [])
    monkeypatch.setattr(sb.time, "sleep", Mock())
    monkeypatch.setattr(sb.creq, "get", Mock(side_effect=AssertionError("unexpected network")))


def response(status=200, data=None, malformed=False):
    result = Mock(status_code=status)
    result.json = Mock(side_effect=ValueError("invalid") if malformed else None,
                       return_value=data)
    return result


@pytest.mark.parametrize("status", [401, 403, 407, 429])
def test_source_refusal_is_not_empty_list_or_proxy_retry(status, monkeypatch):
    get = Mock(return_value=response(status))
    monkeypatch.setattr(sb.creq, "get", get)
    assert sb.fetch_fixtures() == []
    assert f"HTTP {status}" in sb.listing_error()
    assert not sb.route_retry_allowed()
    assert get.call_count == 1
    assert sb._TRANSPORT["http_statuses"] == {str(status): 1}
    assert sb.get("https://book.test/another") is None
    assert get.call_count == 1


def test_valid_empty_response_has_an_explicit_distinct_reason(monkeypatch):
    monkeypatch.setattr(sb.creq, "get", Mock(return_value=response(data={"fixtures": []})))
    assert sb.fetch_fixtures() == []
    assert "HTTP 200: API retornou lista fixtures vazia" in sb.listing_error()
    assert sb._LISTING_FAILURES[-1]["kind"] == "Empty"
    assert not sb.route_retry_allowed()


@pytest.mark.parametrize("data", [{"changedSchema": []}, [], {"fixtures": "oops"}])
def test_changed_fixture_schema_cannot_be_reported_as_transport(data, monkeypatch):
    monkeypatch.setattr(sb.creq, "get", Mock(return_value=response(data=data)))
    assert sb.fetch_fixtures() == []
    assert sb._LISTING_FAILURES[-1]["kind"] == "Parse"
    assert not sb.route_retry_allowed()


def test_malformed_json_is_not_a_valid_empty_sports_calendar(monkeypatch):
    monkeypatch.setattr(sb.creq, "get", Mock(return_value=response(malformed=True)))
    assert sb.fetch_fixtures() == []
    assert "sem JSON válido" in sb.listing_error()


def test_valid_json_with_whitespace_uses_decoder_not_first_byte(monkeypatch):
    result = response(data={"fixtures": [{"id": "123"}]})
    result.content = b' \n {"fixtures":[{"id":"123"}]}'
    monkeypatch.setattr(sb.creq, "get", Mock(return_value=result))
    assert sb.fetch_fixtures() == [{"id": "123"}]
    assert not sb._LISTING_FAILURES


def test_failure_after_first_page_cannot_promote_truncated_listing(monkeypatch):
    monkeypatch.setattr(sb, "TAKE", 2)
    get = Mock(side_effect=[response(data={"fixtures": [{"id": "a"}, {"id": "b"}]}),
                            response(403)])
    monkeypatch.setattr(sb.creq, "get", get)
    assert sb.fetch_fixtures() == []
    assert get.call_count == 2


def test_transport_failure_is_distinct_and_contains_no_query_secrets(monkeypatch):
    get = Mock(side_effect=TimeoutError("private URL token=do-not-log"))
    monkeypatch.setattr(sb.creq, "get", get)
    assert sb.fetch_fixtures() == []
    assert sb.route_retry_allowed()
    assert sb._TRANSPORT["requests"] == 3
    assert sb._TRANSPORT["last_path"] == "/cds-api/bettingoffer/fixtures"
    assert "do-not-log" not in json.dumps(sb._TRANSPORT)


def test_status_preserves_diagnostics_and_does_not_touch_prior_pointer(tmp_path, monkeypatch):
    status = tmp_path / "_status"
    status.mkdir()
    pointer = tmp_path / "sportingbet_latest_full.json"
    pointer.write_text('{"file":"previous.jsonl","at":"2026-09-27T05:45:00Z"}')
    before = pointer.read_bytes()
    monkeypatch.setattr(cc, "STATUS_DIR", status)
    def finish(*args, **kwargs):
        (status / "sportingbet.json").write_text(json.dumps({"ok": False, "error": kwargs["error"]}))
        return 2
    monkeypatch.setattr(cc, "finish", finish)
    sb._record_response("direct", "/cds-api/bettingoffer/fixtures", 403, "HTTP 403", "AccessDenied")
    sb._listing_failure("HTTP 403", "AccessDenied")
    assert sb.finish_capture("sportingbet", 0, 8, error=sb.listing_error()) == 2
    result = json.loads((status / "sportingbet.json").read_text())
    assert result["transport"]["http_statuses"] == {"403": 1}
    assert result["listing_failures"][0]["kind"] == "AccessDenied"
    assert pointer.read_bytes() == before
