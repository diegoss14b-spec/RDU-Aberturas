"""Superbet coverage diagnostics retain identity/clocks and never change health."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import build_ops as ops
import capture_common


@pytest.fixture
def files(monkeypatch, tmp_path):
    start = datetime(2026, 9, 12, 6, tzinfo=timezone.utc)
    end = start + timedelta(minutes=8)
    monkeypatch.setattr(ops, "STATUS", tmp_path)
    monkeypatch.setattr(ops, "now_brt", lambda: end.astimezone(ops.BRT))
    monkeypatch.setattr(ops, "source_state", lambda status, house: "ok" if status.get("ok") else "failed")
    full = {"_pointer": "superbet_latest_full.json", "at": "2026-09-12T01:00:00-03:00",
            "_actual_n": 262, "captured_by": "actions", "market_counts": {"Faltas": 26}}
    monkeypatch.setattr(capture_common, "resolve_odds_pointer", lambda house, **kw: (full, None) if house == "superbet" else (None, None))
    status = {
        "ok": True, "attempted": True, "mode": "full", "n_events": 400,
        "ts_utc": end.isoformat(), "ts_brt": "2026-09-12 03:08",
        "attempt_started_epoch": start.timestamp(), "attempt_finished_epoch": end.timestamp(),
        "duration_sec": 480, "pointer_file": "superbet_unique.jsonl", "pointer_valid": True,
    }
    diagnostic = {
        "mode": "full", "at": start.isoformat(), "updated_at": end.isoformat(),
        "complete": True, "inventory_complete": True, "n_list": 1837, "n_eligible": 1375,
        "n_scheduled": 1375, "n_det": 1375, "n_ok": 1370, "n_unavailable": 5,
        "n_failed": 0, "n_unattempted": 0, "n_out": 400, "detail_requests": 1402,
        "horizon_h": 30, "budget_seconds": 840, "output_file": "superbet_unique.jsonl",
    }

    def write(status_value=None, diag_value=None):
        (tmp_path / "superbet.json").write_text(json.dumps(status if status_value is None else status_value))
        (tmp_path / "superbet_diag.json").write_text(json.dumps(diagnostic if diag_value is None else diag_value))
        return next(row for row in ops.load_casa_status() if row["id"] == "superbet")

    return status, diagnostic, write, tmp_path


def test_complete_matching_diagnostic_uses_events_not_http_retries(files):
    status, diagnostic, write, _ = files
    row = write()
    assert row["discovery"] == {"inventory": 1837, "selected": 1375, "attempted": 1375, "not_selected": 462}
    assert row["coverage"]["detail_requests"] == 1402
    assert row["coverage"]["matches_status"] is True
    for key in ("mode", "at", "updated_at", "complete", "n_failed", "n_unattempted"):
        assert row["coverage"][key] == diagnostic[key]
    assert row["capture_mode"] == status["mode"]
    assert row["ts_brt"] == status["ts_brt"]
    assert row["full_at"] == "2026-09-12T01:00:00-03:00"
    assert row["full_events"] == 262 and row["full_age_min"] == 128
    assert row["ok"] and row["source_state"] == "ok"


def test_partial_retains_explicit_counters_without_inventing_consulted(files):
    status, diagnostic, write, _ = files
    status.update(ok=False, pointer_file=None, error="captura parcial Superbet", error_class="CaptureBudgetExceeded")
    diagnostic.update(complete=False, n_det=903, n_ok=900, n_unavailable=0, n_failed=3,
                      n_unattempted=472, budget_exhausted=True, detail_requests=907)
    row = write()
    assert row["discovery"] == {}
    assert row["coverage"]["matches_status"] is True
    assert row["coverage"]["n_failed"] == 3 and row["coverage"]["n_unattempted"] == 472
    assert row["coverage"]["complete"] is False
    assert not row["ok"] and row["source_state"] == "failed"
    assert row["full_at"] == "2026-09-12T01:00:00-03:00"
    assert row["error"] == "captura parcial Superbet"


@pytest.mark.parametrize("change,reason", [
    ({"mode": "close"}, "mode_mismatch"),
    ({"at": "2026-09-12T03:00:00Z", "updated_at": "2026-09-12T03:08:00Z"}, "outside_attempt_window"),
    ({"output_file": "superbet_previous.jsonl"}, "output_file_mismatch"),
    ({"updated_at": None}, "invalid_diagnostic_clock"),
])
def test_old_or_mismatched_diagnostic_is_never_current(files, change, reason):
    _, diagnostic, write, _ = files
    diagnostic.update(change)
    row = write()
    assert row["coverage"]["matches_status"] is False
    assert row["coverage"]["status_match_reason"] == reason
    assert row["discovery"] == {}
    assert row["ok"] and row["source_state"] == "ok"


def test_skip_does_not_relabel_previous_capture_as_new(files):
    status, _, write, _ = files
    status.update(attempted=False, skipped_reason="full_stride")
    row = write()
    assert row["coverage"]["status_match_reason"] == "no_new_capture"
    assert row["discovery"] == {} and row["attempted"] is False


def test_pending_attempt_rejects_previous_diagnostic(files):
    status, _, write, _ = files
    status.update(ok=False, error_class="Pending", pointer_file=None,
                  run_started_epoch=status["attempt_finished_epoch"], duration_sec=0)
    status.pop("attempt_started_epoch")
    status.pop("attempt_finished_epoch")
    row = write()
    assert row["coverage"]["matches_status"] is False
    assert row["discovery"] == {}


def test_standalone_capture_can_match_its_unique_output_without_attempt_epochs(files):
    status, _, write, _ = files
    for key in ("attempt_started_epoch", "attempt_finished_epoch", "duration_sec"):
        status.pop(key)
    row = write()
    assert row["coverage"]["matches_status"] is True
    assert row["coverage"]["status_match_reason"] == "unique_output_file"


def test_no_attempt_window_or_output_identity_remains_unverified(files):
    status, _, write, _ = files
    for key in ("attempt_started_epoch", "attempt_finished_epoch", "duration_sec", "pointer_file"):
        status.pop(key)
    row = write()
    assert row["coverage"]["matches_status"] is False
    assert row["coverage"]["status_match_reason"] == "unverified_attempt"
    assert row["discovery"] == {}


def test_actual_ops_renderer_shows_mapped_event_counts(files):
    node = os.environ.get("RDU_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for actual Ops rendering regression")
    _, _, write, _ = files
    script = r'''
const fs=require('fs'),vm=require('vm');
const root={innerHTML:''};
const context={window:{setInterval(){},OPS:{casas:[JSON.parse(process.argv[2])]}},
  document:{getElementById(){return root;}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.window.renderOps();
process.stdout.write(root.innerHTML);
'''
    result = subprocess.run(
        [node, "-e", script, str(ops.ROOT / "valor/js/ops.js"), json.dumps(write())],
        capture_output=True, text=True, timeout=30, check=True,
    )
    assert "Inventário: 1837 · selecionados: 1375 · consultados: 1375 · fora da seleção: 462" in result.stdout
    assert "consultados: 1402" not in result.stdout


@pytest.mark.parametrize("field,value", [("n_det", 1374), ("n_failed", 1), ("n_unattempted", 1), ("n_list", 900), ("n_ok", None)])
def test_inconsistent_complete_counters_are_not_projected(files, field, value):
    _, diagnostic, write, _ = files
    diagnostic[field] = value
    assert write()["discovery"] == {}


def test_missing_or_invalid_diag_does_not_change_other_houses(files):
    _, _, write, directory = files
    write()
    (directory / "betano_discovery.json").write_text(json.dumps({"metrics": {"inventory": 50, "selected": 10}}))
    for invalid in (None, [], "bad"):
        (directory / "superbet_diag.json").write_text(json.dumps(invalid))
        rows = {row["id"]: row for row in ops.load_casa_status()}
        assert rows["superbet"]["coverage"] == {}
        assert rows["superbet"]["discovery"] == {}
        assert rows["betano"]["discovery"] == {"inventory": 50, "selected": 10}
        assert "coverage" not in rows["betano"]
    (directory / "superbet_diag.json").unlink()
    assert next(row for row in ops.load_casa_status() if row["id"] == "superbet")["coverage"] == {}
