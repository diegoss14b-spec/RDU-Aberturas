"""Portable CI regressions: request-local admission, retries and in-flight drain."""
import json
from threading import Barrier
from types import SimpleNamespace

import pytest
import requests
import fetch_odds_bet365 as fetch
from test_bet365_capture_sep11 import isolated_capture, NOW, quote, inventory, seed_full


@pytest.mark.parametrize("limit", ["requests", "deadline"])
def test_budget_before_first_http_has_explicit_false(monkeypatch, limit):
    if limit == "requests":
        monkeypatch.setattr(fetch, "N_REQ", fetch.REQUEST_LIMIT)
    else:
        monkeypatch.setattr(fetch, "DEADLINE", 1)
    with pytest.raises(fetch.CaptureBudgetExceeded) as caught:
        fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")
    assert caught.value.request_started is False


def test_budget_after_timeout_retry_has_explicit_true(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "REQUEST_LIMIT", 1)
    def http(*args, **kwargs):
        calls.append(1)
        raise requests.exceptions.ReadTimeout("synthetic")
    monkeypatch.setattr(fetch.requests, "get", http)
    with pytest.raises(fetch.CaptureBudgetExceeded) as caught:
        fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")
    assert caught.value.request_started is True
    assert len(calls) == fetch.N_REQ == 1


def test_previous_or_other_worker_request_cannot_set_local_started(monkeypatch):
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {"success": 1, "results": []}))
    fetch.get("/v1/bet365/upcoming", {"page": 1}, "fake")
    assert fetch.N_REQ == 1
    monkeypatch.setattr(fetch, "REQUEST_LIMIT", 1)
    with pytest.raises(fetch.CaptureBudgetExceeded) as caught:
        fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")
    assert caught.value.request_started is False


@pytest.mark.parametrize("started", [False, True, None])
def test_initial_budget_empty_only_when_proven_no_http_and_drains_other_worker(monkeypatch, started):
    monkeypatch.setattr(fetch, "FI_BATCH", 1)
    ready = Barrier(2)
    calls = []
    def batch(lote, token):
        ident = lote[0]["fi"]
        calls.append(ident)
        ready.wait(timeout=2)
        if ident == "0":
            error = fetch.CaptureBudgetExceeded("synthetic")
            if started is not None:
                error.request_started = started
            raise error
        found = fetch.ObservedRows()
        found[ident] = {"FI": ident}
        found.observed_at[ident] = "2026-09-11 14:58:00"
        return found, False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    events = [{"fi": str(i), "time": NOW.timestamp() + 3600} for i in range(4)]
    results = list(fetch.iter_batches(events, "fake"))
    assert set(calls) == {"0", "1"} and len(results) == 2
    assert results[0][0] == ([] if started is False else [events[0]])
    assert results[0][2:] == (True, ["0"])
    assert list(results[1][1]) == ["1"]
    assert results[1][1].observed_at["1"] == "2026-09-11 14:58:00"


def test_missing_single_budget_false_does_not_erase_batch_already_requested(monkeypatch):
    calls = []
    def get(path, params, token):
        calls.append(params["FI"])
        if len(calls) == 1:
            return {"results": [{"FI": str(i)} for i in range(9)]}
        error = fetch.CaptureBudgetExceeded("before missing-single HTTP")
        error.request_started = False
        raise error
    monkeypatch.setattr(fetch, "get", get)
    events = [{"fi": str(i), "time": NOW.timestamp() + 3600} for i in range(10)]
    found, exhausted, missing = fetch.fetch_batch(events, "fake")
    assert list(found) == list(map(str, range(9)))
    assert exhausted and missing == ["9"]
    assert set(found.observed_at) == set(found)


@pytest.mark.parametrize("started,attempted,unattempted", [(False, 10, 10), (True, 20, 0)])
def test_main_keeps_selected_tombstones_and_full_guard_with_correct_attempt_metrics(
        monkeypatch, isolated_capture, started, attempted, unattempted):
    previous = [quote(f"{i:02}", age=3) for i in range(25)]
    old_pointer = seed_full(isolated_capture, previous)
    candidates = inventory(previous)
    monkeypatch.setattr(fetch, "MAX_EVENTS", 20)
    monkeypatch.setattr(fetch, "FI_BATCH", 10)
    monkeypatch.setattr(fetch, "_token", lambda: "fake")
    monkeypatch.setattr(fetch, "fetch_inventory", lambda *a: (candidates, 21))
    ready = Barrier(2)
    raw = {"cards_fouls": {"sp": {"number_of_cards_in_match": {"odds": [
        {"header": "Over", "name": "4.5", "odds": "1.833"},
        {"header": "Under", "name": "4.5", "odds": "2.000"}]}}}}
    def batch(lote, token):
        ready.wait(timeout=2)
        if lote[0]["fi"] == "00":
            error = fetch.CaptureBudgetExceeded("synthetic")
            error.request_started = started
            raise error
        found = fetch.ObservedRows()
        for event in lote:
            found[event["fi"]] = dict(raw, FI=event["fi"])
            found.observed_at[event["fi"]] = "2026-09-11 14:59:00"
        return found, False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    assert fetch.main() == 15
    metrics = json.loads((isolated_capture.status / "bet365_discovery.json").read_text())["metrics"]
    assert metrics["attempted"] == attempted and metrics["unattempted"] == unattempted
    assert metrics["fresh_events"] == 10 and metrics["retained_events"] == 5
    assert fetch.CAPTURE_INCOMPLETE and metrics["incomplete"]
    assert json.loads((isolated_capture.odds / "bet365_latest_full.json").read_text()) == old_pointer
    rows = [json.loads(line) for line in (isolated_capture.odds / "bet365_2026-09-11_1500.jsonl").read_text().splitlines()]
    assert {row["event_id"] for row in rows} == {f"{i:02}" for i in range(10, 25)}
    assert {row["event_id"] for row in rows if row.get("retained_from_previous")} == {f"{i:02}" for i in range(20, 25)}

