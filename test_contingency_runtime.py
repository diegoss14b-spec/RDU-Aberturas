import copy
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import build_manifest
import contingency_runtime as runtime
import gate_board

NOW = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)


def public_board():
    cap = (NOW - timedelta(minutes=30)).isoformat()
    expiry = (NOW + timedelta(minutes=90)).isoformat()
    return {"mode": "odds_only", "model": {"status": "unavailable", "source": "none", "markets": []},
            "pricing": {}, "gerado_iso": cap, "rebuilt_at": NOW.isoformat(),
            "contingency": {"expires_at": expiry, "reason": "fixture stale"},
            "jogos": [{"valor": [], "tem_valor": False, "game_state": "upcoming",
                       "captured_at": cap, "expires_at": expiry,
                       "inicio_iso": (NOW + timedelta(hours=3)).isoformat()}]}


def test_public_freshness_and_rebuild_are_separate():
    board = public_board()
    assert runtime.public_odds_only_reasons(board, NOW) == []
    assert runtime.public_odds_only_reasons(board, NOW + timedelta(minutes=90))


@pytest.mark.parametrize("field,value", [("valor", [{"ev": 1}]), ("tem_valor", True),
                                         ("sofa_id", 123), ("game_state", "started"),
                                         ("captured_at", "2026-09-30T14:30:00"),
                                         ("inicio_iso", NOW.isoformat()),
                                         ("expires_at", (NOW + timedelta(hours=5)).isoformat())])
def test_public_refuses_mixed_or_unproven_game(field, value):
    board = public_board()
    board["jogos"][0][field] = value
    assert runtime.public_odds_only_reasons(board, NOW)


@pytest.mark.parametrize("field,value", [("pricing", {"x": 1}), ("jogos", []),
                                         ("rebuilt_at", "2026-09-30T15:00:00"),
                                         ("gerado_iso", NOW.isoformat())])
def test_public_refuses_claims_without_contract(field, value):
    board = public_board()
    board[field] = value
    assert runtime.public_odds_only_reasons(board, NOW)


def test_forced_hold_does_not_depend_on_single_sofa_success():
    with patch.dict(os.environ, {"MESA_ODDS_ONLY": "1"}), patch.object(gate_board, "load_sofa_state") as load:
        assert runtime.contingency_reasons()
        load.assert_not_called()


def test_green_status_cannot_certify_missing_fixture(tmp_path):
    with patch.object(gate_board, "ROOT", tmp_path):
        state = gate_board.load_sofa_state({"pointer_valid": True, "pointer_age_h": 0})
    assert state["pointer_valid"] is False


def test_fixture_path_cannot_escape_root(tmp_path):
    folder = tmp_path / "data/fixtures"
    folder.mkdir(parents=True)
    (folder / "sofa_latest.json").write_text(json.dumps({"file": "../../untrusted.json", "n": 1, "at": NOW.isoformat()}))
    (tmp_path / "untrusted.json").write_text('{"fixtures":[{}]}')
    with patch.object(gate_board, "ROOT", tmp_path):
        assert gate_board.load_sofa_state({})["pointer_valid"] is False


def test_manifest_assembly_time_does_not_relabel_capture(tmp_path):
    board = public_board()
    before = copy.deepcopy(board)
    assert build_manifest._ts_of("board", board, tmp_path / "unused") == NOW
    assert board == before
    del board["rebuilt_at"]
    with pytest.raises(ValueError):
        build_manifest._ts_of("board", board, tmp_path / "unused")


def test_pipeline_has_hold_and_revalidation_after_reconciliation():
    root = Path(__file__).parent
    workflow = (root / ".github/workflows/valor.yml").read_text()
    persist = (root / "persist_snapshot.sh").read_text()
    assert "MESA_ODDS_ONLY: '1'" in workflow
    assert persist.index("python contingency_runtime.py") < persist.index("  stage\n")
    assert "python gate_board.py || return 1" in persist
    assert 'SOFA_GATE_MAX_AGE_H' not in workflow


def test_real_projection_gate_and_refresh_after_feeder(tmp_path, monkeypatch):
    from test_odds_only import write_snapshot, event, NOW as source_now
    from odds_only import build_odds_only, odds_only_reasons
    import odds_only
    # Controlled runtime clock, no network and a real pointer/JSONL fixture.
    monkeypatch.setattr(odds_only, "_now", lambda value=None: value or source_now)
    monkeypatch.setattr(runtime, "public_odds_only_reasons", lambda board: [])
    write_snapshot(tmp_path)
    initial = build_odds_only(tmp_path)
    path = tmp_path / "valor/data/board.js"
    path.parent.mkdir(parents=True)
    path.write_text("window.BOARD=" + json.dumps(initial) + ";")
    assert runtime.refresh_before_persist(tmp_path)
    changed = event()
    changed["mercados"]["Cartões"][0]["over"] = 2.1
    write_snapshot(tmp_path, events=[changed])
    assert odds_only_reasons(initial, tmp_path)
    assert runtime.refresh_before_persist(tmp_path)
    current = gate_board.parse_board(path.read_text())
    assert current["jogos"][0]["mercados"]["Cartões"]["Superbet"][0]["over"] == 2.1
    assert not odds_only_reasons(current, tmp_path)


def test_forced_hold_refuses_full_board_during_persist(tmp_path, monkeypatch):
    path = tmp_path / "valor/data/board.js"
    path.parent.mkdir(parents=True)
    path.write_text("window.BOARD={};")
    monkeypatch.setenv("MESA_ODDS_ONLY", "1")
    with pytest.raises(ValueError, match="contingência obrigatória"):
        runtime.refresh_before_persist(tmp_path)


def test_signal_judge_never_loads_models_in_contingency(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "mesa_bot"))
    from mesa_bot import judge
    def forbidden():
        raise AssertionError("must not instantiate model")
    monkeypatch.setattr(judge, "_ctx", forbidden)
    assert judge.judge_board(public_board()) == []
    from mesa_bot.signals import flatten_signals
    poisoned = public_board()
    poisoned["jogos"][0]["valor"] = [{"actionable": True, "ev": .3}]
    assert flatten_signals(poisoned) == []
