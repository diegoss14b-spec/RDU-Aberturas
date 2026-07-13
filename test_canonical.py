# -*- coding: utf-8 -*-
"""test_canonical.py — testes mínimos de identidade canônica (P0 do relatório)."""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from canonical import (
    norm_team, history_key, parse_history_key, match_to_sofa, resolve_fixture,
    gscore, ALIASES,
)


def test_aliases_basic():
    cases = [
        ("Ceará", "ceara"),
        ("Ceará CE", "ceara"),
        ("CRB", "crb al"),
        ("CRB AL", "crb al"),
        ("Operário PR", "operario ferroviario"),
        ("Operário Ferroviário", "operario ferroviario"),
        ("France", "franca"),
        ("Spain", "espanha"),
        ("RB Bragantino", "red bull bragantino"),
        ("Sport", "sport recife"),
        ("América Mineiro", "america mg"),
        ("Athletic Club", "athletic club mg"),
        ("Londrina-PR", "londrina"),
        ("Vasco", "vasco da gama"),
    ]
    for raw, expect in cases:
        got = norm_team(raw)
        assert got == expect, f"norm_team({raw!r}) = {got!r}, want {expect!r}"


def test_history_key_sofa_and_legacy():
    k_sofa = history_key("superbet", "2026-07-13", "ceara", "athletic club mg",
                         "Finalizações", 22.5, "over", sofa_id=12345)
    assert k_sofa == "superbet|sofa:12345|Finalizações|22.5|over"
    meta = parse_history_key(k_sofa)
    assert meta["format"] == "sofa"
    assert meta["sofa_id"] == "12345"
    assert meta["mercado"] == "Finalizações"
    assert meta["lado"] == "over"

    k_leg = history_key("betano", "2026-07-13", "ceara", "athletic club mg",
                        "Cartões", 4.5, "under", sofa_id=None)
    assert k_leg == "betano|2026-07-13|ceara|athletic club mg|Cartões|4.5|under"
    meta2 = parse_history_key(k_leg)
    assert meta2["format"] == "legacy"
    assert meta2["hn"] == "ceara"
    assert meta2["day"] == "2026-07-13"


def test_history_key_lado_normalizes():
    k = history_key("7k", "2026-07-13", "a", "b", "Faltas", 20.5, "Mais", sofa_id=1)
    assert k.endswith("|over")
    k2 = history_key("7k", "2026-07-13", "a", "b", "Faltas", 20.5, "menos", sofa_id=1)
    assert k2.endswith("|under")


def test_gscore_order_swap():
    # confrontos com ordem trocada ainda casam
    s = gscore("ceara", "athletic club mg", "athletic club mg", "ceara")
    assert s >= 95


def test_match_to_sofa_pair():
    BRT = timezone(timedelta(hours=-3))
    day = "2026-07-13"
    start = datetime(2026, 7, 13, 20, 30, tzinfo=BRT)
    fixtures = [{
        "home": "Ceará", "away": "Athletic Club MG",
        "day_brt": day, "time_brt": "20:30",
        "start_ts": int(start.astimezone(timezone.utc).timestamp()),
        "sofa_id": 999, "league": "Brasileirão Série B",
        "_hn": norm_team("Ceará"), "_an": norm_team("Athletic Club MG"),
        "_lfp": "br-b",
    }]
    fx, sc, method = match_to_sofa(
        norm_team("Ceará CE"), norm_team("Athletic Club"),
        day, start, fixtures, book_league="Série B",
    )
    assert fx is not None, f"expected match, got sc={sc} method={method}"
    assert fx["sofa_id"] == 999
    assert method in ("pair", "one_side", "slot_unique")


def test_match_to_sofa_one_side():
    """1 lado forte + horário único no slot → match."""
    BRT = timezone(timedelta(hours=-3))
    day = "2026-07-13"
    start = datetime(2026, 7, 13, 16, 0, tzinfo=BRT)
    fixtures = [{
        "home": "Londrina", "away": "Novorizontino",
        "day_brt": day, "time_brt": "16:00",
        "start_ts": int(start.astimezone(timezone.utc).timestamp()),
        "sofa_id": 777, "league": "Brasileirão Série B",
        "_hn": norm_team("Londrina"), "_an": norm_team("Novorizontino"),
        "_lfp": "br-b",
    }]
    fx, sc, method = match_to_sofa(
        norm_team("Londrina PR"), "time esquisito xyz",
        day, start, fixtures, book_league="Brasileirão Série B",
    )
    assert fx is not None, f"one-side should match, sc={sc} m={method}"
    assert fx["sofa_id"] == 777


def test_resolve_fixture_unmatched():
    idt = resolve_fixture("Time Fantasma FC", "Outro Inventado", "2026-07-13T20:00:00-03:00",
                          fixtures=[])
    assert idt["sofa_id"] is None
    assert idt["match_method"] == "unmatched"
    assert idt["hn"]
    assert idt["an"]


def test_parse_unknown_key():
    meta = parse_history_key("broken")
    assert meta["format"] == "unknown"


def main():
    tests = [
        test_aliases_basic,
        test_history_key_sofa_and_legacy,
        test_history_key_lado_normalizes,
        test_gscore_order_swap,
        test_match_to_sofa_pair,
        test_match_to_sofa_one_side,
        test_resolve_fixture_unmatched,
        test_parse_unknown_key,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
