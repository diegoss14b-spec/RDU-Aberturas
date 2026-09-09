"""Independent controls for durable, source-bound history identity preservation."""
import copy
import gzip
import json
import socket
import subprocess
import urllib.request
from datetime import datetime

import pytest

import build_manifest
import deploy
import history_policy as hp
import history_policy_dynamic as hd
import history_policy_remap as hr
import history_preservation_inventory as inventory
from test_history_policy_transition import env, window


OLD = "book|2026-09-08|alpha town|beta united|Gols|2.5|over"
TARGET = "book|sofa:12345|Gols|2.5|over"
LEGACY_TARGET = "book|2026-09-09|alpha town|beta united|Gols|2.5|over"


def test_committed_source_index_is_available_and_authenticated_before_capture():
    meta, raw, settled = hp.load_baseline()
    evidence_hash, entries = hd.load_evidence(meta, raw, settled, hp.BASELINE_DIR)
    assert evidence_hash == hd.EVIDENCE_SHA256
    assert len(entries) == 1050
    assert not set(entries) & settled


@pytest.fixture
def dynamic_env(env, monkeypatch):
    baseline = env["baseline"]
    ids = env["ids"][:-1] + [OLD]
    raw = gzip.compress(json.dumps({"raw_ids": ids, "settled_ids": ids[:6]}).encode(), mtime=0)
    source = {"path": "data/odds_history/keys/original.json", "sha256": "a" * 64}
    meta = json.loads((baseline / "baseline.json").read_text())
    meta.update(inventory_sha256=hp.sha(raw), source_files=[source])
    (baseline / "baseline.json").write_text(json.dumps(meta))
    (baseline / "identities.json.gz").write_bytes(raw)
    before = {
        "status": "open", "kickoff": "2026-09-08T15:00:00-0300", "sofa_id": None,
        "home_raw": "Alpha Town", "away_raw": "Beta United",
        "home_norm": "alpha town", "away_norm": "beta united", "league_raw": "Test League",
        "open_odd": 2.1, "open_ts": "2026-09-07T15:00:00-0300", "min_odd": 1.9,
        "max_odd": 2.2, "n_obs": 9, "n_moves": 2, "n_price_moves": 2, "n_line_moves": 0,
        "last_odd": 2.0, "last_ts": "2026-09-08T10:00:00-0300",
        "open_time_verified": False, "timestamp_provenance": "legacy_unknown",
    }
    contract = {
        "kickoff_epoch": int(datetime.fromisoformat(before["kickoff"]).timestamp()),
        "name_pairs": [list(hd.name_pair(before["home_raw"], before["away_raw"], before["league_raw"]))],
        "legacy_days": ["2026-09-08", "2026-09-09"],
        "sofa_ids": ["12345"], "league_names": [hd.league_name(before["league_raw"])],
    }
    entry = {
        "old_key": OLD, "source_path": source["path"], "source_file_sha256": source["sha256"],
        "original_record": before, "original_record_sha256": hp.digest(before),
        "floor_record": copy.deepcopy(before), "floor_record_sha256": hp.digest(before),
        "fixture_contract": contract,
    }
    evidence = {"schema": 1, "baseline_sha256": hp.digest(meta), "inventory_sha256": meta["inventory_sha256"],
                "source_commit": meta["source_commit"], "entry_count": 1, "entries": [entry]}

    def save_evidence():
        encoded = json.dumps(evidence).encode()
        (baseline / hd.EVIDENCE_FILE).write_bytes(encoded)
        monkeypatch.setattr(hd, "EVIDENCE_SHA256", hp.sha(encoded))

    save_evidence()
    successor = {**copy.deepcopy(before), "sofa_id": "12345", "merged_from_keys": [OLD]}
    records = {key: {"status": "settled" if key in ids[:6] else "open"}
               for key in ids if key != OLD}
    records.update({TARGET: successor, "new": {"status": "open"}})
    history_root = env["root"] / "data" / "odds_history"
    shard = history_root / "keys" / "2026-09.json"

    def write_records(value=None):
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_text(json.dumps(records if value is None else value))

    def proof():
        settled = [key for key, value in records.items() if value.get("status") == "settled"]
        return hp.preservation_proof(records, settled, records=records, durable_remaps=True)

    def build_target(report=None):
        target = env["target"]
        target["history_preservation"] = proof() if report is None else report
        (env["valor"] / "data" / "history.js").write_bytes(window("HIST", target))
        assert build_manifest.main() == 0

    env.update(records=records, proof=proof, source=source, evidence=evidence,
               save_evidence=save_evidence, original=before, ids=ids, meta=meta,
               history_root=history_root, shard=shard, write_records=write_records,
               build_target=build_target)
    # Every negative starts from a genuinely accepted proof, preventing an
    # unrelated fixture/schema mistake from making adversarial cases pass.
    assert hp.validate_preservation(proof())
    return env


