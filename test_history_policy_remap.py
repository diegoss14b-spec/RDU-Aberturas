"""Closed rename proof: positive replay and hostile alias/value regressions."""
import copy
import gzip
import json

import pytest

import history_policy as hp
import history_policy_remap as hr
from test_history_policy_transition import env  # shared real manifest/deploy gate fixture


OLD = "book|2026-09-08|home|away|Gols|2.5|over"
TARGET = "book|sofa:12345|Gols|2.5|over"


@pytest.fixture
def remap_env(env, monkeypatch):
    ids = env["ids"][:-1] + [OLD]
    inventory = gzip.compress(json.dumps({"raw_ids": ids, "settled_ids": ids[:6]}).encode(), mtime=0)
    baseline = env["baseline"]
    meta = json.loads((baseline / "baseline.json").read_text())
    source = {"path": "data/odds_history/keys/original.json", "sha256": "a" * 64}
    meta.update(inventory_sha256=hp.sha(inventory), source_files=[source])
    (baseline / "baseline.json").write_text(json.dumps(meta))
    (baseline / "identities.json.gz").write_bytes(inventory)
    before = {"status": "open", "kickoff": "2026-09-08T15:00:00-03:00", "sofa_id": None,
              "open_odd": 2.1, "open_ts": "2026-09-07T15:00:00-03:00", "min_odd": 1.9,
              "max_odd": 2.2, "n_obs": 9, "last_odd": 2.0, "last_ts": "2026-09-08T10:00:00-03:00"}
    audited = {**before, "sofa_id": "12345", "merged_from_keys": [OLD],
               "open_ts": "2026-09-07T14:00:00-03:00"}
    entry = {"old_key": OLD, "target_key": TARGET, "source_path": source["path"],
             "source_file_sha256": source["sha256"], "original_record": before,
             "original_record_sha256": hp.digest(before), "audited_record": audited,
             "audited_record_sha256": hp.digest(audited), "audited_commit": "b" * 40,
             "target_path": "data/odds_history/keys/current.json"}
    evidence = {"schema": 1, "baseline_sha256": hp.digest(meta), "source_commit": meta["source_commit"],
                "entry_count": 1, "entries": [entry], "audited_commits": ["b" * 40],
                "target_files": [{"commit": "b" * 40, "path": entry["target_path"], "sha256": "c" * 64}]}

    def save_evidence():
        raw = json.dumps(evidence).encode()
        (baseline / hr.EVIDENCE_FILE).write_bytes(raw)
        monkeypatch.setattr(hr, "EVIDENCE_SHA256", hp.sha(raw))

    save_evidence()
    records = {key: {"status": "settled" if key in ids[:6] else "open"} for key in ids if key != OLD}
    records.update({TARGET: copy.deepcopy(audited), "new": {"status": "open"}})

    def proof():
        return hp.preservation_proof(records, [key for key, record in records.items()
                                              if record["status"] == "settled"], records=records)

    env.update(records=records, proof=proof, evidence=evidence, save_evidence=save_evidence,
               ids=ids, original=before, audited=audited)
    return env


def test_closed_remap_validates_actual_records_and_real_counts(remap_env):
    proof = remap_env["proof"]()
    assert proof["schema"] == 2
    assert proof["raw_count"] == 9  # NOT 10: aliases never count as raw records
    assert proof["settled_count"] == 6
    assert proof["literal_missing_raw_count"] == proof["remapped_raw_count"] == 1
    assert proof["missing_raw_count"] == proof["missing_settled_count"] == 0
    assert proof["remapped_settled_count"] == 0
    assert hp.validate_preservation(proof)
    remap_env["target"]["history_preservation"] = proof
    assert remap_env["gate"]() is None


def test_old_interface_stays_literal_only_even_for_approved_mapping(remap_env):
    records = remap_env["records"]
    p = hp.preservation_proof(records, remap_env["ids"][:6])
    assert p["schema"] == 1 and p["missing_raw_count"] == 1
    with pytest.raises(ValueError, match="identities lost"):
        hp.validate_preservation(p)


