"""End-to-end offline capture: main, sweeps, queue, scheduler, retries and pointers.

Only requests.get and deterministic clocks are simulated. All implementation
functions remain real, and the shared fixture redirects output to tmp_path.
"""
from collections import Counter
from datetime import datetime, timedelta
import json
from threading import Event, Lock, local

import pytest
import requests
import fetch_odds_bet365 as fetch
from test_bet365_capture_sep11 import isolated_capture, NOW, quote, seed_full


def raw(fi):
    return {"FI": fi, "cards_fouls": {"sp": {"number_of_cards_in_match": {"odds": [
        {"header": "Over", "name": "4.5", "odds": "1.833"},
        {"header": "Under", "name": "4.5", "odds": "2.000"}]}}}}


def upcoming(page):
    # Twenty full pages, each with six genuine and44 excluded virtual entries.
    real = [{"id": f"{i:03}", "time": int(NOW.timestamp()) + 86400,
             "league": {"id": "1", "name": "Brazil Serie A"},
             "home": {"name": "Home"}, "away": {"name": "Away"}}
            for i in range((page - 1) * 6, page * 6)]
    virtual = [{"league": {"name": "Esoccer Battle"}} for _ in range(44)]
    return {"success": 1, "pager": {"total": 1000}, "results": real + virtual}


class Transport:
    def __init__(self, scenario):
        self.scenario = scenario
        self.lock = Lock()
        self.thread_clock = local()
        self.prematch_started = Event()
        self.last_lane_started = Event()
        self.second_started = Event()
        self.active = self.peak = 0
        self.calls = []
        self.attempts = Counter()
        self.expected_clock = {}
        self.hol_advanced = False

    def clock_class(self):
        transport = self
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                # Capture starts at14:55 BRT, receipts are14:56–14:59,
                # and delayed main-thread processing reads15:00 BRT.
                observed = getattr(transport.thread_clock, "received", None)
                if observed is None:
                    observed = NOW if transport.prematch_started.is_set() else NOW - timedelta(minutes=5)
                return observed.astimezone(tz) if tz else observed.replace(tzinfo=None)
        return Clock

    def reply(self, data, ids=(), observed=None):
        transport = self
        class Reply:
            status_code = 200
            def json(self):
                if observed is not None:
                    transport.thread_clock.received = observed
                    stamp = observed.astimezone(fetch.BRT).strftime("%Y-%m-%d %H:%M:%S")
                    with transport.lock:
                        transport.expected_clock.update({ident: stamp for ident in ids})
                return data
        return Reply()

    def get(self, url, *, params, timeout):
        path = url.removeprefix(fetch.BASE)
        ids = str(params.get("FI") or "").split(",") if params.get("FI") else []
        key = tuple(ids)
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append((path, key, timeout))
            self.attempts[key] += 1
            attempt = self.attempts[key]
        try:
            assert 0 < timeout <= 30
            if path == "/v1/bet365/upcoming":
                return self.reply(upcoming(int(params["page"])))
            assert path == "/v3/bet365/prematch"
            self.prematch_started.set()
            if ids[0] == "010":
                self.second_started.set()
            if ids[0] == "110":
                self.last_lane_started.set()
            if self.scenario == "slow_head" and len(ids) == 10 and ids[0] == "000":
                if attempt == 1:
                    self.hol_advanced = self.last_lane_started.wait(timeout=3)
                # Both batch attempts fail; all ten singles must be recovered.
                raise requests.exceptions.ReadTimeout("synthetic slow head")
            if self.scenario == "request_cap" and ids[0] == "000":
                assert self.second_started.wait(timeout=3)
            observed = NOW - timedelta(seconds=1 if len(ids) == 1 else 240 - int(ids[0]))
            return self.reply({"success": 1, "results": [raw(fi) for fi in ids]}, ids, observed)
        finally:
            with self.lock:
                self.active -= 1


def install(monkeypatch, scenario):
    transport = Transport(scenario)
    monkeypatch.setattr(fetch, "_token", lambda: "synthetic-token")
    monkeypatch.setattr(fetch.requests, "get", transport.get)
    monkeypatch.setattr(fetch, "datetime", transport.clock_class())
    return transport


def result_rows(paths):
    output = paths.odds / "bet365_2026-09-11_1455.jsonl"
    return [json.loads(line) for line in output.read_text().splitlines()]


def test_main_120_slow_head_refills_recovers_and_promotes_with_original_clocks(monkeypatch, isolated_capture):
    transport = install(monkeypatch, "slow_head")
    assert fetch.main() == 120
    assert transport.hol_advanced, "lane2 did not reach the last batch while head was pending"
    assert transport.active == 0 and transport.peak == 2
    assert fetch.N_REQ == len(transport.calls) == 43  #20 inventory +13 batches +10 singles
    assert fetch.N_REQ <= fetch.REQUEST_LIMIT == 90 and fetch.DEADLINE == 420
    assert not fetch.CAPTURE_INCOMPLETE
    assert transport.attempts[tuple(f"{i:03}" for i in range(10))] == 2
    assert all(transport.attempts[(f"{i:03}",)] == 1 for i in range(10))
    rows = result_rows(isolated_capture)
    assert [row["event_id"] for row in rows] == [f"{i:03}" for i in range(120)]
    assert all(row["captured_at"] == transport.expected_clock[row["event_id"]] for row in rows)
    assert {row["captured_at"] for row in rows} != {"2026-09-11 15:00:00"}
    assert all(row["parser_contract"] == 2 and not row.get("retained_from_previous") for row in rows)
    pointer = json.loads((isolated_capture.odds / "bet365_latest_full.json").read_text())
    assert pointer["n"] == 120 and not pointer.get("promotion_blocked")
    gate = json.loads(fetch.GATE_F.read_text())
    assert gate["last_full_n"] == 120 and gate["last_full_req"] == 43
    metrics = json.loads((isolated_capture.status / "bet365_discovery.json").read_text())["metrics"]
    assert metrics["fresh_events"] == metrics["attempted"] == metrics["succeeded"] == 120
    assert metrics["unattempted"] == 0 and not metrics["incomplete"]


def test_main_real_request_cap_stops_new_http_and_keeps_full_without_barrier_order(monkeypatch, isolated_capture):
    previous = [quote(f"{i:03}", age=3) for i in range(120)]
    original = seed_full(isolated_capture, previous)
    monkeypatch.setattr(fetch, "REQUEST_LIMIT", 22)  #20 inventory +2 real batches
    transport = install(monkeypatch, "request_cap")
    assert fetch.main() == 20
    assert fetch.N_REQ == len(transport.calls) == 22
    assert transport.active == 0 and transport.peak == 2
    prematch = [call for call in transport.calls if call[0] == "/v3/bet365/prematch"]
    assert len(prematch) == 2
    assert {fi for _, ids, _ in prematch for fi in ids} == {f"{i:03}" for i in range(20)}
    assert fetch.CAPTURE_INCOMPLETE
    assert json.loads((isolated_capture.odds / "bet365_latest_full.json").read_text()) == original
    assert not fetch.GATE_F.exists()
    rows = result_rows(isolated_capture)
    assert {row["event_id"] for row in rows} == {f"{i:03}" for i in range(20)}
    assert all(row["captured_at"] == transport.expected_clock[row["event_id"]] for row in rows)
    assert not any(row.get("retained_from_previous") for row in rows)
    metrics = json.loads((isolated_capture.status / "bet365_discovery.json").read_text())["metrics"]
    assert metrics["fresh_events"] == metrics["attempted"] == 20
    assert metrics["unattempted"] == 100 and metrics["incomplete"]