def test_new_identity_rename_needs_no_target_whitelist_edit(dynamic_env):
    proof = dynamic_env["proof"]()
    assert proof["schema"] == 3
    assert proof["raw_count"] == len(dynamic_env["records"]) == 9
    assert proof["settled_count"] == 6
    assert proof["literal_missing_raw_count"] == proof["remapped_raw_count"] == 1
    assert proof["missing_raw_count"] == proof["missing_settled_count"] == 0
    assert proof["remapped_settled_count"] == 0
    assert hp.validate_preservation(proof)


def test_proof_and_actual_inventory_work_without_git_or_network(dynamic_env, monkeypatch):
    # A checkout with no original Git objects is enough: only committed evidence
    # and current shards are needed. Any hidden fallback must fail this test.
    def forbidden(*args, **kwargs):
        raise AssertionError("runtime source verification attempted Git/network")

    dynamic_env["write_records"]()
    monkeypatch.chdir(dynamic_env["root"])
    for name in ("run", "Popen", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    proof = dynamic_env["proof"]()
    assert inventory.validate_local_inventory(proof, dynamic_env["history_root"]) == proof


@pytest.mark.parametrize("key,sofa_id", [
    ("book|sofa:99999|Gols|2.5|over", "99999"),
    ("otherbook|sofa:12345|Gols|2.5|over", "12345"),
    ("book|sofa:12345|Cartões|2.5|over", "12345"),
    ("book|sofa:12345|Gols|3.5|over", "12345"),
    ("book|sofa:12345|Gols|2.5|under", "12345"),
    ("book|sofa:12345|Gols@away|2.5|over", "12345"),
])
def test_unverified_fixture_or_changed_market_contract_blocks(dynamic_env, key, sofa_id):
    record = dynamic_env["records"].pop(TARGET)
    record["sofa_id"] = sofa_id
    dynamic_env["records"][key] = record
    with pytest.raises(ValueError):
        hp.validate_preservation(dynamic_env["proof"]())


@pytest.mark.parametrize("changes", [
    {"sofa_id": "99999"},
    {"home_raw": "Alpha Town Women", "home_norm": "alpha town women"},
    {"home_raw": "Beta United", "away_raw": "Alpha Town",
     "home_norm": "beta united", "away_norm": "alpha town"},
    {"home_norm": "wrong club"},
    {"home_norm": "beta united", "away_norm": "alpha town"},
    {"home_norm": "alpha town women"},
    {"league_raw": "Other League"},
    {"kickoff": "2026-09-09T15:00:00-0300"},
    {"open_odd": 1.5}, {"open_odd": True}, {"open_odd": float("nan")},
    {"min_odd": 2.0}, {"max_odd": 2.1}, {"n_obs": 8},
    {"last_ts": "2026-09-08T09:00:00-0300"},
    {"merged_from_keys": []}, {"merged_from_keys": [OLD, OLD]},
])
def test_forged_successor_fields_do_not_prove_retention(dynamic_env, changes):
    dynamic_env["records"][TARGET].update(changes)
    with pytest.raises(ValueError):
        hp.validate_preservation(dynamic_env["proof"]())


def test_second_lineage_owner_is_ambiguous(dynamic_env):
    dynamic_env["records"]["unrelated"] = copy.deepcopy(dynamic_env["records"][TARGET])
    with pytest.raises(ValueError):
        hp.validate_preservation(dynamic_env["proof"]())


def test_one_successor_cannot_be_reused_for_two_baseline_identities(dynamic_env):
    other = OLD.replace("2026-09-08", "2026-09-09")
    entry = copy.deepcopy(dynamic_env["evidence"]["entries"][0])
    second = {**copy.deepcopy(entry), "old_key": other}
    records = copy.deepcopy(dynamic_env["records"])
    records[TARGET]["merged_from_keys"].append(other)
    with pytest.raises(ValueError, match="reused target"):
        hd.remap_witnesses(records, {OLD, other}, {OLD: entry, other: second})


def test_missing_successor_is_not_proved_by_source_archive(dynamic_env):
    dynamic_env["records"].pop(TARGET)
    dynamic_env["records"]["unrelated"] = {"status": "open"}
    with pytest.raises(ValueError):
        hp.validate_preservation(dynamic_env["proof"]())


def test_same_instant_offset_spelling_preserves_identity(dynamic_env):
    record = dynamic_env["records"][TARGET]
    record.update(kickoff="2026-09-08T15:00:00-03:00",
                  open_ts="2026-09-07T15:00:00-03:00", last_ts="2026-09-08T13:00:00Z")
    assert hp.validate_preservation(dynamic_env["proof"]())


def test_new_observations_can_extend_values_without_rewriting_evidence(dynamic_env):
    before = copy.deepcopy(dynamic_env["evidence"])
    dynamic_env["records"][TARGET].update(n_obs=14, n_moves=3, n_price_moves=3,
                                         min_odd=1.8, max_odd=2.4, status="settled")
    assert hp.validate_preservation(dynamic_env["proof"]())
    assert dynamic_env["evidence"] == before


def test_exact_fixture_contract_allows_next_legacy_to_sofa_rename(dynamic_env):
    record = dynamic_env["records"].pop(TARGET)
    record["sofa_id"] = None
    dynamic_env["records"][LEGACY_TARGET] = record
    first = dynamic_env["proof"]()
    assert hp.validate_preservation(first)
    record = dynamic_env["records"].pop(LEGACY_TARGET)
    record.update(sofa_id="12345", merged_from_keys=[OLD, LEGACY_TARGET])
    dynamic_env["records"][TARGET] = record
    second = dynamic_env["proof"]()
    assert hp.validate_preservation(second)
    assert first["remap_evidence_sha256"] == second["remap_evidence_sha256"]
    assert second["raw_count"] == first["raw_count"]


def test_source_index_tampering_blocks_even_when_public_proof_is_unchanged(dynamic_env):
    proof = dynamic_env["proof"]()
    path = dynamic_env["baseline"] / hd.EVIDENCE_FILE
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        hp.validate_preservation(proof)


def test_pinned_index_from_another_baseline_still_blocks(dynamic_env):
    dynamic_env["evidence"]["baseline_sha256"] = "0" * 64
    dynamic_env["save_evidence"]()
    with pytest.raises(ValueError):
        dynamic_env["proof"]()


@pytest.mark.parametrize("kind", ["changed_value", "dropped_owner", "replaced_settled", "new_record"])
def test_actual_ledger_cannot_be_hidden_by_valid_detached_witness(dynamic_env, kind):
    proof = dynamic_env["proof"]()
    records = copy.deepcopy(dynamic_env["records"])
    if kind == "changed_value":
        records[TARGET]["n_obs"] += 1  # Individually valid, but not this sealed build.
    elif kind == "dropped_owner":
        records.pop(TARGET)
        records["replacement"] = {"status": "open"}
    elif kind == "replaced_settled":
        records.pop("key0")
        records["replacement"] = {"status": "settled", "merged_from_keys": ["key0"]}
    else:
        records["extra"] = {"status": "open"}
    dynamic_env["write_records"](records)
    assert hp.validate_preservation(proof)
    with pytest.raises(ValueError):
        inventory.validate_local_inventory(proof, dynamic_env["history_root"])


def test_hot_and_archived_shards_form_one_real_inventory(dynamic_env):
    records = copy.deepcopy(dynamic_env["records"])
    archived = records.pop("key0")
    dynamic_env["write_records"]({**records, "__main_lines__": {"ignored": True}})
    path = dynamic_env["history_root"] / "_archive" / "keys" / "2026-08.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"key0": archived}))
    loaded = inventory.load_records(dynamic_env["history_root"])
    assert loaded == dynamic_env["records"]
    assert inventory.validate_actual_records(dynamic_env["proof"](), loaded)


@pytest.mark.parametrize("payload", ["[]", '{"broken":42}', '{"broken":'])
def test_missing_or_malformed_actual_inventory_is_never_skipped(dynamic_env, payload):
    proof = dynamic_env["proof"]()
    with pytest.raises(ValueError, match="inventory missing"):
        inventory.validate_local_inventory(proof, dynamic_env["history_root"])
    dynamic_env["write_records"]()
    dynamic_env["shard"].write_text(payload)
    with pytest.raises(ValueError):
        inventory.validate_local_inventory(proof, dynamic_env["history_root"])


def set_schema2_live(dynamic_env, monkeypatch):
    """Real old-schema witness/contract, not a hand-labelled schema-3 report."""
    before = dynamic_env["original"]
    audited = copy.deepcopy(dynamic_env["records"][TARGET])
    source = dynamic_env["source"]
    entry = {"old_key": OLD, "target_key": TARGET, "source_path": source["path"],
             "source_file_sha256": source["sha256"], "original_record": before,
             "original_record_sha256": hp.digest(before), "audited_record": audited,
             "audited_record_sha256": hp.digest(audited), "audited_commit": "b" * 40,
             "target_path": "data/odds_history/keys/current.json"}
    evidence = {"schema": 1, "baseline_sha256": hp.digest(dynamic_env["meta"]),
                "source_commit": dynamic_env["meta"]["source_commit"], "entry_count": 1,
                "entries": [entry], "audited_commits": ["b" * 40],
                "target_files": [{"commit": "b" * 40, "path": entry["target_path"], "sha256": "c" * 64}]}
    encoded = json.dumps(evidence).encode()
    (dynamic_env["baseline"] / hr.EVIDENCE_FILE).write_bytes(encoded)
    monkeypatch.setattr(hr, "EVIDENCE_SHA256", hp.sha(encoded))
    records = dynamic_env["records"]
    settled = [k for k, v in records.items() if v.get("status") == "settled"]
    proof = hp.preservation_proof(records, settled, records=records)
    assert proof["schema"] == 2 and hp.validate_preservation(proof)
    live_history = copy.deepcopy(dynamic_env["target"])
    live_history["history_preservation"] = proof
    live_raw = window("HIST", live_history)
    manifest = copy.deepcopy(dynamic_env["live"])
    manifest["artifacts"]["/data/history.js"].update(
        count=6, valid_count=0, sha256=hp.sha(live_raw),
        history_contract=hp.contract(live_history, hp.sha(live_raw)))
    dynamic_env["remote"].update(history=live_raw, manifest=window("MANIFEST", manifest))


def test_deploy_accepts_schema2_live_to_schema3_with_actual_inventory(dynamic_env, monkeypatch):
    set_schema2_live(dynamic_env, monkeypatch)
    dynamic_env["write_records"]()
    dynamic_env["build_target"]()
    assert deploy.manifest_gate(dynamic_env["valor"], history_root=dynamic_env["history_root"]) is None


def test_deploy_blocks_missing_bank_even_with_valid_manifest_and_witness(dynamic_env, monkeypatch):
    set_schema2_live(dynamic_env, monkeypatch)
    dynamic_env["build_target"]()
    reason = deploy.manifest_gate(dynamic_env["valor"], history_root=dynamic_env["history_root"])
    assert "inventory missing" in reason


def test_rehashed_forged_history_and_manifest_do_not_override_actual_bank(dynamic_env, monkeypatch):
    set_schema2_live(dynamic_env, monkeypatch)
    dynamic_env["write_records"]()  # Persisted truth stays untouched.
    dynamic_env["records"][TARGET]["n_obs"] += 1
    forged = dynamic_env["proof"]()
    assert hp.validate_preservation(forged)  # Valid in isolation, wrong actual record.
    dynamic_env["build_target"](forged)  # Both artifact and manifest hashes are consistent.
    reason = deploy.manifest_gate(dynamic_env["valor"], history_root=dynamic_env["history_root"])
    assert "differs from actual persisted history" in reason


def test_production_entrypoint_requires_actual_bank_before_any_api(dynamic_env, monkeypatch):
    valor = dynamic_env["valor"]
    (valor / "index.html").write_text("x" * 15001)
    for name in ("board", "valor", "history", "ops"):
        path = valor / "js" / (name + ".js")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// test fixture")
    monkeypatch.setattr(deploy, "DIR", valor)
    monkeypatch.setattr(deploy, "TOKEN", "not-a-real-token")
    checked = []

    def gate(path, *, history_root=None):
        checked.append((path, history_root))
        return "preservation actual inventory missing"

    def forbidden(*args, **kwargs):
        raise AssertionError("publication API called despite invalid actual bank")

    monkeypatch.setattr(deploy, "manifest_gate", gate)
    monkeypatch.setattr(deploy, "api", forbidden)
    assert deploy.main() == 1
    assert checked == [(valor, dynamic_env["history_root"])]
