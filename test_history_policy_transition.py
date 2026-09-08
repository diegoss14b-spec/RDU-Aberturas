"""Controls for the one-time eligibility change, never a data-loss bypass."""
import copy
import gzip
import json
from datetime import datetime, timezone

import pytest

import build_manifest
import deploy
import history_policy as hp
from manifest_common import artifact_valid_count, sha256_bytes
from test_manifest import _fake_valor


def window(name, value):
    return ("window." + name + "=" + json.dumps(value, ensure_ascii=False) + ";").encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    gi = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = {"gerado_iso": gi, "banco": {"monitoradas": 8, "liquidadas": 6, "clv_validas": 5}}
    old_raw = window("HIST", old)
    live = {"manifest_version": 1, "build_id": "old", "artifacts": {"/data/history.js": {
        "count": 6, "valid_count": 5, "sha256": hp.sha(old_raw)}}}
    live_raw = window("MANIFEST", live)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    ids = ["key" + str(i) for i in range(8)]
    inventory = gzip.compress(json.dumps({"raw_ids": ids, "settled_ids": ids[:6]}).encode(), mtime=0)
    meta = {"schema": 1, "target_policy": hp.POLICY, "source_commit": "a" * 40,
            "source_manifest_sha256": hp.sha(live_raw), "source_history_sha256": hp.sha(old_raw),
            "inventory_sha256": hp.sha(inventory), "raw_count": 8, "settled_count": 6,
            "public_counts": old["banco"]}
    (baseline / "baseline.json").write_text(json.dumps(meta))
    (baseline / "identities.json.gz").write_bytes(inventory)
    monkeypatch.setattr(hp, "BASELINE_DIR", baseline)
    target = {"gerado_iso": gi, "clv_policy": hp.POLICY,
              "banco": {"monitoradas": 9, "liquidadas": 6, "clv_validas": 0},
              "history_preservation": hp.preservation_proof(ids + ["new"], ids[:6])}
    valor = tmp_path / "valor"
    _fake_valor(valor, gi)
    monkeypatch.setattr(build_manifest, "VALOR", valor)
    monkeypatch.setattr(deploy, "DEPLOY_LIVE_BASE", "https://readonly.invalid")
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    remote = {"manifest": live_raw, "history": old_raw}
    monkeypatch.setattr(deploy, "_fetch_text", lambda url, timeout=20:
                        remote["manifest" if url.endswith("manifest.js") else "history"].decode())

    def gate(value=None):
        (valor / "data/history.js").write_bytes(window("HIST", target if value is None else value))
        assert build_manifest.main() == 0
        return deploy.manifest_gate(valor)
    return {"target": target, "old": old, "live": live, "remote": remote, "gate": gate,
            "ids": ids, "valor": valor, "baseline": baseline, "root": tmp_path}


def strict_remote(env, valid=5):
    hist = copy.deepcopy(env["target"])
    hist["banco"] = {"monitoradas": 8, "liquidadas": 6, "clv_validas": valid}
    raw = window("HIST", hist)
    live = copy.deepcopy(env["live"])
    live["artifacts"]["/data/history.js"].update(
        count=6, valid_count=valid, sha256=hp.sha(raw), history_contract=hp.contract(hist, hp.sha(raw)))
    env["remote"].update(history=raw, manifest=window("MANIFEST", live))


def test_closed_legacy_transition_accepts_only_reclassification(env, capsys):
    assert env["gate"]() is None
    log = capsys.readouterr().out
    assert "CLV_POLICY_TRANSITION" in log and '"clv_validas": 0' in log
    assert "target_history_sha256" in log and "target_build_id" in log


def test_zero_strict_count_does_not_fall_back_to_legacy_head():
    assert artifact_valid_count("history", {"banco": {"clv_validas": 0}, "head": {"n_valid": 99}}) == 0


def test_same_count_replaced_raw_identity_blocks(env):
    ids = env["ids"]
    env["target"]["history_preservation"] = hp.preservation_proof(ids[1:] + ["replacement"], ids[1:7])
    assert "identities lost" in env["gate"]()