def test_proof_is_detached_from_later_build_view_mutations(remap_env):
    proof = remap_env["proof"]()
    sealed = copy.deepcopy(proof)
    remap_env["records"][TARGET]["open_odd"] = 99
    remap_env["records"][TARGET]["merged_from_keys"].append("later-view-alias")
    assert proof == sealed
    assert hp.validate_preservation(proof)


def test_reintroduced_literal_identity_needs_no_remap_or_extra_count(remap_env):
    remap_env["records"][OLD] = copy.deepcopy(remap_env["original"])
    proof = remap_env["proof"]()
    assert proof["schema"] == 1
    assert proof["raw_count"] == len(remap_env["records"]) == 10
    assert proof["missing_raw_count"] == 0
    assert "remapped_raw_count" not in proof
    assert hp.validate_preservation(proof)


@pytest.mark.parametrize("field,value,message", [
    ("kickoff", "2026-09-09T15:00:00-03:00", "fixture/kickoff"),
    ("sofa_id", "999", "fixture/kickoff"),
    ("open_odd", 2.11, "opening odd"),
    ("open_odd", True, "invalid remap value"),
    ("open_odd", float("nan"), "invalid remap value"),
    ("open_ts", "2026-09-07T13:00:00-03:00", "opening timestamp"),
    ("min_odd", 1.91, "minimum observation"),
    ("max_odd", 2.19, "maximum observation"),
    ("n_obs", 8, "observation counter"),
    ("last_ts", "2026-09-08T09:59:00-03:00", "last observation"),
    ("merged_from_keys", [], "ambiguous/missing"),
    ("merged_from_keys", [OLD, OLD], "ambiguous/missing"),
])
def test_changed_or_lost_record_values_fail_closed(remap_env, field, value, message):
    remap_env["records"][TARGET][field] = value
    with pytest.raises(ValueError, match=message):
        remap_env["proof"]()


@pytest.mark.parametrize("target", [
    "otherbook|sofa:12345|Gols|2.5|over", "book|sofa:12345|Cartões|2.5|over",
    "book|sofa:12345|Gols|3.5|over", "book|sofa:12345|Gols|2.5|under",
    "book|sofa:67890|Gols|2.5|over",
])
def test_fake_alias_changed_house_market_line_side_or_successor_blocks(remap_env, target):
    remap_env["records"][target] = remap_env["records"].pop(TARGET)
    with pytest.raises(ValueError, match="unapproved successor"):
        remap_env["proof"]()


def test_changed_market_in_rehashed_certificate_still_blocks(remap_env):
    remap_env["evidence"]["entries"][0]["target_key"] = "book|sofa:12345|Gols|3.5|over"
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="market contract"):
        remap_env["proof"]()


def test_missing_successor_is_not_alias_coverage(remap_env):
    remap_env["records"].pop(TARGET)
    with pytest.raises(ValueError, match="ambiguous/missing"):
        remap_env["proof"]()


def test_multiple_lineage_owners_fail_even_when_pinned_target_exists(remap_env):
    remap_env["records"]["fake"] = copy.deepcopy(remap_env["records"][TARGET])
    with pytest.raises(ValueError, match="ambiguous/missing"):
        remap_env["proof"]()


def test_unknown_lost_record_with_fake_alias_remains_missing(remap_env):
    remap_env["records"].pop("key6")
    remap_env["records"]["replacement"] = {"status": "open", "merged_from_keys": ["key6"]}
    proof = remap_env["proof"]()
    assert proof["missing_raw_count"] == 1
    assert proof["missing_raw_examples"] == ["key6"]
    with pytest.raises(ValueError, match="identities lost"):
        hp.validate_preservation(proof)


def test_settled_loss_cannot_be_approved_by_raw_rename(remap_env):
    remap_env["records"].pop("key0")
    remap_env["records"]["replacement"] = {"status": "settled", "merged_from_keys": ["key0"]}
    with pytest.raises(ValueError, match="identities lost"):
        hp.validate_preservation(remap_env["proof"]())


def test_settled_demotion_at_same_raw_identity_blocks(remap_env):
    remap_env["records"]["key0"]["status"] = "open"
    remap_env["records"]["new"]["status"] = "settled"
    with pytest.raises(ValueError, match="missing_settled_count"):
        hp.validate_preservation(remap_env["proof"]())


