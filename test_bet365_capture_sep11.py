"""CI regressions: real implementation, synthetic data, tmp_path, no network.

Place beside the implementation modules and run pytest. While this artifact is
outside the checkout, use PYTHONPATH=<checkout>; no research files are required.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
import requests

import bet365_capture_plan as plan
import build_board as board
import capture_common as cc
import fetch_odds_bet365 as fetch
from observation_clock import observation_meta, observation_reason


NOW = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def isolated_capture(monkeypatch, tmp_path):
    """Redirect all possible collector output; fail if any real HTTP is tried."""
    odds = tmp_path / "odds"
    status = odds / "_status"
    snapshots = odds / "_snapshots"
    status.mkdir(parents=True)
    snapshots.mkdir()
    monkeypatch.setattr(fetch, "OUTDIR", odds)
    monkeypatch.setattr(fetch, "STATUS_DIR", status)
    monkeypatch.setattr(fetch, "FIS_F", status / "bet365_fis.json")
    monkeypatch.setattr(fetch, "GATE_F", status / "bet365_gate.json")
    monkeypatch.setattr(fetch, "UNKNOWN_MK_F", status / "bet365_unknown_markets.jsonl")
    monkeypatch.setattr(fetch, "MIN_EFF", 5)
    monkeypatch.setattr(fetch, "N_REQ", 0)
    monkeypatch.setattr(fetch, "FULL_SKIPPED", False)
    monkeypatch.setattr(fetch, "CAPTURE_INCOMPLETE", False)
    monkeypatch.setattr(fetch, "_unknown_seen", None)
    monkeypatch.setattr(fetch, "DEADLINE", None)
    monkeypatch.setattr(fetch, "datetime", Clock)
    monkeypatch.setattr(fetch, "time", SimpleNamespace(time=lambda: NOW.timestamp(),
                        monotonic=lambda: 0, sleep=lambda _: None))
    monkeypatch.setattr(cc, "ODDS_DIR", odds)
    monkeypatch.setattr(cc, "STATUS_DIR", status)
    monkeypatch.setattr(cc, "FULL_SNAPSHOT_DIR", snapshots)
    monkeypatch.setattr(cc, "datetime", Clock)
    monkeypatch.setattr(board, "datetime", Clock)
    monkeypatch.delenv("ODDS_WINDOW_H", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    def forbidden_http(*args, **kwargs):
        raise AssertionError("Test attempted real HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_http)
    return SimpleNamespace(odds=odds, status=status, snapshots=snapshots)


def quote(ident="a", age=1, **updates):
    row = {"casa": "bet365", "event_id": ident, "parser_contract": 2,
           "name": "Home - Away", "league": "Brazil Serie A",
           "start": NOW.timestamp() + 86400,
           "captured_at": (NOW - timedelta(hours=age)).isoformat(),
           "mercados": {"Cartões": [{"linha": 4.5, "over": 1.833, "under": 2.0}]}}
    row.update(updates)
    return row


def inventory(rows):
    return [{"fi": str(r["event_id"]), "home": "Home", "away": "Away",
             "league": "Brazil Serie A", "time": r["start"]} for r in rows]


def retain(rows, current=None, selected=(), now=NOW):
    return plan.retained_quotes(rows, inventory(rows) if current is None else current,
                                 set(selected), now.timestamp())


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def load_rows_for_board(monkeypatch, tmp_path, rows, house="bet365", stale=False):
    path = tmp_path / "synthetic_board_source.jsonl"
    write_rows(path, rows)
    monkeypatch.setattr(cc, "resolve_odds_pointer", lambda *a, **k: (
        {"mode": "full", "_stale": stale}, path))
    return board.load_normalized(house, house)


def test_retention_preserves_original_timestamp_prices_and_input():
    row = quote(); original = copy.deepcopy(row)
    assert retain([row]) == [dict(original, retained_from_previous=True)]
    assert row == original


def test_selected_id_is_tombstone_even_if_new_result_has_no_market():
    assert retain([quote()], selected={"a"}) == []


def test_unlisted_id_is_not_resurrected():
    assert retain([quote()], current=[]) == []


def test_corrected_participants_do_not_inherit_old_quotes():
    row = quote(); current = inventory([row]); current[0]['home'] = 'Different'
    assert retain([row], current=current) == []


def test_unknown_malformed_market_does_not_abort_capture(isolated_capture):
    fetch._detect_unknown_total('new_cards_total', {'odds':[None,
        {'header':'Over','name':'4.5','odds':'1.9'},
        {'header':'Under','name':'4.5','odds':'1.9'}]}, 'fake', 'Home - Away')
    assert json.loads(fetch.UNKNOWN_MK_F.read_text())['n_odds'] == 2


def test_changed_kickoff_cannot_inherit_quote():
    row = quote(); current = inventory([row]); current[0]["time"] += 60
    assert retain([row], current) == []


def test_started_event_is_not_retained():
    assert retain([quote(start=NOW.timestamp())]) == []


@pytest.mark.parametrize("contract", [None, 1, "2"])
def test_legacy_parser_generation_not_retained(contract):
    assert retain([quote(parser_contract=contract)]) == []


@pytest.mark.parametrize("clock", [None, "", "invalid", "Infinity", "nan"])
def test_missing_invalid_capture_clock_not_retained(clock):
    assert retain([quote(captured_at=clock)]) == []


def test_future_capture_clock_sixty_second_tolerance():
    row = quote(captured_at=(NOW + timedelta(seconds=61)).isoformat())
    assert retain([row]) == []
    row["captured_at"] = (NOW + timedelta(seconds=60)).isoformat()
    assert plan.quote_age_hours(row, NOW.timestamp()) == 0


def test_retention_twelve_hour_boundary():
    assert len(retain([quote(age=12)])) == 1
    assert retain([quote(age=12 + 1/3600)]) == []


def test_repeated_retention_never_refreshes_clock_or_extends_lifetime():
    rows = retain([quote(age=11)])
    stamp = rows[0]["captured_at"]
    assert retain(rows, now=NOW + timedelta(minutes=30))[0]["captured_at"] == stamp
    assert retain(rows, now=NOW + timedelta(hours=2)) == []


def test_provider_integer_id_and_epoch_string_start_match():
    row = quote(ident=123); current = inventory([row]); row["start"] = str(int(row["start"]))
    assert len(retain([row], current)) == 1


def test_duplicate_previous_fi_retained_once():
    row = quote()
    assert len(retain([row, dict(row)])) == 1


def test_empty_quotes_not_retained():
    assert retain([quote(mercados={}, mercados_time={})]) == []


def test_team_only_quote_retained():
    row = quote(mercados={}, mercados_time={"Cartões": {"Home": [
        {"linha": 1.5, "over": 2.0, "under": 1.8}]}})
    assert len(retain([row])) == 1


def test_naive_brt_and_aware_utc_are_equivalent():
    naive = quote(captured_at="2026-09-11 14:00:00")
    aware = quote(captured_at="2026-09-11T17:00:00+00:00")
    assert plan.quote_age_hours(naive, NOW.timestamp()) == 1
    assert plan.quote_age_hours(naive, NOW.timestamp()) == plan.quote_age_hours(aware, NOW.timestamp())


def test_board_fresh_pointer_does_not_refresh_old_row(monkeypatch, tmp_path):
    assert load_rows_for_board(monkeypatch, tmp_path, [quote(age=3)])[0]["_stale"]


def test_board_two_hour_boundary(monkeypatch, tmp_path):
    rows = load_rows_for_board(monkeypatch, tmp_path, [quote("fresh", 2), quote("stale", 2+1/3600)])
    assert not rows[0].get("_stale", False)
    assert rows[1]["_stale"]


def test_board_omits_missing_future_expired_clocks(monkeypatch, tmp_path):
    rows = [quote("missing", captured_at=None), quote("expired", age=13),
            quote("future", captured_at=(NOW + timedelta(minutes=2)).isoformat())]
    assert load_rows_for_board(monkeypatch, tmp_path, rows) == []


def test_board_twelve_hour_boundary(monkeypatch, tmp_path):
    rows = load_rows_for_board(monkeypatch, tmp_path, [quote("a", 12), quote("b", 12+1/3600)])
    assert len(rows) == 1
    assert rows[0]["_stale"]


def test_board_other_bookmakers_unchanged(monkeypatch, tmp_path):
    rows = load_rows_for_board(monkeypatch, tmp_path, [quote(age=13, casa="superbet")], house="superbet")
    assert len(rows) == 1
    assert not rows[0].get("_stale", False)


def test_retained_observation_duplicate_in_history():
    row = retain([quote()])[0]
    observed = observation_meta(row, json.dumps(row))
    assert observed["timestamp_source"] == "source_captured_at"
    assert observation_reason(observed, NOW, {"last_seen_observed_at": row["captured_at"]}) == "duplicate_or_out_of_order_observation"


def test_batch_nine_of_ten_retries_only_missing(monkeypatch):
    calls = []
    def get(path, params, token):
        calls.append(params["FI"])
        ids = range(9) if len(calls) == 1 else [9]
        return {"success": 1, "results": [{"FI": str(i)} for i in ids]}
    monkeypatch.setattr(fetch, "get", get)
    found, exhausted, errors = fetch.fetch_batch([{"fi": str(i)} for i in range(10)], "fake")
    assert set(found) == {str(i) for i in range(10)}
    assert calls == [",".join(map(str, range(10))), "9"]
    assert not exhausted and errors == []


def test_batch_preserves_nine_when_missing_retry_hits_deadline(monkeypatch):
    calls = []
    def get(path, params, token):
        calls.append(params["FI"])
        if len(calls) == 2:
            raise fetch.CaptureBudgetExceeded("synthetic deadline")
        return {"success": 1, "results": [{"FI": str(i)} for i in range(9)]}
    monkeypatch.setattr(fetch, "get", get)
    found, exhausted, errors = fetch.fetch_batch([{"fi": str(i)} for i in range(10)], "fake")
    assert set(found) == {str(i) for i in range(9)}
    assert exhausted and errors == ["9"]


@pytest.mark.parametrize("response,expected_errors", [(None, ["a"]), ({"success": 1, "results": []}, [])])
def test_batch_transport_failure_is_not_valid_empty_response(monkeypatch, response, expected_errors):
    monkeypatch.setattr(fetch, "get", lambda *args: response)
    found, exhausted, errors = fetch.fetch_batch([{"fi": "a"}], "fake")
    assert found == {} and not exhausted and errors == expected_errors


@pytest.mark.parametrize("hit_limit", [True, False])
def test_get_exhausted_budget_makes_no_http(monkeypatch, hit_limit):
    monkeypatch.setattr(fetch, "N_REQ", fetch.REQUEST_LIMIT if hit_limit else 0)
    monkeypatch.setattr(fetch, "DEADLINE", 0.5 if not hit_limit else None)
    with pytest.raises(fetch.CaptureBudgetExceeded):
        fetch.get("/v3/bet365/prematch", {"FI": "a"}, "fake")


def test_budget_exception_class_survives_finish_and_blocks_outer_retry(isolated_capture):
    import run_capture
    assert cc.finish('bet365',0,5,error=fetch.CaptureBudgetExceeded('orçamento de requests/tempo esgotado')) == 2
    status = json.loads((isolated_capture.status / 'bet365.json').read_text())
    assert status['error_class'] == 'CaptureBudgetExceeded'
    assert not run_capture.should_retry(status, 'bet365')


def seed_full(paths, rows, age=3):
    source = paths.snapshots / "previous.jsonl"
    write_rows(source, rows)
    pointer = {"file": "_snapshots/previous.jsonl", "n": len(rows), "mode": "full",
               "at": (NOW - timedelta(hours=age)).isoformat(), "captured_by": "actions"}
    (paths.odds / "bet365_latest_full.json").write_text(json.dumps(pointer), encoding="utf-8")
    return pointer


def test_full_skip_needs_no_token_and_preserves_status_clock(monkeypatch, isolated_capture):
    old = seed_full(isolated_capture, [quote("a", .5), quote("b", .5)], age=.5)
    def forbidden_token():
        raise AssertionError("Reusing a verified full must not request a token")
    monkeypatch.setattr(fetch, "_token", forbidden_token)
    n = fetch.main()
    assert n == 2 and fetch.FULL_SKIPPED and fetch.N_REQ == 0
    assert cc.finish("bet365", n, fetch.MIN_EFF, reused=True) == 0
    status = json.loads((isolated_capture.status / "bet365.json").read_text())
    assert status["ts_utc"] == "2026-09-11T17:30:00Z"
    assert status["pointer_at"] == old["at"]
    assert status["attempted"] is False


@pytest.mark.parametrize("fresh_count", [0, 4, 5])
def test_main_retained_rows_do_not_satisfy_fresh_minimum(monkeypatch, isolated_capture, fresh_count):
    """Exercise real main, real queue, real retention, real pointer promotion."""
    previous = [quote(str(i), age=3) for i in range(5, 10)]
    original_pointer = seed_full(isolated_capture, previous)
    candidates = inventory([quote(str(i), age=3) for i in range(10)])
    monkeypatch.setattr(fetch, "MAX_EVENTS", 5)
    monkeypatch.setattr(fetch, "_token", lambda: "fake")
    def sweep(token, now, max_pages, start_page=1):
        sweep.next_page = 11
        return candidates if start_page == 1 else []
    monkeypatch.setattr(fetch, "_sweep_upcoming", sweep)
    raw_market = {"cards_fouls": {"sp": {"number_of_cards_in_match": {"odds": [
        {"header": "Over", "name": "4.5", "odds": "1.833"},
        {"header": "Under", "name": "4.5", "odds": "2.000"}]}}}}
    def batch(events, token):
        assert [e["fi"] for e in events] == list(map(str, range(5)))
        return {str(i): dict(raw_market, FI=str(i)) for i in range(fresh_count)}, False, []
    monkeypatch.setattr(fetch, "fetch_batch", batch)
    total = fetch.main()
    assert total == fresh_count + 5
    assert fetch.CAPTURE_INCOMPLETE is (fresh_count < 5)
    metrics = json.loads((isolated_capture.status / "bet365_discovery.json").read_text())["metrics"]
    assert metrics["fresh_events"] == fresh_count and metrics["retained_events"] == 5
    pointer = json.loads((isolated_capture.odds / "bet365_latest_full.json").read_text())
    if fresh_count < 5:
        assert pointer == original_pointer
    else:
        assert pointer["n"] == 10 and pointer["file"] != original_pointer["file"]
