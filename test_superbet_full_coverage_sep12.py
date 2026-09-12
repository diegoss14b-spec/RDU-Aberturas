"""Superbet: nenhum teto por quantidade e publicação só após cobertura completa."""
import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import capture_common as cc
import fetch_odds_superbet as fetch


def event(eid, kickoff, *, foul=True):
    odds = [{"status": "active", "marketName": "Total de Faltas", "name": side + " de 23.5", "price": 1.9}
            for side in ("Mais", "Menos")] if foul else []
    return {"eventId": eid, "unixDateMillis": int(kickoff.timestamp() * 1000),
            "matchName": f"Time {eid}·Visitante", "tournamentId": 1, "odds": odds}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("ODDS_WINDOW_H", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(fetch, "OUTDIR", tmp_path)
    monkeypatch.setattr(fetch, "MIN_EVENTS", 1)
    monkeypatch.setattr(fetch.random, "uniform", lambda *_: 0)
    monkeypatch.setattr(cc, "ODDS_DIR", tmp_path)
    monkeypatch.setattr(cc, "FULL_SNAPSHOT_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(cc, "STATUS_DIR", tmp_path / "_status")
    return tmp_path


def install_source(monkeypatch, events, *, failures=None):
    by_id = {str(e["eventId"]): e for e in events}
    calls = []
    lock = threading.Lock()

    def get(url, **kwargs):
        if url.endswith("/struct"):
            return {"data": [{"id": 1, "localNames": {"pt-BR": "Liga teste"}}]}
        if "/events/by-date?" in url:
            # A listagem só traz mercados principais: não vale excluir por inline.
            return {"data": [dict(e, odds=[]) for e in events]}
        eid = url.rsplit("/", 1)[-1]
        with lock:
            calls.append(eid)
        if failures and eid in failures:
            value = failures[eid]
            if isinstance(value, Exception):
                raise value
            return value
        return {"data": [by_id[eid]]}

    monkeypatch.setattr(fetch, "get", get)
    return calls


def diag(path):
    return json.loads((path / "_status" / "superbet_diag.json").read_text())


def latest_rows(path, *, full=True):
    pointer = json.loads((path / ("superbet_latest_full.json" if full else "superbet_latest.json")).read_text())
    return pointer, [json.loads(line) for line in (path / pointer["file"]).read_text().splitlines()]


def seed_full(path):
    source = path / "seed.jsonl"
    source.write_text(json.dumps({"event_id": "old", "mercados": {"Faltas": [1]}}) + "\n")
    cc.write_odds_latest("superbet", source.name, 1, min_events=1)
    return (path / "superbet_latest_full.json").read_bytes(), (path / "superbet_latest.json").read_bytes()


def test_inventory_over_900_reaches_last_late_foul(monkeypatch, isolated):
    now = datetime.now(timezone.utc)
    events = [event(i, now + timedelta(hours=1, seconds=i), foul=i in (900, 1200)) for i in range(1, 1201)]
    calls = install_source(monkeypatch, events)
    assert fetch.main() == 2
    assert len(calls) == len(set(calls)) == 1200
    pointer, rows = latest_rows(isolated)
    assert [r["event_id"] for r in rows] == [900, 1200]
    assert rows[-1]["mercados"]["Faltas"] == [{"linha": 23.5, "over": 1.9, "under": 1.9}]
    assert pointer["n"] == 2
    progress = diag(isolated)
    assert progress["complete"] and progress["n_ok"] == 1200
    assert progress["n_unattempted"] == progress["n_failed"] == 0


def test_selection_deduplicates_and_applies_only_time_window():
    now = datetime(2026, 9, 12, 6, tzinfo=timezone.utc)
    events = [event(1, now), event("1", now), event(2, now + timedelta(hours=30)),
              event(3, now + timedelta(hours=31)), event(4, now - timedelta(seconds=1)),
              {"eventId": 5, "unixDateMillis": "invalid", "utcDate": (now + timedelta(hours=2)).isoformat()},
              {"eventId": 6, "matchDate": (now + timedelta(hours=3)).isoformat()},
              {"eventId": 7, "matchTimestamp": 1},
              {"eventId": 8, "unixDateMillis": str(int((now + timedelta(hours=4)).timestamp() * 1000))}]
    assert [e["eventId"] for e in fetch.select_events(events, now, 30)] == [1, 5, 6, 8, 2, 7]
    assert [e["eventId"] for e in fetch.select_events(events, now, 30, 2)] == [1, 5, 7]


@pytest.mark.parametrize("horizon,close", [(0, None), (-1, None), (float("inf"), None),
                                           (float("nan"), None), (30, float("inf")), (30, 0)])
def test_invalid_horizon_cannot_create_a_healthy_empty_inventory(horizon, close):
    with pytest.raises(fetch.CaptureIncomplete):
        fetch.select_events([], datetime.now(timezone.utc), horizon, close)


@pytest.mark.parametrize("bad", [None, {}, {"data": []}, {"data": [None]}, {"data": [{"eventId": "other", "odds": []}]}])
def test_bad_detail_never_promotes_partial(monkeypatch, isolated, bad):
    before = seed_full(isolated)
    now = datetime.now(timezone.utc)
    events = [event(1, now + timedelta(hours=1)), event(2, now + timedelta(hours=2))]
    install_source(monkeypatch, events, failures={"2": bad})
    with pytest.raises(fetch.CaptureIncomplete, match="captura parcial Superbet"):
        fetch.main()
    assert before == ((isolated / "superbet_latest_full.json").read_bytes(), (isolated / "superbet_latest.json").read_bytes())
    progress = diag(isolated)
    assert not progress["complete"] and progress["n_failed"] == 1 and progress["n_out"] == 1


def test_404_is_explicit_terminal_unavailable(monkeypatch, isolated):
    now = datetime.now(timezone.utc)
    install_source(monkeypatch, [event(1, now + timedelta(hours=1)), event(2, now + timedelta(hours=2))],
                   failures={"2": fetch.NOT_FOUND})
    assert fetch.main() == 1
    progress = diag(isolated)
    assert progress["complete"] and progress["n_unavailable"] == 1
    assert progress["unavailable_events"] == [2]
    assert progress["n_failed"] == 0


@pytest.mark.parametrize("value", [None, {}, {"data": None}, {"data": {}}, {"data": [None]}, {"data": [{}]}])
def test_invalid_inventory_is_not_valid_empty(monkeypatch, isolated, value):
    before = seed_full(isolated)
    monkeypatch.setattr(fetch, "get", lambda url, **kw: {} if url.endswith("/struct") else value)
    with pytest.raises(fetch.CaptureIncomplete):
        fetch.main()
    assert (isolated / "superbet_latest_full.json").read_bytes() == before[0]
    assert not diag(isolated)["complete"]


def test_valid_empty_close_preserves_full(monkeypatch, isolated):
    before = seed_full(isolated)
    monkeypatch.setenv("ODDS_WINDOW_H", "2")
    install_source(monkeypatch, [])
    assert fetch.main() == 0 and fetch.MIN_EFF == 0
    assert diag(isolated)["complete"]
    assert (isolated / "superbet_latest_full.json").read_bytes() == before[0]


def test_close_excludes_late_events_and_never_promotes(monkeypatch, isolated):
    before = seed_full(isolated)
    monkeypatch.setenv("ODDS_WINDOW_H", "2")
    now = datetime.now(timezone.utc)
    calls = install_source(monkeypatch, [event(1, now + timedelta(hours=1)), event(2, now + timedelta(hours=3))])
    assert fetch.main() == 1 and calls == ["1"]
    assert (isolated / "superbet_latest_full.json").read_bytes() == before[0]
    assert latest_rows(isolated, full=False)[0]["mode"] == "close"


def test_budget_stops_refill_preserves_full_and_reports_pending(monkeypatch, isolated):
    before = seed_full(isolated)
    now = datetime.now(timezone.utc)
    events = [event(i, now + timedelta(hours=1)) for i in range(1, 6)]
    install_source(monkeypatch, events)
    clock = SimpleNamespace(value=0)
    monkeypatch.setattr(fetch, "time", SimpleNamespace(monotonic=lambda: clock.value, time_ns=time.time_ns))
    monkeypatch.setattr(fetch, "CAPTURE_BUDGET_SECONDS", 3)
    monkeypatch.setattr(fetch, "WORKERS", 1)

    def capture(e, deadline):
        clock.value += 2
        return events[e["eventId"] - 1], now

    monkeypatch.setattr(fetch, "_fetch_event", capture)
    with pytest.raises(fetch.CaptureBudgetExceeded):
        fetch.main()
    progress = diag(isolated)
    assert progress["n_scheduled"] == 2 and progress["n_unattempted"] == 3
    assert progress["n_out"] == 2
    assert progress["budget_exhausted"] and not progress["complete"]
    assert before == ((isolated / "superbet_latest_full.json").read_bytes(), (isolated / "superbet_latest.json").read_bytes())


def test_scheduler_refills_around_slow_first_detail_and_preserves_final_order(monkeypatch, isolated):
    now = datetime.now(timezone.utc)
    events = [event(i, now + timedelta(hours=1, seconds=i)) for i in range(1, 9)]
    install_source(monkeypatch, events)
    monkeypatch.setattr(fetch, "WORKERS", 3)
    first_started, tail_started = threading.Event(), threading.Event()
    counts = {"inflight": 0, "peak": 0}
    lock = threading.Lock()

    def capture(e, deadline):
        with lock:
            counts["inflight"] += 1
            counts["peak"] = max(counts["peak"], counts["inflight"])
        try:
            if e["eventId"] == 1:
                first_started.set()
                assert tail_started.wait(timeout=3), "primeiro detalhe bloqueou o restante do inventário"
            else:
                assert first_started.wait(timeout=3)
                if e["eventId"] == 8:
                    tail_started.set()
            return events[e["eventId"] - 1], now
        finally:
            with lock:
                counts["inflight"] -= 1

    monkeypatch.setattr(fetch, "_fetch_event", capture)
    assert fetch.main() == 8
    assert tail_started.is_set() and counts["peak"] <= 3
    assert [row["event_id"] for row in latest_rows(isolated)[1]] == list(range(1, 9))


def test_unique_outputs_dont_overwrite_previous_run(monkeypatch, isolated):
    now = datetime.now(timezone.utc)
    install_source(monkeypatch, [event(1, now + timedelta(hours=1))])
    fetch.main()
    first, _ = latest_rows(isolated, full=False)
    original = (isolated / first["file"]).read_bytes()
    fetch.main()
    second, _ = latest_rows(isolated, full=False)
    assert first["file"] != second["file"]
    assert (isolated / first["file"]).read_bytes() == original


def test_timestamp_is_detail_response_time(monkeypatch):
    now = datetime(2026, 9, 12, 12, tzinfo=fetch.BRT)
    captured = now + timedelta(minutes=10)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return captured.astimezone(tz)

    monkeypatch.setattr(fetch, "datetime", Clock)
    monkeypatch.setattr(fetch.random, "uniform", lambda *_: 0)
    e = event(1, now + timedelta(hours=1))
    monkeypatch.setattr(fetch, "get", lambda *_args, **_kw: {"data": [e]})
    detail, observed = fetch._fetch_event(e, time.monotonic() + 60)
    assert observed == captured
    assert fetch.normalize_event(e, detail, {}, observed)["captured_at"] == "2026-09-12 12:10:00"


def test_get_limits_timeout_to_remaining_and_uses_thread_session(monkeypatch):
    requests_seen = []
    sessions = []

    class Response:
        status_code = 200
        raw = io.BytesIO(b'{"data": []}')
        def __enter__(self): return self
        def __exit__(self, *args): pass

    class Session:
        def __init__(self):
            self.headers = {}
            sessions.append(self)
        def get(self, url, **kwargs):
            requests_seen.append((self, kwargs["timeout"]))
            result = Response()
            result.raw = io.BytesIO(b'{"data": []}')
            return result
        def close(self): pass

    fetch._close_sessions()
    monkeypatch.setattr(fetch.requests, "Session", Session)
    monkeypatch.setattr(fetch, "time", SimpleNamespace(monotonic=lambda: 10))
    try:
        assert fetch.get("https://example.test", deadline=14) == {"data": []}
        assert fetch.get("https://example.test", deadline=14) == {"data": []}
        assert len(sessions) == 1 and requests_seen == [(sessions[0], (2, 2))] * 2
        worker = threading.Thread(target=lambda: fetch.get("https://example.test", deadline=14))
        worker.start()
        worker.join(timeout=2)
        assert len(sessions) == 2
    finally:
        fetch._close_sessions()


def test_get_expired_deadline_makes_no_request(monkeypatch):
    monkeypatch.setattr(fetch, "_session", lambda: pytest.fail("não deve abrir sessão"))
    with pytest.raises(fetch.CaptureBudgetExceeded):
        fetch.get("https://example.test", deadline=time.monotonic() - 1)


@pytest.mark.parametrize("status,body,terminal", [(404, b"not found", True), (200, b"null", False),
                                                 (200, b"", False), (200, b"{broken", False)])
def test_http_404_is_the_only_missing_detail_response(monkeypatch, status, body, terminal):
    class Response:
        status_code = status
        def __enter__(self):
            self.raw = io.BytesIO(body)
            return self
        def __exit__(self, *args): pass

    monkeypatch.setattr(fetch, "_session", lambda: SimpleNamespace(get=lambda *args, **kw: Response()))
    if terminal:
        assert fetch.get("https://example.test", tries=1, allow_not_found=True) is fetch.NOT_FOUND
    elif body == b"null":
        # O decoder pode devolver null, mas nunca o converte no sentinel HTTP404.
        assert fetch.get("https://example.test", tries=1, allow_not_found=True) is None
    else:
        with pytest.raises(fetch.CaptureIncomplete):
            fetch.get("https://example.test", tries=1, allow_not_found=True)


def test_normalizer_rejects_unpaired_nonfinite_or_inactive_odds():
    now = datetime.now(timezone.utc)
    e = event(1, now + timedelta(hours=1))
    e["odds"][0]["price"] = float("inf")
    assert fetch.normalize_event(e, e, {}, now) is None
    e["odds"][0]["price"] = 1.9
    e["odds"][1]["status"] = "suspended"
    assert fetch.normalize_event(e, e, {}, now) is None