def test_raw_count_floor_cannot_be_relaxed_by_closed_rename(remap_env):
    remap_env["records"].pop("new")
    # Add the preexisting target to the frozen inventory: a closed merge now
    # retains all baseline identities semantically but reduces real row count.
    baseline = remap_env["baseline"]
    inv = json.loads(gzip.decompress((baseline / "identities.json.gz").read_bytes()))
    inv["raw_ids"].append(TARGET)
    raw = gzip.compress(json.dumps(inv).encode(), mtime=0)
    meta = json.loads((baseline / "baseline.json").read_text())
    meta.update(raw_count=9, inventory_sha256=hp.sha(raw))
    (baseline / "identities.json.gz").write_bytes(raw)
    (baseline / "baseline.json").write_text(json.dumps(meta))
    remap_env["evidence"]["baseline_sha256"] = hp.digest(meta)
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="raw history count fell"):
        hp.validate_preservation(remap_env["proof"]())


def test_wrong_certificate_hash_blocks(remap_env):
    with (remap_env["baseline"] / hr.EVIDENCE_FILE).open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(ValueError, match="evidence missing/altered"):
        remap_env["proof"]()


@pytest.mark.parametrize("field", ["baseline_sha256", "source_commit"])
def test_rehashed_certificate_from_wrong_baseline_blocks(remap_env, field):
    remap_env["evidence"][field] = "0" * 64
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="wrong baseline/source"):
        remap_env["proof"]()


@pytest.mark.parametrize("field", ["source_file_sha256", "original_record_sha256", "audited_record_sha256"])
def test_wrong_original_source_or_record_hash_blocks(remap_env, field):
    remap_env["evidence"]["entries"][0][field] = "0" * 64
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="source record/hash"):
        remap_env["proof"]()


def test_successor_receipt_cannot_claim_another_snapshot(remap_env):
    remap_env["evidence"]["entries"][0]["audited_commit"] = "d" * 40
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="successor snapshot receipt"):
        remap_env["proof"]()


def test_duplicate_mapping_target_in_certificate_blocks(remap_env):
    duplicate = copy.deepcopy(remap_env["evidence"]["entries"][0])
    duplicate["old_key"] = "key6"
    remap_env["evidence"]["entries"].append(duplicate)
    remap_env["evidence"]["entry_count"] += 1
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="reused target"):
        remap_env["proof"]()


def rehash(proof):
    proof["report_sha256"] = hp.digest({k: v for k, v in proof.items() if k != "report_sha256"})


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_record_hash", "wrong_evidence_hash", "wrong_count", "changed_record"])
def test_rehashed_report_cannot_bypass_closed_witness_validation(remap_env, mutation):
    proof = remap_env["proof"]()
    if mutation == "duplicate":
        proof["remap_witnesses"] *= 2
    elif mutation == "wrong_record_hash":
        proof["remap_witnesses"][0]["record_sha256"] = "0" * 64
    elif mutation == "wrong_evidence_hash":
        proof["remap_evidence_sha256"] = "0" * 64
    elif mutation == "wrong_count":
        proof["remapped_raw_count"] = 2
    else:
        witness = proof["remap_witnesses"][0]
        witness["record"]["open_odd"] = 1.5
        witness["record_sha256"] = hp.digest(witness["record"])
    rehash(proof)
    with pytest.raises(ValueError):
        hp.validate_preservation(proof)


def test_original_settled_ids_cannot_enter_remap_certificate(remap_env):
    remap_env["evidence"]["entries"][0]["old_key"] = "key0"
    remap_env["save_evidence"]()
    with pytest.raises(ValueError, match="ineligible original"):
        remap_env["proof"]()


def test_new_observations_can_extend_the_audited_record(remap_env):
    remap_env["records"][TARGET].update(n_obs=11, min_odd=1.8, max_odd=2.4,
                                        last_ts="2026-09-08T11:00:00-03:00", status="settled")
    assert hp.validate_preservation(remap_env["proof"]())
