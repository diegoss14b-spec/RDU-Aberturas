"""Portable CI tests: real get/fetch_batch; only transport/clocks are mocked.

Requires the minimal retry-profile delta. No research module or live fixture.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import fetch_odds_bet365 as fetch
from test_bet365_capture_sep11 import isolated_capture, NOW


@pytest.mark.parametrize("fi_count,attempts", [(0, 3), (1, 3), (2, 2), (5, 2), (10, 2)])
@pytest.mark.parametrize("failure", ["timeout", "HTTP502", "HTTP429"])
def test_attempt_profile_by_fi_count_and_no_final_backoff(monkeypatch, fi_count, attempts, failure):
    path = "/v3/bet365/prematch" if fi_count else "/v1/bet365/upcoming"
    params = {"FI": ",".join(map(str, range(fi_count)))} if fi_count else {"page": 1}
    calls, sleeps = [], []
    monkeypatch.setattr(fetch.time, "sleep", sleeps.append)
    def http(*args, **kwargs):
        calls.append(kwargs["timeout"])
        if failure == "timeout":
            raise fetch.requests.exceptions.ReadTimeout("synthetic")
        return SimpleNamespace(status_code=int(failure[4:]))
    monkeypatch.setattr(fetch.requests, "get", http)
    assert fetch.get(path, params, "fake") is None
    assert calls == [30]*attempts and fetch.N_REQ == attempts
    assert sleeps == ([2*(i+1) for i in range(attempts-1)] if failure == "HTTP429"
                      else [1.0]*(attempts-1))


def test_second_batch_attempt_success_recovers_only_still_missing_and_keeps_clocks(monkeypatch):
    calls = []
    current = {"seconds": 0}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = NOW + timedelta(seconds=current["seconds"])
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(fetch, "datetime", Clock)
    def http(url, params, **kwargs):
        calls.append(params["FI"])
        if len(calls) == 1:
            raise fetch.requests.exceptions.ReadTimeout("synthetic first batch timeout")
        ids = range(9) if len(calls) == 2 else [9]
        current["seconds"] = 4 if len(calls) == 2 else 8
        return SimpleNamespace(status_code=200, json=lambda: {
            "success": 1, "results": [{"FI": str(i)} for i in ids]})
    monkeypatch.setattr(fetch.requests, "get", http)
    found, exhausted, missing = fetch.fetch_batch([{"fi": str(i)} for i in range(10)], "fake")
    full = ",".join(map(str, range(10)))
    assert calls == [full, full, "9"] and fetch.N_REQ == 3
    assert set(found) == set(map(str, range(10))) and not exhausted and not missing
    assert {found.observed_at[str(i)] for i in range(9)} == {"2026-09-11 15:00:04"}
    assert found.observed_at["9"] == "2026-09-11 15:00:08"


def test_two_batch_timeouts_then_each_missing_fi_requested_once(monkeypatch):
    calls = []
    def http(url, params, **kwargs):
        calls.append(params["FI"])
        if "," in params["FI"]:
            raise fetch.requests.exceptions.ReadTimeout("synthetic batch-only timeout")
        return SimpleNamespace(status_code=200, json=lambda: {
            "success": 1, "results": [{"FI": params["FI"]}]})
    monkeypatch.setattr(fetch.requests, "get", http)
    found, exhausted, missing = fetch.fetch_batch([{"fi": str(i)} for i in range(10)], "fake")
    full = ",".join(map(str, range(10)))
    assert calls == [full, full]+list(map(str, range(10)))
    assert len(found) == 10 and not exhausted and not missing and fetch.N_REQ == 12


def test_quota_during_missing_recovery_keeps_nine_original_observations(monkeypatch):
    monkeypatch.setattr(fetch, "REQUEST_LIMIT", 1)
    calls = []
    def http(url, params, **kwargs):
        calls.append(params["FI"])
        return SimpleNamespace(status_code=200, json=lambda: {
            "success": 1, "results": [{"FI": str(i)} for i in range(9)]})
    monkeypatch.setattr(fetch.requests, "get", http)
    found, exhausted, missing = fetch.fetch_batch([{"fi": str(i)} for i in range(10)], "fake")
    assert len(calls) == fetch.N_REQ == 1
    assert set(found) == set(map(str, range(9))) and exhausted and missing == ["9"]
    assert set(found.observed_at) == set(found)
    assert set(found.observed_at.values()) == {"2026-09-11 15:00:00"}


@pytest.mark.parametrize("remaining,started", [(0, False), (1, True)])
def test_quota_admission_preserves_local_request_started(monkeypatch, remaining, started):
    monkeypatch.setattr(fetch, "REQUEST_LIMIT", 1)
    monkeypatch.setattr(fetch, "N_REQ", 1-remaining)
    calls = []
    def http(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=502)
    monkeypatch.setattr(fetch.requests, "get", http)
    with pytest.raises(fetch.CaptureBudgetExceeded) as caught:
        fetch.get("/v3/bet365/prematch", {"FI": "1,2"}, "fake")
    assert caught.value.request_started is started
    assert len(calls) == remaining and fetch.N_REQ == 1


def test_missing_transport_is_not_silently_marked_resolved(monkeypatch):
    def http(url, params, **kwargs):
        if "," in params["FI"]:
            return SimpleNamespace(status_code=200, json=lambda: {
                "success": 1, "results": [{"FI": "0"}]})
        raise fetch.requests.exceptions.ReadTimeout("synthetic missing FI")
    monkeypatch.setattr(fetch.requests, "get", http)
    found, exhausted, missing = fetch.fetch_batch([{"fi": "0"}, {"fi": "1"}], "fake")
    assert list(found) == ["0"] and not exhausted and missing == ["1"]
    assert fetch.N_REQ == 4  # one partial batch + three single attempts


def test_workload_and_global_request_budget_are_unchanged():
    assert fetch.MAX_EVENTS == 120 and fetch.FI_BATCH == 10 and fetch.REQUEST_LIMIT == 90
