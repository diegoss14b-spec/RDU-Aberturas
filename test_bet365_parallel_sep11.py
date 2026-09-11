"""CI regressions for bounded concurrency; all HTTP, clocks and files isolated."""
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from datetime import datetime, timedelta
import json
from threading import Barrier, Event, Lock
from time import sleep as real_sleep
from types import SimpleNamespace

import pytest
import requests
import fetch_odds_bet365 as fetch
from test_bet365_capture_sep11 import isolated_capture, NOW, quote, inventory


def _wait_for_head_done(pending, return_when):
    # STOP is asserted after the budget future is known complete. A bare
    # Barrier starts workers together but cannot order their completion.
    futures_wait([next(iter(pending))])
    return futures_wait(pending, return_when=return_when)


def response(rows=()):
    return SimpleNamespace(status_code=200, json=lambda: {"success": 1, "results": list(rows)})


def test_global_http_slots_cover_both_inventory_and_prematch(monkeypatch):
    state = {"active": 0, "peak": 0}
    lock = Lock()
    def http(url, **kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        real_sleep(0.01)
        with lock:
            state["active"] -= 1
        return response()
    monkeypatch.setattr(fetch.requests, "get", http)
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(fetch.get, "/v1/bet365/upcoming" if i % 2 else "/v3/bet365/prematch",
                            {"FI": "a,b"} if i % 2 == 0 else {"page": i}, "not-a-token") for i in range(8)]
        assert all(job.result()["success"] == 1 for job in jobs)
    assert state == {"active": 0, "peak": 2}
    assert fetch.N_REQ == 8


def test_request_limit_is_atomic_across_workers(monkeypatch):
    monkeypatch.setattr(fetch, "REQUEST_LIMIT", 5)
    count = []
    def http(*args, **kwargs):
        count.append(1)
        real_sleep(0.005)
        return response()
    monkeypatch.setattr(fetch.requests, "get", http)
    def call():
        try:
            fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")
            return "ok"
        except fetch.CaptureBudgetExceeded:
            return "budget"
    with ThreadPoolExecutor(max_workers=12) as pool:
        answers = list(pool.map(lambda _: call(), range(12)))
    assert answers.count("ok") == len(count) == fetch.N_REQ == 5
    assert answers.count("budget") == 7


def test_deadline_rechecked_after_waiting_for_http_slot(monkeypatch):
    clock = {"now": 0}
    released = []
    class Slots:
        def acquire(self, timeout):
            assert timeout == 10
            clock["now"] = 9
            return True
        def release(self):
            released.append(True)
    monkeypatch.setattr(fetch, "HTTP_SLOTS", Slots())
    monkeypatch.setattr(fetch, "DEADLINE", 10)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: clock["now"])
    with pytest.raises(fetch.CaptureBudgetExceeded):
        fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")
    assert fetch.N_REQ == 0 and released == [True]


def test_timeout_is_clipped_to_time_remaining(monkeypatch):
    monkeypatch.setattr(fetch, "DEADLINE", 8)
    calls = []
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: calls.append(k["timeout"]) or response())
    fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")
    assert calls == [8]


