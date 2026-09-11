"""Offline regressions for work-conserving, ordered BetsAPI batch scheduling."""
from concurrent.futures import wait as real_wait
from threading import Event, Lock

import fetch_odds_bet365 as fetch
from test_bet365_capture_sep11 import isolated_capture, NOW


def events(count):
    return [{"fi": str(i), "time": NOW.timestamp() + 86400} for i in range(count)]


def success(ident):
    rows = fetch.ObservedRows()
    rows[ident] = {"FI": ident}
    rows.observed_at[ident] = f"source-clock-{ident}"
    return rows, False, []


def test_third_batch_starts_before_first_finishes_without_reordering_or_freshening(monkeypatch):
    monkeypatch.setattr(fetch, "FI_BATCH", 1)
    third_started = Event()
    lock = Lock()
    state = {"active": 0, "peak": 0}
    def batch(lote, token):
        ident = lote[0]["fi"]
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            if ident == "0":
                assert third_started.wait(timeout=3), "head-of-line blocker kept lane2 idle"
            elif ident == "2":
                third_started.set()
            return success(ident)
        finally:
            with lock:
                state["active"] -= 1
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    results = list(fetch.iter_batches(events(6), "fake"))
    assert [lote[0]["fi"] for lote, _, _, _ in results] == list(map(str, range(6)))
    assert state["active"] == 0 and state["peak"] == 2
    assert [rows.observed_at[str(i)] for i, (_, rows, _, _) in enumerate(results)] == [f"source-clock-{i}" for i in range(6)]


def test_bounded_120_event_selection_can_buffer_behind_one_slow_head(monkeypatch):
    monkeypatch.setattr(fetch, "FI_BATCH", 10)
    last_started = Event()
    calls = []
    def batch(lote, token):
        ident = lote[0]["fi"]
        calls.append(ident)
        if ident == "0":
            assert last_started.wait(timeout=3), "fast lane did not advance through remaining batches"
        elif ident == "110":
            last_started.set()
        rows = fetch.ObservedRows()
        for event in lote:
            rows[event["fi"]] = {"FI": event["fi"]}
            rows.observed_at[event["fi"]] = f"source-clock-{event['fi']}"
        return rows, False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    results = list(fetch.iter_batches(events(120), "fake"))
    assert len(calls) == len(results) == 12
    assert [lote[0]["fi"] for lote, _, _, _ in results] == list(map(str, range(0, 120, 10)))
    assert sum(len(rows) for _, rows, _, _ in results) == 120
    assert len({ident for _, rows, _, _ in results for ident in rows}) == 120


def test_observed_late_budget_stops_refill_and_drains_head_plus_buffer(monkeypatch):
    monkeypatch.setattr(fetch, "FI_BATCH", 1)
    budget_observed = Event()
    calls = []
    def batch(lote, token):
        ident = lote[0]["fi"]
        calls.append(ident)
        if ident == "0":
            assert budget_observed.wait(timeout=3)
        elif ident == "2":
            error = fetch.CaptureBudgetExceeded("synthetic admission refusal")
            error.request_started = False
            raise error
        return success(ident)
    def wait_until_any(pending, return_when):
        done, others = real_wait(pending, return_when=return_when)
        if any(isinstance(future.exception(), fetch.CaptureBudgetExceeded) for future in done):
            budget_observed.set()
        return done, others
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    monkeypatch.setattr(fetch, "wait", wait_until_any)
    results = list(fetch.iter_batches(events(5), "fake"))
    assert set(calls) == {"0", "1", "2"}
    assert len(results) == 3
    assert results[0][1].observed_at["0"] == "source-clock-0"
    assert results[1][1].observed_at["1"] == "source-clock-1"
    assert results[2] == ([], {}, True, ["2"])


def test_transport_missing_does_not_stop_other_batches_or_disappear(monkeypatch):
    monkeypatch.setattr(fetch, "FI_BATCH", 1)
    third_started = Event()
    def batch(lote, token):
        ident = lote[0]["fi"]
        if ident == "0":
            assert third_started.wait(timeout=3)
        if ident == "1":
            return fetch.ObservedRows(), False, ["1"]
        if ident == "2":
            third_started.set()
        return success(ident)
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    results = list(fetch.iter_batches(events(4), "fake"))
    assert [lote[0]["fi"] for lote, _, _, _ in results] == ["0", "1", "2", "3"]
    assert results[1][2:] == (False, ["1"])
    assert sum(len(rows) for _, rows, _, _ in results) == 3