def test_settled_identity_replaced_at_same_count_blocks(env):
    ids = env["ids"]
    env["target"]["history_preservation"] = hp.preservation_proof(ids, ids[1:7])
    assert "missing_settled_count" in env["gate"]()


@pytest.mark.parametrize("field,value", [("monitoradas", 7), ("liquidadas", 5)])
def test_raw_public_counter_loss_blocks(env, field, value):
    env["target"]["banco"][field] = value
    assert "history count fell" in env["gate"]()


def test_same_strict_policy_never_uses_transition_exception(env):
    strict_remote(env)
    assert "encolheu" in env["gate"]()


def test_strict_zero_to_zero_still_checks_raw_counts(env):
    strict_remote(env, valid=0)
    env["target"]["banco"]["monitoradas"] = 7
    assert "history count fell" in env["gate"]()


def test_same_strict_policy_with_preserved_counts_passes(env):
    strict_remote(env, valid=0)
    assert env["gate"]() is None


def test_altered_report_blocks(env):
    env["target"]["history_preservation"]["raw_count"] += 1
    assert "report missing/altered" in env["gate"]()


def test_report_from_other_baseline_blocks_even_with_rehashed_body(env):
    p = env["target"]["history_preservation"]
    p["source_commit"] = "b" * 40
    p["report_sha256"] = hp.digest({k: v for k, v in p.items() if k != "report_sha256"})
    assert "source/schema mismatch" in env["gate"]()


def test_unknown_target_policy_fails_before_manifest_write(env):
    value = copy.deepcopy(env["target"])
    value["clv_policy"]["max_close_age_minutes"] = 90
    # Do not mutate the module's shared policy constant through the test fixture.
    (env["valor"] / "data/history.js").write_bytes(window("HIST", value))
    assert build_manifest.main() == 1


def test_live_history_must_match_live_manifest_hash(env):
    env["remote"]["history"] += b" "
    assert "live history hash differs" in env["gate"]()


def test_other_live_legacy_build_requires_new_verified_baseline(env):
    other = copy.deepcopy(env["live"])
    other["build_id"] = "different-legacy-build"
    env["remote"]["manifest"] = window("MANIFEST", other)
    assert "differs from verified Git baseline" in env["gate"]()


def test_missing_baseline_fails_closed(env):
    (env["baseline"] / "baseline.json").unlink()
    assert "baseline.json" in env["gate"]()


def test_strict_to_strict_rejects_missing_preservation_evidence(env):
    strict_remote(env, valid=0)
    env["target"]["history_preservation"] = {"error": "missing source inventory"}
    assert "report missing/altered" in env["gate"]()


def test_strict_to_strict_rejects_replaced_legacy_id_at_same_size(env):
    strict_remote(env, valid=0)
    ids = env["ids"]
    env["target"]["history_preservation"] = hp.preservation_proof(ids[1:] + ["replacement"], ids[1:7])
    assert "identities lost" in env["gate"]()


def test_arbitrary_approval_file_no_longer_bypasses_decline(env):
    strict_remote(env)
    directory = env["root"] / "data/odds/_status"
    directory.mkdir(parents=True)
    (directory / "history_shrink_approved.json").write_text("{}")
    assert "encolheu" in env["gate"]()


def test_policy_rollback_to_legacy_is_blocked(env):
    strict_remote(env)
    assert "cannot roll back" in env["gate"](env["old"])


def test_target_contract_cannot_be_reused_for_another_artifact(env):
    assert env["gate"]() is None
    path = env["valor"] / "data/manifest.js"
    man = json.loads(path.read_text().split("=", 1)[1].rstrip(";"))
    man["artifacts"]["/data/history.js"]["history_contract"]["history_sha256"] = "0" * 64
    path.write_bytes(window("MANIFEST", man))
    assert "contrato" in deploy.manifest_gate(env["valor"])