def test_timeout_retry_diagnostics_never_contain_secret(monkeypatch, capsys):
    calls = []
    def http(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ReadTimeout("https://host/?token=PRIVATE_SENTINEL")
        return response()
    monkeypatch.setattr(fetch.requests, "get", http)
    assert fetch.get("/v3/bet365/prematch", {"FI": "a,b"}, "PRIVATE_SENTINEL")["success"] == 1
    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    assert "result=ReadTimeout" in output and "FI_count=2" in output
    assert "elapsed_s=" in output and fetch.N_REQ == 2


def test_inventory_sweeps_concurrent_and_deep_cursor_independent(monkeypatch):
    started = Barrier(2)
    def sweep(token, now, pages, start_page=1, *, return_cursor=False):
        assert pages == 10 and return_cursor
        started.wait(timeout=2)
        return [{"page": start_page}], 11 if start_page == 1 else 31
    monkeypatch.setattr(fetch, "_sweep_upcoming", sweep)
    events, cursor = fetch.fetch_inventory("fake", NOW, 21)
    assert events == [{"page": 1}, {"page": 21}]
    assert cursor == 31


def test_sweep_list_compatibility_and_explicit_cursor(monkeypatch):
    monkeypatch.setattr(fetch, "get", lambda *a, **k: {"success": 1, "pager": {"total": 2000}, "results": []})
    assert fetch._sweep_upcoming("fake", NOW, 2, start_page=21) == []
    assert fetch._sweep_upcoming("fake", NOW, 2, start_page=21, return_cursor=True) == ([], 23)
    monkeypatch.setattr(fetch, "get", lambda *a, **k: {"success": 1, "pager": {"total": 50}, "results": []})
    assert fetch._sweep_upcoming("fake", NOW, 10, return_cursor=True) == ([], 11)


def events(n):
    return [{"fi": str(i), "time": NOW.timestamp() + 86400} for i in range(n)]


def observed(ident, stamp):
    rows = fetch.ObservedRows()
    rows[ident] = {"FI": ident}
    rows.observed_at[ident] = stamp
    return rows


def test_batches_are_ordered_with_two_inflight_and_original_clock(monkeypatch):
    monkeypatch.setattr(fetch, "FI_BATCH", 1)
    second_done = Event()
    active = {"count": 0, "peak": 0}
    lock = Lock()
    def batch(lote, token):
        ident = lote[0]["fi"]
        with lock:
            active["count"] += 1
            active["peak"] = max(active["peak"], active["count"])
        if ident == "0":
            assert second_done.wait(timeout=2)
        elif ident == "1":
            second_done.set()
        with lock:
            active["count"] -= 1
        return observed(ident, f"2026-09-11 14:00:0{ident}"), False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    result = list(fetch.iter_batches(events(4), "fake"))
    assert [row[0][0]["fi"] for row in result] == ["0", "1", "2", "3"]
    assert result[1][1].observed_at["1"] == "2026-09-11 14:00:01"
    assert active["peak"] == 2


def test_budget_drains_other_inflight_success_without_starting_more(monkeypatch):
    monkeypatch.setattr(fetch, "wait", _wait_for_head_done)
    monkeypatch.setattr(fetch, "FI_BATCH", 1)
    started = Barrier(2)
    calls = []
    def batch(lote, token):
        ident = lote[0]["fi"]
        calls.append(ident)
        started.wait(timeout=2)
        if ident == "0":
            raise fetch.CaptureBudgetExceeded("synthetic budget")
        return observed(ident, "2026-09-11 14:00:00"), False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    result = list(fetch.iter_batches(events(4), "fake"))
    assert set(calls) == {"0", "1"}
    assert result[0][2:] == (True, ["0"])
    assert list(result[1][1]) == ["1"]


def test_partial_batch_preserved_and_stops_new_submissions(monkeypatch):
    monkeypatch.setattr(fetch, "wait", _wait_for_head_done)
    monkeypatch.setattr(fetch, "FI_BATCH", 2)
    started = Barrier(2)
    def batch(lote, token):
        started.wait(timeout=2)
        ident = lote[0]["fi"]
        return observed(ident, "2026-09-11 14:00:00"), ident == "0", ["1"] if ident == "0" else []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    result = list(fetch.iter_batches(events(8), "fake"))
    assert len(result) == 2 and list(result[0][1]) == ["0"]
    assert list(result[1][1]) == ["2"]


def test_batch_and_single_recovery_receive_distinct_source_clocks(monkeypatch):
    calls = []
    current = {"seconds": 1}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            now = NOW + timedelta(seconds=current["seconds"])
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    def get(path, params, token):
        calls.append(params["FI"])
        current["seconds"] = 1 if len(calls) == 1 else 5
        return {"results": [{"FI": str(i)} for i in (range(9) if len(calls) == 1 else [9])]}
    monkeypatch.setattr(fetch, "datetime", Clock)
    monkeypatch.setattr(fetch, "get", get)
    found, exhausted, missing = fetch.fetch_batch(events(10), "fake")
    assert not exhausted and not missing and len(found) == 10
    assert set(found.observed_at[str(i)] for i in range(9)) == {"2026-09-11 15:00:01"}
    assert found.observed_at["9"] == "2026-09-11 15:00:05"


def test_main_writes_receipt_clock_not_delayed_processing_clock(monkeypatch, isolated_capture):
    candidates = inventory([quote(str(i)) for i in range(5)])
    monkeypatch.setattr(fetch, "_token", lambda: "fake")
    monkeypatch.setattr(fetch, "fetch_inventory", lambda *a: (candidates, 21))
    raw_market = {"cards_fouls": {"sp": {"number_of_cards_in_match": {"odds": [
        {"header": "Over", "name": "4.5", "odds": "1.833"},
        {"header": "Under", "name": "4.5", "odds": "2.000"}]}}}}
    def batch(lote, token):
        found = fetch.ObservedRows()
        for event in lote:
            found[event["fi"]] = dict(raw_market, FI=event["fi"])
            found.observed_at[event["fi"]] = "2026-09-11 14:58:00"
        return found, False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    assert fetch.main() == 5
    output = isolated_capture.odds / "bet365_2026-09-11_1500.jsonl"
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert {row["captured_at"] for row in rows} == {"2026-09-11 14:58:00"}
    assert not fetch.CAPTURE_INCOMPLETE
