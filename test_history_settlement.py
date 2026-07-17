# -*- coding: utf-8 -*-
"""Regressões do retry de liquidação e do merge legacy -> Sofa."""
from datetime import datetime, timezone

from history_merge import merge_records
from history_settle import (
    BRT,
    PENDING_STATUS,
    build_settlement_status,
    find_result,
    settle_one,
)
from migrate_history_keys import migrate_keys_dict, migrate_tick_rows


def fixture():
    start = datetime(2026, 7, 17, 22, 0, tzinfo=timezone.utc)
    return {
        "sofa_id": 999,
        "home": "Time Alpha",
        "away": "Time Beta",
        "_hn": "time alpha",
        "_an": "time beta",
        "day_brt": "2026-07-17",
        "start_ts": int(start.timestamp()),
        "time_brt": "19:00",
        "league": "Teste",
        "_lfp": "teste",
    }


def record(status="closed"):
    return {
        "status": status,
        "open_odd": 2.1,
        "open_ts": "2026-07-17T09:00:00-03:00",
        "last_odd": 1.9,
        "last_ts": "2026-07-17T18:55:00-03:00",
        "close_odd": 1.9,
        "close_ts": "2026-07-17T18:55:00-03:00",
        "kickoff": "2026-07-17T19:00:00-03:00",
        "home_raw": "Time Alpha",
        "away_raw": "Time Beta",
        "home_norm": "time alpha",
        "away_norm": "time beta",
        "n_obs": 2,
        "n_moves": 1,
    }


def result_row(**fields):
    row = {
        "date": "2026-07-17",
        "home": "Time Alpha",
        "away": "Time Beta",
        "_h": "time alpha",
        "_a": "time beta",
        "_source": "auto",
    }
    row.update(fields)
    return row


def test_unavailable_is_retried_then_settled():
    key = "betano|2026-07-17|time alpha|time beta|Escanteios|9.5|over"
    item = record("unavailable")
    now = datetime(2026, 7, 18, 12, 0, tzinfo=BRT)
    outcome, _, _ = settle_one(key, item, [], now)
    assert outcome == "pending"
    assert item["status"] == PENDING_STATUS
    assert item["settlement_retryable"] is True
    assert item["settlement_reason"] == "game_not_in_results"

    outcome, _, clv = settle_one(key, item, [result_row(corners=11)], now)
    assert outcome == "settled"
    assert item["status"] == "settled"
    assert item["result"] == 11
    assert item["won"] is True
    assert item["settlement_retryable"] is False
    assert clv["clv_pct"] == item["clv_pct"]


def test_missing_stat_remains_pending():
    key = "betano|2026-07-17|time alpha|time beta|Chutes no gol|8.5|under"
    item = record()
    now = datetime(2026, 7, 18, 12, 0, tzinfo=BRT)
    outcome, _, _ = settle_one(key, item, [result_row(shots=25)], now)
    assert outcome == "pending"
    assert item["settlement_reason"] == "stat_missing:shots_on_goal"


def test_pending_audit_is_throttled_but_retry_still_runs():
    key = "betano|2026-07-17|time alpha|time beta|Escanteios|9.5|over"
    item = record()
    now = datetime(2026, 7, 18, 12, 0, tzinfo=BRT)
    outcome, changed, _ = settle_one(key, item, [], now)
    assert outcome == "pending" and changed is True
    attempts = item["settlement_attempts"]
    outcome, changed, _ = settle_one(key, item, [], now.replace(minute=15))
    assert outcome == "pending" and changed is False
    assert item["settlement_attempts"] == attempts
    outcome, changed, _ = settle_one(key, item, [], now.replace(hour=18))
    assert outcome == "pending" and changed is True
    assert item["settlement_attempts"] == attempts + 1


def test_find_result_prefers_row_with_requested_stat():
    auto = result_row(corners=None)
    manual = result_row(corners=12)
    manual["_source"] = "manual"
    assert find_result([auto, manual], "2026-07-17", "time alpha", "time beta", "corners") is manual


