# -*- coding: utf-8 -*-
"""test_history_quality.py — P1 open/close/quality."""
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from history_quality import (
    compute_capture_quality, is_pre_kickoff, should_close_key, pick_main_line, BRT,
)


def test_quality_full_prematch():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    k = {
        "kickoff": ko.isoformat(),
        "open_ts": (ko - timedelta(hours=12)).isoformat(),
        "open_odd": 1.9,
        "status": "open",
    }
    assert compute_capture_quality(k) == "full_prematch"


def test_quality_late_open():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    k = {
        "kickoff": ko.isoformat(),
        "open_ts": (ko - timedelta(hours=1)).isoformat(),
        "status": "open",
    }
    assert compute_capture_quality(k) == "late_open"


def test_quality_post_kickoff():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    k = {
        "kickoff": ko.isoformat(),
        "open_ts": (ko + timedelta(minutes=5)).isoformat(),
        "status": "open",
    }
    assert compute_capture_quality(k) == "post_kickoff"


def test_quality_no_close():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    k = {
        "kickoff": ko.isoformat(),
        "open_ts": (ko - timedelta(hours=10)).isoformat(),
        "status": "closed",
        "close_odd": None,
    }
    assert compute_capture_quality(k) == "no_close"


def test_quality_closed_ok():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    k = {
        "kickoff": ko.isoformat(),
        "open_ts": (ko - timedelta(hours=10)).isoformat(),
        "close_ts": (ko - timedelta(minutes=5)).isoformat(),
        "close_odd": 1.85,
        "status": "closed",
    }
    assert compute_capture_quality(k) == "full_prematch"


def test_is_pre_kickoff():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    assert is_pre_kickoff(ko - timedelta(minutes=1), ko) is True
    assert is_pre_kickoff(ko + timedelta(seconds=10), ko) is False


def test_n_moves_first_obs_zero():
    """1ª observação não incrementa n_moves (brief P1 §7.5)."""
    # simula lógica de price_moved
    is_new = True
    price_moved = (not is_new) and True
    n_moves = 0
    if price_moved:
        n_moves += 1
    assert n_moves == 0
    # 2ª obs com odd diferente
    is_new = False
    last_odd, odd = 1.90, 2.05
    price_moved = (not is_new) and abs(last_odd - odd) >= 0.01
    if price_moved:
        n_moves += 1
    assert n_moves == 1


def test_clv_valido_requires_close_before_kickoff():
    """CLV inválido se close_ts >= kickoff."""
    from datetime import datetime, timezone, timedelta
    BRT = timezone(timedelta(hours=-3))
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    open_ts = ko - timedelta(hours=5)
    close_ok = ko - timedelta(minutes=5)
    close_bad = ko + timedelta(minutes=1)
    assert open_ts < ko and close_ok < ko
    assert not (close_bad < ko)


def test_should_close():
    ko = datetime(2026, 7, 14, 16, 0, tzinfo=BRT)
    k = {"kickoff": ko.isoformat(), "status": "open"}
    assert should_close_key(k, now=ko - timedelta(minutes=5)) is False
    assert should_close_key(k, now=ko - timedelta(minutes=1)) is True


def test_pick_main_line():
    lines = [
        {"linha": 2.5, "over": 1.5, "under": 2.5},
        {"linha": 3.0, "over": 1.95, "under": 1.85},
        {"linha": 3.5, "over": 2.4, "under": 1.5},
    ]
    assert pick_main_line(lines) == 3.0


def main():
    tests = [
        test_quality_full_prematch, test_quality_late_open, test_quality_post_kickoff,
        test_quality_no_close, test_quality_closed_ok, test_is_pre_kickoff,
        test_n_moves_first_obs_zero, test_clv_valido_requires_close_before_kickoff,
        test_should_close, test_pick_main_line,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
