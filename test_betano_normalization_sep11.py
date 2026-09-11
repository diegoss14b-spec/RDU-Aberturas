"""Betano normalization regressions: synthetic fixtures, no network or collector mains.

This file is ready to place beside the repository's other pytest modules. It
imports the real implementation and has no dependency on audit files or paths.
Only pytest's temporary directory is written by these tests.
"""
import copy
import json
import socket

import pytest

from bookmaker_contracts import betano_market, normalize_betano_markets


ASIAN = "Asiático (Mais/Menos) Total de Cartões"
STANDARD = "Total de Cartões"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Betano normalization tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def quote(name=STANDARD, line=3.5, over=1.8, under=2.0, **extra):
    return {"market": name, "line": line, "over": over, "under": under, **extra}


def event(markets, name="Coritiba - Mirassol"):
    return {
        "name": name, "league": "Brasileirão", "event_id": "synthetic-betano-1",
        "start": 2000000000000, "captured_at": "2026-09-11T16:00:00-03:00",
        "markets": markets,
    }


@pytest.fixture
def read_consumers(tmp_path, monkeypatch):
    """Exercise actual board/history/counter readers on one temporary snapshot."""
    import build_board as board
    import capture_common as capture
    import history_ingest as history

    snapshot = tmp_path / "betano_synthetic.jsonl"
    pointer = tmp_path / "betano_latest.json"
    pointer.write_text(json.dumps({"file": snapshot.name}), encoding="utf-8")
    monkeypatch.setattr(capture, "resolve_odds_pointer", lambda *a, **k: ({}, snapshot))
    monkeypatch.setattr(history, "ODDS", tmp_path)

    def read(events):
        if isinstance(events, dict):
            events = [events]
        payload = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
        snapshot.write_text(payload, encoding="utf-8")
        source_before = snapshot.read_bytes()
        pointer_before = pointer.read_bytes()
        shown, source_name = board.load_betano()
        stored = history.load_events("betano")
        counts = capture.snapshot_market_counts(snapshot, casa="betano")
        assert source_name == snapshot.name
        assert snapshot.read_bytes() == source_before
        assert pointer.read_bytes() == pointer_before
        return shown, stored, counts

    return read


def normalized_record(record):
    return record.get("mercados", {}), record.get("mercados_time", {})


def test_three_consumers_share_atomic_pairs_metadata_and_counts(read_consumers):
    source = event({
        "cartoes": [quote(ASIAN, 3.5, 2.1, 1.7), quote(ASIAN, 4.0, 2.2, 1.65)],
        "principais_ou": [quote(STANDARD, 3.5, 1.91, 1.87)],
        "estatisticas": [quote("Coritiba Total de Desarmes", 11.5)],
    })
    before = copy.deepcopy(source)
    expected = normalize_betano_markets(source)
    shown, stored, counts = read_consumers(source)
    assert len(shown) == len(stored) == 1
    assert normalized_record(shown[0]) == expected
    assert normalized_record(stored[0]) == expected
    assert counts == {"Cartões": 1, "Desarmes": 1}
    assert source == before
    standard, asian = expected[0]["Cartões"]
    assert (standard["linha"], standard["over"], standard["under"]) == (3.5, 1.91, 1.87)
    assert standard["source_market"] == STANDARD
    assert standard["source_tab"] == "principais_ou"
    assert standard["market_family"] == "standard_total"
    assert standard["settlement"] == "no_push"
    assert (asian["linha"], asian["over"], asian["under"]) == (4.0, 2.2, 1.65)
    assert asian["source_market"] == ASIAN
    assert asian["market_family"] == "asian_total"
    assert asian["settlement"] == "push_on_equal"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("tab", ["cartoes", "principais_ou"])
def test_standard_half_line_wins_regardless_of_position(reverse, tab):
    rows = [quote(over=1.91, under=1.87), quote(ASIAN, over=2.2, under=1.7)]
    if reverse:
        rows.reverse()
    selected = normalize_betano_markets(event({tab: rows}))[0]["Cartões"]
    assert len(selected) == 1
    assert (selected[0]["over"], selected[0]["under"]) == (1.91, 1.87)
    assert selected[0]["source_market"] == STANDARD


def test_standard_half_line_wins_even_from_lower_priority_tab():
    source = event({"cartoes": [quote(ASIAN, over=2.2, under=1.7)],
                    "principais_ou": [quote(over=1.91, under=1.87)]})
    row = normalize_betano_markets(source)[0]["Cartões"][0]
    assert (row["over"], row["under"], row["source_tab"]) == (1.91, 1.87, "principais_ou")


def test_new_integer_asian_line_is_kept_once_with_push(read_consumers):
    source = event({"cartoes": [quote(ASIAN, 4.0, 1.91, 1.87)],
                    "principais_ou": [quote(ASIAN, "4.0", 1.9, 1.9)]})
    shown, stored, counts = read_consumers(source)
    assert normalized_record(shown[0]) == normalized_record(stored[0])
    rows = shown[0]["mercados"]["Cartões"]
    assert len(rows) == 1
    assert (rows[0]["linha"], rows[0]["over"], rows[0]["under"]) == (4.0, 1.91, 1.87)
    assert rows[0]["settlement"] == "push_on_equal"
    assert counts == {"Cartões": 1}