def test_backlog_observable_by_market_and_age():
    now = datetime(2026, 7, 20, 19, 0, tzinfo=BRT)
    key = "betano|2026-07-17|time alpha|time beta|Escanteios|9.5|over"
    item = record(PENDING_STATUS)
    item["settlement_reason"] = "stat_missing:corners"
    summary = build_settlement_status([(key, item)], [], now)
    market = summary["by_market"]["Escanteios"]
    assert summary["backlog"]["total"] == 1
    assert market["pending_age"]["3-7d"] == 1
    assert market["pending_reasons"]["stat_missing:corners"] == 1


def test_merge_preserves_open_close_result_and_counts():
    legacy = record("settled")
    legacy.update({"result": 12, "won": True, "n_obs": 3, "n_moves": 1})
    sofa = record("closed")
    sofa.update(
        {
            "sofa_id": 999,
            "open_ts": "2026-07-17T10:00:00-03:00",
            "close_ts": "2026-07-17T18:58:00-03:00",
            "close_odd": 1.85,
            "n_obs": 2,
            "n_moves": 2,
        }
    )
    merged = merge_records(legacy, sofa)
    assert merged["open_ts"] == "2026-07-17T09:00:00-03:00"
    assert merged["close_ts"] == "2026-07-17T18:58:00-03:00"
    assert merged["result"] == 12 and merged["won"] is True
    assert merged["status"] == "settled"
    assert merged["n_obs"] == 5 and merged["n_moves"] == 3


def test_key_migration_is_idempotent_and_merges_main_line():
    legacy_key = "betano|2026-07-17|time alpha|time beta|Escanteios|9.5|over"
    sofa_key = "betano|sofa:999|Escanteios|9.5|over"
    legacy = record("settled")
    legacy.update({"result": 12, "won": True})
    sofa = record("closed")
    sofa["sofa_id"] = 999
    keys = {
        legacy_key: legacy,
        sofa_key: sofa,
        "__main_lines__": {
            "betano|2026-07-17|time alpha|time beta|Escanteios": {
                "line": 9.5,
                "ts": "2026-07-17T10:00:00-03:00",
            },
            "betano|sofa:999|Escanteios": {
                "line": 10.5,
                "ts": "2026-07-17T11:00:00-03:00",
            },
        },
    }
    once, stats = migrate_keys_dict(keys, [fixture()])
    twice, stats2 = migrate_keys_dict(once, [fixture()])
    assert once == twice
    assert stats["merges"] == 1 and stats2["merges"] == 0
    assert list(key for key in once if not key.startswith("__")) == [sofa_key]
    assert once[sofa_key]["result"] == 12
    assert once["__main_lines__"]["betano|sofa:999|Escanteios"]["line"] == 10.5


def test_tick_migration_is_idempotent_and_deduplicates():
    base = {
        "ts": "2026-07-17T10:00:00-03:00",
        "kind": "open",
        "casa": "betano",
        "kickoff": "2026-07-17T19:00:00-03:00",
        "home": "time alpha",
        "away": "time beta",
        "mercado": "Escanteios",
        "linha": 9.5,
        "lado": "over",
        "odd": 2.1,
        "djogo": "2026-07-17",
    }
    legacy = dict(base, gid="2026-07-17|time alpha|time beta", sofa_id=None)
    sofa = dict(base, gid="sofa:999", sofa_id=999)
    once, stats = migrate_tick_rows([legacy, sofa], [fixture()])
    twice, stats2 = migrate_tick_rows(once, [fixture()])
    assert len(once) == 1 and once[0]["gid"] == "sofa:999"
    assert stats["deduped"] == 1
    assert once == twice and stats2["deduped"] == 0


def main():
    tests = [
        test_unavailable_is_retried_then_settled,
        test_missing_stat_remains_pending,
        test_pending_audit_is_throttled_but_retry_still_runs,
        test_find_result_prefers_row_with_requested_stat,
        test_backlog_observable_by_market_and_age,
        test_merge_preserves_open_close_result_and_counts,
        test_key_migration_is_idempotent_and_merges_main_line,
        test_tick_migration_is_idempotent_and_deduplicates,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  OK  {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
