"""Regression fixtures from the 2026-09-06 audit; no network or production writes."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bookmaker_contracts import betano_market, event_participants, normalize_7k_event_name
from canonical import flags_compatible, norm_team, resolve_fixture, fixture_scoped_alias_pair


@pytest.mark.parametrize("raw,expected", [
    ("Total de Desarmes", ("Desarmes", None)),
    ("Coritiba Total de Desarmes", ("Desarmes", "Coritiba")),
    ("Mirassol Total de Desarmes", ("Desarmes", "Mirassol")),
    ("Coritiba Chutes no gol", ("Chutes no gol", "Coritiba")),
    ("Mirassol Total de Chutes no gol", ("Chutes no gol", "Mirassol")),
    ("Coritiba Total de Cartões", ("Cartões", "Coritiba")),
    ("Total de Faltas", ("Faltas", None)),
    ("Coritiba Total de chutes", ("Finalizações", "Coritiba")),
])
def test_betano_supported_contracts(raw, expected):
    assert betano_market(raw, ("Coritiba", "Mirassol")) == expected


@pytest.mark.parametrize("raw", [
    "Primeiro Tempo Chutes no gol", "Jogador Chutes no gol",
    "Coritiba Primeiro Tempo Chutes no gol", "Coritiba Chutes no gol 1º Tempo",
    "Coritiba Total de Desarmes 2º Tempo", "Primeiro Tempo Total de Desarmes",
    "Total de Desarmes Jogador", "Ronaldo Chutes no gol", "Flamengo Total de Faltas",
    "Coritiba (F) Chutes no gol", "Coritiba U20 Total de Desarmes",
    "Coritiba Total de Gols", "Coritiba Mais Desarmes", "Handicap de Desarmes",
])
def test_betano_unknown_scope_or_participant_rejected(raw):
    assert betano_market(raw, ("Coritiba", "Mirassol")) is None


def test_betano_malformed_or_ambiguous_event_fails_closed():
    assert event_participants("A - B - C") == ()
    assert betano_market("A Total de Desarmes", ()) is None
    assert betano_market("A Total de Desarmes", ("A", "A")) is None
    assert event_participants("América-MG - Athletico-PR") == ("América-MG", "Athletico-PR")


@pytest.mark.parametrize("raw,expected", [
    ("OB Odense - OB Odense vs FC Copenhague", "OB Odense - FC Copenhague"),
    ("Viborg - OB Odense - OB Odense", "Viborg - OB Odense"),
    ("América-MG - Athletico-PR", "América-MG - Athletico-PR"),
    ("A - B - C", "A - B - C"),
    ("Odense F - Odense - Copenhagen", "Odense F - Odense - Copenhagen"),
])
def test_7k_exact_duplicate_and_idempotence(raw, expected):
    assert normalize_7k_event_name(raw) == expected
    assert normalize_7k_event_name(expected) == expected


@pytest.mark.parametrize("raw,league,expected", [
    ("Man Utd", "England Premier League", "manchester united"),
    ("Wolves", "Inglaterra - Championship", "wolverhampton"),
    ("Rennes", "França - Ligue 1", "stade rennais"),
    ("Stade Rennes", "França - Ligue 1", "stade rennais"),
    ("Marselha", "França - Ligue 1", "olympique de marseille"),
    ("FC Copenhagen", "Dinamarca - Superliga", "kobenhavn"),
    ("FC Copenhague", "Dinamarca - Superliga", "kobenhavn"),
    ("ASA AL", "Brasil - Brasileiro - Série D", "as arapiraquense"),
])
def test_reviewed_alias_requires_context(raw, league, expected):
    assert norm_team(raw, league) == expected
    assert norm_team(raw, "Australia - A League") == norm_team(raw)
    assert norm_team(raw) != expected


def test_category_suffixes_not_erased_by_aliases():
    league = "England Premier League"
    assert norm_team("Wolves U20", league) != norm_team("Wolves", league)
    assert norm_team("Wolves F", league) != norm_team("Wolves", league)
    assert not flags_compatible(norm_team("Everton F", league), norm_team("Man Utd F", league),
                                norm_team("Everton", league), norm_team("Man Utd", league))


def test_bare_league_alias_needs_exact_fixture_country_anchor_and_unique_pair():
    dt = datetime(2026, 9, 6, 15, 15, tzinfo=timezone.utc)
    fx = {"home": "Angers", "away": "Stade Rennais", "sofa_id": 16310937,
          "_hn": "angers", "_an": "stade rennais", "day_brt": "2026-09-06",
          "start_ts": int(dt.timestamp()), "league_id": 34, "label": "Ligue1"}
    args = ("angers", "rennes", "Ligue 1", "2026-09-06", dt)
    assert norm_team("Rennes", "Ligue 1") == "rennes"
    assert fixture_scoped_alias_pair(*args, [fx]) == ("angers", "stade rennais", "France - Ligue 1")
    assert fixture_scoped_alias_pair(*args, [])[:2] == ("angers", "rennes")
    assert fixture_scoped_alias_pair(*args, [dict(fx, league_id=999)])[:2] == ("angers", "rennes")
    assert fixture_scoped_alias_pair(*args, [dict(fx, start_ts=fx["start_ts"] + 7200)])[:2] == ("angers", "rennes")
    assert fixture_scoped_alias_pair(*args, [dict(fx, _hn="other team")])[:2] == ("angers", "rennes")
    assert fixture_scoped_alias_pair(*args, [fx, dict(fx, sofa_id=999)])[:2] == ("angers", "rennes")
    assert fixture_scoped_alias_pair("angers f", "rennes", "Ligue 1", "2026-09-06", dt, [fx])[:2] == ("angers f", "rennes")
    assert resolve_fixture("Angers", "Rennes", fx["start_ts"], "Ligue 1", [fx])["sofa_id"] == 16310937


def fixture():
    start = int(datetime(2026, 9, 6, 13, tzinfo=timezone.utc).timestamp())
    return {"home": "Everton", "away": "Manchester United", "sofa_id": 16363259,
            "day_brt": "2026-09-06", "start_ts": start, "time_brt": "10:00",
            "league": "Premier League", "_hn": "everton", "_an": "manchester united",
            "_lfp": "epl"}


def test_context_alias_reaches_same_fixture_in_board_and_history_contract():
    fx = fixture()
    resolved = resolve_fixture("Everton", "Man Utd", fx["start_ts"], "England Premier League", [fx])
    assert resolved["sofa_id"] == 16363259
    assert resolved["match_method"] == "pair"
    assert resolve_fixture("Everton F", "Man Utd F", fx["start_ts"], "England Premier League", [fx])["sofa_id"] is None
    assert resolve_fixture("Everton", "Man Utd", fx["start_ts"] + 86400, "England Premier League", [fx])["sofa_id"] is None


def test_three_consumers_use_same_betano_contract(tmp_path, monkeypatch):
    import build_board as board
    import capture_common as cc
    import history_ingest as hist
    rec = {"name": "Coritiba - Mirassol", "league": "Brasileirão", "start": 1788703200000,
           "markets": {"estatisticas": [
               {"market": raw, "line": 12.5, "over": 1.8, "under": 2.0}
               for raw in ("Total de Desarmes", "Coritiba Total de Desarmes", "Mirassol Chutes no gol",
                           "Primeiro Tempo Chutes no gol", "Ronaldo Chutes no gol")]}}
    snap = tmp_path / "snapshot.jsonl"
    snap.write_text(json.dumps(rec), encoding="utf-8")
    (tmp_path / "betano_latest.json").write_text(json.dumps({"file": snap.name}), encoding="utf-8")
    monkeypatch.setattr(cc, "resolve_odds_pointer", lambda *a, **k: ({}, snap))
    monkeypatch.setattr(hist, "ODDS", tmp_path)
    shown = board.load_betano()[0][0]
    stored = hist.load_events("betano")[0]
    assert shown["mercados"] == stored["mercados"]
    assert shown["mercados_time"] == stored["mercados_time"]
    assert set(shown["mercados_time"]) == {"Desarmes", "Chutes no gol"}
    assert cc.snapshot_market_counts(snap, casa="betano") == {"Chutes no gol": 1, "Desarmes": 1}


def test_7k_old_snapshot_fixed_in_both_readers(tmp_path, monkeypatch):
    import build_board as board
    import capture_common as cc
    import history_ingest as hist
    rec = {"name": "OB Odense - OB Odense - FC Copenhague", "mercados": {"Cartões": []}}
    snap = tmp_path / "snapshot.jsonl"
    snap.write_text(json.dumps(rec), encoding="utf-8")
    (tmp_path / "7k_latest.json").write_text(json.dumps({"file": snap.name}), encoding="utf-8")
    monkeypatch.setattr(cc, "resolve_odds_pointer", lambda *a, **k: ({}, snap))
    monkeypatch.setattr(hist, "ODDS", tmp_path)
    assert board.load_normalized("7k", "7k")[0]["name"] == "OB Odense - FC Copenhague"
    ev = hist.load_events("7k")[0]
    assert (ev["home_raw"], ev["away_raw"]) == ("OB Odense", "FC Copenhague")
    # The raw accumulated source is never rewritten by a normalizer.
    assert json.loads(snap.read_text())["name"] == rec["name"]
