"""Offline, source-bound contingency contracts; helpers reusable by gate tests."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket

import pytest

import odds_only as oo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("contingency tests cannot call the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.delenv("MERCADOS_OFF", raising=False)


def event(slug="superbet", **changes):
    obj = {
        "casa": oo.HOUSES[slug], "event_id": "1001", "name": "Time A - Time B",
        "league": "Liga teste", "start": (NOW + timedelta(hours=5)).isoformat(),
        "captured_at": (NOW - timedelta(minutes=10)).isoformat(),
        "mercados": {"Cartões": [{"linha": 4.5, "over": 1.9, "under": 1.95}]},
    }
    obj.update(changes)
    return obj


def write_snapshot(root, slug="superbet", events=None, *, pointer_at=None, pointer_changes=None):
    """Synthetic source fixture, no connection to bookmaker production data."""
    root = Path(root)
    events = [event(slug)] if events is None else events
    directory = root / "data" / "odds"
    directory.mkdir(parents=True, exist_ok=True)
    relative = f"_snapshots/{slug}_full_test.jsonl"
    target = directory / relative
    target.parent.mkdir(exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in events), encoding="utf-8")
    pointer = {"file": relative, "mode": "full", "n": len(events),
               "at": pointer_at or (NOW - timedelta(minutes=10)).isoformat()}
    pointer.update(pointer_changes or {})
    path = directory / f"{slug}_latest_full.json"
    path.write_text(json.dumps(pointer), encoding="utf-8")
    return path, target


def board(root):
    return oo.build_odds_only(root, NOW)


def unavailable(root):
    with pytest.raises(oo.OddsOnlyUnavailable):
        board(root)


def test_normal_snapshot_and_reconstruction(tmp_path):
    write_snapshot(tmp_path)
    result = board(tmp_path)
    assert result["mode"] == "odds_only"
    assert result["model"] == {"status": "unavailable", "source": "none", "markets": []}
    assert result["pricing"] == {}
    assert result["gerado_iso"] == "2026-09-30T11:50:00+00:00"
    assert result["rebuilt_at"] == "2026-09-30T12:00:00+00:00"
    assert result["contingency"]["expires_at"] == "2026-09-30T13:50:00+00:00"
    game = result["jogos"][0]
    assert game["casas"] == ["Superbet"]
    assert game["valor"] == [] and game["tem_valor"] is False
    assert "sofa_id" not in game
    assert game["source_identity"]["event_id"] == "1001"
    assert len(game["source_identity"]["row_sha256"]) == 64
    assert oo.odds_only_reasons(result, tmp_path, NOW) == []
    assert oo.odds_only_reasons(result, tmp_path, NOW + timedelta(minutes=5)) == []


def test_other_invalid_house_does_not_remove_valid_house(tmp_path):
    write_snapshot(tmp_path)
    write_snapshot(tmp_path, "pinnacle", [event("pinnacle", captured_at="bad")])
    result = board(tmp_path)
    assert result["casas"] == ["Superbet"]
    assert {"house": "Pinnacle", "reason": "no_eligible_events"} in result["contingency"]["excluded"]


@pytest.mark.parametrize("captured", ["bad", None, True, "2026-09-30T11:50:00",
    (NOW - timedelta(hours=3)).isoformat(), (NOW + timedelta(minutes=6)).isoformat()])
def test_invalid_expired_future_capture_not_laundered_by_pointer(tmp_path, captured):
    write_snapshot(tmp_path, "7k", [event("7k", captured_at=captured)], pointer_at=NOW.isoformat())
    unavailable(tmp_path)


@pytest.mark.parametrize("pointer_at", ["bad", "2026-09-30T12:00:00", (NOW - timedelta(hours=3)).isoformat(),
    (NOW + timedelta(minutes=6)).isoformat()])
def test_invalid_pointer_clock_cannot_be_replaced_by_record_clock(tmp_path, pointer_at):
    write_snapshot(tmp_path, pointer_at=pointer_at)
    unavailable(tmp_path)


@pytest.mark.parametrize("start", [None, True, "bad", "2026-09-30T17:00:00",
    (NOW - timedelta(hours=1)).isoformat(), (NOW + timedelta(minutes=5)).isoformat()])
def test_kickoff_is_explicit_future_with_five_minute_guard(tmp_path, start):
    write_snapshot(tmp_path, events=[event(start=start)])
    unavailable(tmp_path)


@pytest.mark.parametrize("start", [int((NOW + timedelta(hours=5)).timestamp()),
    int((NOW + timedelta(hours=5)).timestamp() * 1000), "2026-09-30T14:00:00-03:00"])
def test_unambiguous_epoch_and_aware_kickoff(tmp_path, start):
    write_snapshot(tmp_path, events=[event(start=start)])
    assert board(tmp_path)["jogos"][0]["inicio_iso"] == "2026-09-30T17:00:00+00:00"


@pytest.mark.parametrize("slug", ["superbet", "pinnacle", "estrelabet"])
def test_only_audited_legacy_brt_serializers(tmp_path, slug):
    write_snapshot(tmp_path, slug, [event(slug, captured_at="2026-09-30 08:50:00")])
    game = board(tmp_path)["jogos"][0]
    assert game["captured_at"] == "2026-09-30T11:50:00+00:00"
    assert game["source_identity"]["timestamp_source"] == "legacy_collector_brt"


def test_legacy_clock_does_not_follow_renewed_pointer(tmp_path):
    write_snapshot(tmp_path, events=[event(captured_at="2026-09-30 08:10:00")], pointer_at=NOW.isoformat())
    unavailable(tmp_path)


def test_legacy_naive_kickoff_is_still_rejected(tmp_path):
    write_snapshot(tmp_path, events=[event(captured_at="2026-09-30 08:50:00", start="2026-09-30 17:00:00")])
    unavailable(tmp_path)


@pytest.mark.parametrize("slug", ["7k", "bet365", "sportingbet"])
def test_naive_clock_of_unapproved_collector_rejected(tmp_path, slug):
    write_snapshot(tmp_path, slug, [event(slug, captured_at="2026-09-30 08:50:00")])
    unavailable(tmp_path)


@pytest.mark.parametrize("value", [True, None, "1.91", float("nan"), float("inf"), 1.0, 1000])
def test_invalid_odds_not_shown(tmp_path, value):
    obj = event()
    obj["mercados"]["Cartões"][0]["over"] = value
    write_snapshot(tmp_path, events=[obj])
    unavailable(tmp_path)


@pytest.mark.parametrize("change", [{"three_way": True}, {"exactly": 3.0},
    {"market_type_name": "Total Cartões - 3 vias"}, {"market_family": "three_way_total"},
    {"outcomes": [1, 2, 3]}, {"period": "1H"}, {"scope": "team"}, {"linha": 4.25}])
def test_ambiguous_three_way_or_wrong_period_scope_rejected(tmp_path, change):
    obj = event()
    obj["mercados"]["Cartões"][0].update(change)
    write_snapshot(tmp_path, events=[obj])
    unavailable(tmp_path)


def test_total_and_exact_team_contract_metadata_preserved(tmp_path):
    obj = event()
    obj["mercados"]["Cartões"][0].update(market_type_name="Cartões", market_type_id=123, scope="match")
    obj["mercados_time"] = {"Cartões": {
        "Time A": [{"linha": 1.5, "over": 1.9, "under": 1.9}],
        "time b": [{"linha": 2.5, "over": 1.8, "under": 2.0}],
        "Time A Sub-20": [{"linha": 9.5, "over": 1.8, "under": 2.0}],
    }}
    write_snapshot(tmp_path, events=[obj])
    game = board(tmp_path)["jogos"][0]
    assert game["mercados"]["Cartões"]["Superbet"][0]["market_type_id"] == 123
    assert set(game["times"]["Cartões"]) == {"home", "away"}
    assert game["times"]["Cartões"]["home"]["casas"]["Superbet"][0]["linha"] == 1.5


def test_fuzzy_team_names_are_not_mapped(tmp_path):
    obj = event(mercados={})
    obj["mercados_time"] = {"Cartões": {"Time A FC": [{"linha": 1.5, "over": 1.9, "under": 1.9}]}}
    write_snapshot(tmp_path, events=[obj])
    unavailable(tmp_path)


def test_same_names_across_houses_not_joined(tmp_path):
    write_snapshot(tmp_path)
    write_snapshot(tmp_path, "pinnacle", [event("pinnacle")])
    result = board(tmp_path)
    assert len(result["jogos"]) == 2
    assert all(len(g["casas"]) == 1 for g in result["jogos"])
    assert {g["source_identity"]["house"] for g in result["jogos"]} == {"superbet", "pinnacle"}


def test_duplicate_id_cross_match_fails_entire_house(tmp_path):
    write_snapshot(tmp_path, events=[event(), event(name="Outra A - Outra B")])
    unavailable(tmp_path)


def test_different_ids_same_names_stay_separate(tmp_path):
    write_snapshot(tmp_path, events=[event(), event(event_id="1002")])
    assert len(board(tmp_path)["jogos"]) == 2


@pytest.mark.parametrize("change", [{"n": 2}, {"n": True}, {"mode": "close"},
    {"file": "../superbet_full_test.jsonl"}, {"file": "/tmp/superbet.jsonl"},
    {"file": "_snapshots/pinnacle_full_test.jsonl"}, {"casa": "Betano"}])
def test_pointer_integrity_and_traversal_rejected(tmp_path, change):
    write_snapshot(tmp_path, pointer_changes=change)
    unavailable(tmp_path)


def test_wrong_house_record_rejected(tmp_path):
    write_snapshot(tmp_path, events=[event(casa="Pinnacle")])
    unavailable(tmp_path)


def test_missing_raw(tmp_path):
    _, target = write_snapshot(tmp_path)
    target.unlink()
    unavailable(tmp_path)


def test_symlink_raw_rejected_even_if_target_inside_root(tmp_path):
    _, target = write_snapshot(tmp_path)
    alternative = target.with_name("copy.jsonl")
    target.rename(alternative)
    target.symlink_to(alternative)
    unavailable(tmp_path)


def test_malformed_raw_line_fails_house(tmp_path):
    path, target = write_snapshot(tmp_path)
    target.write_text(target.read_text() + "{bad}\n")
    obj = json.loads(path.read_text())
    obj["n"] = 2
    path.write_text(json.dumps(obj))
    unavailable(tmp_path)


def test_duplicate_json_keys_fail_house(tmp_path):
    _, target = write_snapshot(tmp_path)
    target.write_text(target.read_text().replace('"event_id": "1001"', '"event_id": "1001", "event_id": "wrong"'))
    unavailable(tmp_path)


def test_betano_raw_conversion_and_exact_team_sides(tmp_path):
    obj = event("betano")
    obj.pop("casa")
    obj.pop("mercados")
    obj["markets"] = {"cartoes": [
        {"market": "Total de Cartões", "line": 4.5, "over": 1.9, "under": 1.95},
        {"market": "Time A Total de Cartões", "line": 1.5, "over": 1.8, "under": 2.0},
    ]}
    write_snapshot(tmp_path, "betano", [obj])
    game = board(tmp_path)["jogos"][0]
    assert game["mercados"]["Cartões"]["Betano"][0]["source_market"] == "Total de Cartões"
    assert game["times"]["Cartões"]["home"]["casas"]["Betano"][0]["linha"] == 1.5


def test_honor_disabled_markets(tmp_path, monkeypatch):
    write_snapshot(tmp_path)
    monkeypatch.setenv("MERCADOS_OFF", "cartões")
    unavailable(tmp_path)


def test_nested_quote_old_clock_cannot_be_renewed(tmp_path):
    obj = event()
    obj["mercados"]["Cartões"][0]["captured_at"] = (NOW - timedelta(hours=3)).isoformat()
    write_snapshot(tmp_path, events=[obj])
    unavailable(tmp_path)


def test_oldest_real_quote_clock_not_rebuild_time(tmp_path):
    obj = event()
    obj["mercados"]["Cartões"][0]["captured_at"] = (NOW - timedelta(minutes=40)).isoformat()
    write_snapshot(tmp_path, events=[obj])
    result = board(tmp_path)
    assert result["gerado_iso"] == "2026-09-30T11:20:00+00:00"
    assert result["contingency"]["expires_at"] == "2026-09-30T13:20:00+00:00"


@pytest.mark.parametrize("tamper", ["odds", "home", "kickoff", "capture", "ttl", "ev", "sofa", "price", "extra", "proof"])
def test_gate_rejects_forged_board(tmp_path, tamper):
    write_snapshot(tmp_path)
    result = board(tmp_path)
    game = result["jogos"][0]
    if tamper == "odds": game["mercados"]["Cartões"]["Superbet"][0]["over"] = 2.8
    if tamper == "home": game["home"] = "Outro time"
    if tamper == "kickoff": game["inicio_iso"] = (NOW + timedelta(days=2)).isoformat()
    if tamper == "capture": game["captured_at"] = NOW.isoformat()
    if tamper == "ttl": game["expires_at"] = (NOW + timedelta(days=2)).isoformat()
    if tamper == "ev": game["valor"] = [{"edge": 1}]
    if tamper == "sofa": game["sofa_id"] = 123
    if tamper == "price": result["pricing"] = {"mu": 5}
    if tamper == "extra": result["fake"] = True
    if tamper == "proof": game["source_identity"]["snapshot_sha256"] = "0" * 64
    assert oo.odds_only_reasons(result, tmp_path, NOW)


def test_gate_rejects_missing_raw_after_build(tmp_path):
    _, target = write_snapshot(tmp_path)
    result = board(tmp_path)
    target.unlink()
    assert oo.odds_only_reasons(result, tmp_path, NOW)


def test_gate_rejects_expiry_and_malformed_board_without_throwing(tmp_path):
    write_snapshot(tmp_path)
    result = board(tmp_path)
    assert oo.odds_only_reasons(result, tmp_path, NOW + timedelta(minutes=110))
    assert oo.odds_only_reasons({"mode": "odds_only", "contingency": []}, tmp_path, NOW)
    assert oo.odds_only_reasons(None, tmp_path, NOW)


def test_noop_build_does_not_mutate_sources(tmp_path):
    pointer, raw = write_snapshot(tmp_path)
    before = pointer.read_bytes(), raw.read_bytes()
    board(tmp_path)
    assert before == (pointer.read_bytes(), raw.read_bytes())