def test_partial_rows_never_splice_and_do_not_count(read_consumers):
    source = event({"cartoes": [quote(under=None), quote(ASIAN, 4.0, 1.9, None)],
                    "principais_ou": [quote(over=None), quote(ASIAN, 4.0, None, 1.9)]})
    assert normalize_betano_markets(source) == ({}, {})
    assert read_consumers(source) == ([], [], {})


def test_partial_standard_cannot_replace_complete_asian():
    source = event({"cartoes": [quote(over=2.4, under=None), quote(ASIAN, over=1.9, under=1.9)]})
    row = normalize_betano_markets(source)[0]["Cartões"][0]
    assert (row["over"], row["under"], row["source_market"]) == (1.9, 1.9, ASIAN)


def test_two_complete_variants_are_selected_whole_not_best_side_each():
    source = event({"cartoes": [quote(ASIAN, over=2.4, under=1.6), quote(over=1.8, under=2.1)]})
    row = normalize_betano_markets(source)[0]["Cartões"][0]
    assert (row["over"], row["under"]) == (1.8, 2.1)
    assert (row["over"], row["under"]) != (2.4, 2.1)


@pytest.mark.parametrize("line", [3.25, 3.75, True, -1, "bad", None, float("nan"), float("inf"), 1e308])
def test_invalid_lines_fail_closed(line):
    assert normalize_betano_markets(event({"cartoes": [quote(ASIAN, line=line)]})) == ({}, {})


@pytest.mark.parametrize("side", ["over", "under"])
@pytest.mark.parametrize("price", [None, True, 0, 1, "bad", float("nan"), float("inf")])
def test_invalid_price_in_either_side_rejects_entire_pair(side, price):
    row = quote(ASIAN)
    row[side] = price
    assert normalize_betano_markets(event({"cartoes": [row]})) == ({}, {})


@pytest.mark.parametrize("extra", [{"exact": 4.0}, {"exactly": 4.0}, {"three_way": True}])
def test_third_outcome_cannot_become_push(extra):
    assert normalize_betano_markets(event({"cartoes": [quote(ASIAN, **extra)]})) == ({}, {})


@pytest.mark.parametrize("name", [
    "Asiático (Mais/Menos) - Total de Cartões no Primeiro Tempo",
    "Asiático (Mais/Menos) Total de Faltas", "Coritiba Asiático Total de Cartões",
    "Total de Cartões Vermelhos", "Primeiro Tempo Chutes no gol",
    "Ronaldo Chutes no gol", "Flamengo Total de Faltas",
    "Coritiba U20 Total de Desarmes", "Coritiba (F) Chutes no gol",
])
def test_other_aliases_periods_players_and_participants_stay_rejected(name):
    assert normalize_betano_markets(event({"estatisticas": [quote(name)]})) == ({}, {})


@pytest.mark.parametrize("name", ["Coritiba - Mirassol - Outro", "Coritiba - Coritiba", ""])
def test_ambiguous_or_missing_event_participants_reject_team_total(name):
    source = event({"estatisticas": [quote("Coritiba Total de Desarmes")]}, name=name)
    assert normalize_betano_markets(source) == ({}, {})


def test_legacy_name_mapper_has_no_unrequested_alias_expansion():
    assert betano_market(ASIAN, ("Coritiba", "Mirassol")) is None


def test_fixed_standard_tab_priority_is_shared_by_all_consumers(read_consumers):
    source = event({"principais_ou": [quote(over=2.1, under=1.7)],
                    "cartoes": [quote(over=1.8, under=2.0)]})
    shown, stored, counts = read_consumers(source)
    assert normalized_record(shown[0]) == normalized_record(stored[0])
    row = shown[0]["mercados"]["Cartões"][0]
    assert (row["over"], row["under"], row["source_tab"]) == (1.8, 2.0, "cartoes")
    assert counts == {"Cartões": 1}


@pytest.mark.parametrize("source", [None, [], {}, {"markets": []},
                                     event({"cartoes": [None, "x", {}]})])
def test_malformed_shapes_fail_closed_without_mutation(source):
    before = copy.deepcopy(source)
    assert normalize_betano_markets(source) == ({}, {})
    assert source == before


def test_normalization_is_repeatable_and_does_not_mutate_input():
    source = event({"cartoes": [quote(over="1.876", under="1.924"), quote(ASIAN, 4.0, 2.1, 1.7)]})
    before = copy.deepcopy(source)
    first = normalize_betano_markets(source)
    assert first == normalize_betano_markets(source)
    assert source == before
    assert (first[0]["Cartões"][0]["over"], first[0]["Cartões"][0]["under"]) == (1.88, 1.92)


def test_metadata_survives_real_board_ladder_sanitizer():
    from build_board import sanitize_ou_ladder

    rows = normalize_betano_markets(event({"cartoes": [quote(), quote(ASIAN, 4.0, 2.2, 1.65)]}))[0]["Cartões"]
    cleaned, _ = sanitize_ou_ladder(rows, margin_min=0, margin_max=0.15)
    assert len(cleaned) == len(rows) == 2
    for original, sanitized in zip(rows, cleaned):
        for key in ("source_market", "source_tab", "market_family", "settlement"):
            assert sanitized[key] == original[key]


def test_counter_counts_events_not_lines_and_ignores_invalid_event(read_consumers):
    valid = event({"cartoes": [quote(), quote(ASIAN, 4.0, 2.1, 1.7)]})
    invalid = event({"cartoes": [quote(under=None)]})
    shown, stored, counts = read_consumers([valid, invalid])
    assert len(shown) == len(stored) == 1
    assert counts == {"Cartões": 1}
