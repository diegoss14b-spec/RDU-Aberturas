"""Versioned CLV eligibility and a closed, evidence-bound legacy transition.

The baseline is an inventory, NOT an approval flag. Its exact source manifest
must still be live, and every raw/settled identity must survive the new build.
"""
import gzip
import hashlib
import json
from pathlib import Path

POLICY = {"id": "observed-clock-strict-close/v1", "schema": 2,
          "max_close_age_minutes": 60, "requires_verified_observed_at": True,
          "legacy_history_preserved": True}
BASELINE_DIR = Path(__file__).resolve().parent / "history_policy_baseline"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def counts(history):
    bank = history.get("banco") or {}
    out = {}
    for key in ("monitoradas", "liquidadas", "clv_validas"):
        value = bank.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("history counter missing/invalid: " + key)
        out[key] = value
    if out["clv_validas"] > out["liquidadas"] or out["liquidadas"] > out["monitoradas"]:
        raise ValueError("inconsistent history counters")
    return out


def load_baseline(directory=None):
    directory = Path(directory or BASELINE_DIR)
    meta = json.loads((directory / "baseline.json").read_text(encoding="utf-8"))
    raw = (directory / "identities.json.gz").read_bytes()
    if meta.get("schema") != 1 or meta.get("target_policy") != POLICY or sha(raw) != meta.get("inventory_sha256"):
        raise ValueError("invalid or altered CLV baseline")
    inventory = json.loads(gzip.decompress(raw))
    raw_ids, settled_ids = set(inventory["raw_ids"]), set(inventory["settled_ids"])
    if not settled_ids <= raw_ids or len(raw_ids) != meta.get("raw_count") or len(settled_ids) != meta.get("settled_count"):
        raise ValueError("inconsistent CLV baseline inventory")
    return meta, raw_ids, settled_ids


def preservation_proof(raw_ids, settled_ids, directory=None):
    """Pure set comparison; no mutation, remapping or deletion of historical keys."""
    meta, baseline_ids, baseline_settled = load_baseline(directory)
    raw_ids, settled_ids = set(raw_ids), set(settled_ids)
    body = {"schema": 1, "policy_id": POLICY["id"],
            "baseline_sha256": digest(meta),
            "source_commit": meta["source_commit"],
            "source_manifest_sha256": meta["source_manifest_sha256"],
            "source_history_sha256": meta["source_history_sha256"],
            "baseline_raw_count": len(baseline_ids), "baseline_settled_count": len(baseline_settled),
            "raw_count": len(raw_ids), "settled_count": len(settled_ids),
            "raw_ids_sha256": digest(sorted(raw_ids)),
            "settled_ids_sha256": digest(sorted(settled_ids)),
            "missing_raw_count": len(baseline_ids - raw_ids),
            "missing_settled_count": len(baseline_settled - settled_ids),
            "missing_raw_examples": sorted(baseline_ids - raw_ids)[:5],
            "missing_settled_examples": sorted(baseline_settled - settled_ids)[:5]}
    return {**body, "report_sha256": digest(body)}


def contract(history, history_sha256):
    policy = history.get("clv_policy")
    if policy is None:
        return None
    if policy != POLICY:
        raise ValueError("unsupported CLV policy")
    proof = history.get("history_preservation")
    return {"schema": 1, "policy": POLICY, "policy_sha256": digest(POLICY),
            "history_sha256": history_sha256, "counts": counts(history),
            "preservation": proof}


def validate_preservation(proof, directory=None):
    """Every v1 build must retain the frozen legacy universe, not just its size."""
    meta, _ids, _settled = load_baseline(directory)
    proof = proof or {}
    body = {k: v for k, v in proof.items() if k != "report_sha256"}
    if proof.get("report_sha256") != digest(body) or proof.get("baseline_sha256") != digest(meta):
        raise ValueError("preservation report missing/altered or wrong baseline")
    expected = {"schema": 1, "policy_id": POLICY["id"], "source_commit": meta["source_commit"],
                "source_manifest_sha256": meta["source_manifest_sha256"],
                "source_history_sha256": meta["source_history_sha256"],
                "baseline_raw_count": meta["raw_count"], "baseline_settled_count": meta["settled_count"]}
    if any(proof.get(k) != v for k, v in expected.items()):
        raise ValueError("preservation report source/schema mismatch")
    for key in ("missing_raw_count", "missing_settled_count"):
        if type(proof.get(key)) is not int or proof[key] != 0:
            raise ValueError("historical identities lost: " + key)
    for key in ("raw_count", "settled_count"):
        if type(proof.get(key)) is not int or proof[key] < meta[key]:
            raise ValueError("raw history count fell: " + key)
    return meta


def transition_report(target_contract, live_history, live_manifest_raw, live_history_raw,
                      target_build_id, directory=None):
    """Return a build-specific proof, or raise. No generic approval file exists."""
    if live_history.get("clv_policy") is not None:
        raise ValueError("transition only permits legacy without CLV policy")
    if not target_contract or target_contract.get("policy") != POLICY:
        raise ValueError("target must use exact observed-clock policy")
    meta = validate_preservation(target_contract.get("preservation"), directory)
    if sha(live_manifest_raw) != meta["source_manifest_sha256"] or sha(live_history_raw) != meta["source_history_sha256"]:
        raise ValueError("live legacy snapshot differs from verified Git baseline")
    proof = target_contract.get("preservation") or {}
    before, after = counts(live_history), target_contract["counts"]
    if before != meta["public_counts"]:
        raise ValueError("baseline public counters mismatch")
    if any(after[k] < before[k] for k in ("monitoradas", "liquidadas")):
        raise ValueError("raw/settled public history fell during policy transition")
    report = {"schema": 1, "transition": "legacy-to-" + POLICY["id"],
              "target_build_id": target_build_id, "source_commit": meta["source_commit"],
              "source_manifest_sha256": sha(live_manifest_raw),
              "source_history_sha256": sha(live_history_raw),
              "target_history_sha256": target_contract["history_sha256"],
              "policy_sha256": digest(POLICY), "preservation_report_sha256": proof["report_sha256"],
              "before": before, "after": after}
    return {**report, "report_sha256": digest(report)}
