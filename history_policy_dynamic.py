"""Baseline-rooted preservation across future identity renames.

The source universe and fixture identities are immutable, authenticated inputs.
There is no alias graph traversal, retired-record loader, network/Git lookup or
automatic expansion of evidence at build/deploy time. Every witness compares an
actual uniquely owned successor directly with its original baseline record.
"""
import copy
import base64
import gzip
import hashlib
import json
from pathlib import Path

from canonical import n, norm_team
from history_policy_remap import _market, _number, _time


EVIDENCE_FILE = "dynamic_source_evidence_2026-09-09.json"
EVIDENCE_SHA256 = "ededd40c007d447db7fe48e6b9c0624dfdd3b627f131a0b57d48624437949148"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def league_name(value):
    return " ".join(n(str(value or "")).split())


def name_pair(home, away, league):
    return norm_team(home or "", league=league or ""), norm_team(away or "", league=league or "")


def validate_record(entry, target, record):
    """A lineage label cannot substitute for the immutable fixture/price proof."""
    old = entry["old_key"]
    before, floor = entry["original_record"], entry["floor_record"]
    fixture = entry["fixture_contract"]
    if not isinstance(record, dict) or _market(old) != _market(target):
        raise ValueError("dynamic remap market contract changed")
    origins = record.get("merged_from_keys")
    if (not isinstance(origins, list) or any(not isinstance(x, str) for x in origins)
            or len(origins) != len(set(origins)) or old not in origins):
        raise ValueError("dynamic remap direct baseline lineage missing/invalid")
    # An aware equivalent timestamp is identical evidence despite -0300 vs
    # -03:00 spelling; no tolerance window or inferred timezone is introduced.
    if _time(record.get("kickoff")).timestamp() != fixture["kickoff_epoch"]:
        raise ValueError("dynamic remap fixture kickoff changed")
    league = record.get("league_raw") or ""
    if league_name(league) not in fixture["league_names"]:
        raise ValueError("dynamic remap fixture league changed")
    pair = name_pair(record.get("home_raw"), record.get("away_raw"), league)
    allowed_pairs = {tuple(x) for x in fixture["name_pairs"]}
    if not all(pair) or pair not in allowed_pairs:
        raise ValueError("dynamic remap fixture teams changed")
    if record.get("home_norm") is not None or record.get("away_norm") is not None:
        normalized_pair = name_pair(record.get("home_norm"), record.get("away_norm"), league)
        if not all(normalized_pair) or normalized_pair not in allowed_pairs:
            raise ValueError("dynamic remap normalized fixture teams changed")
    parts = target.split("|")
    if len(parts) == 5 and parts[1].startswith("sofa:"):
        sid = parts[1][5:]
        if not sid or sid not in fixture["sofa_ids"] or str(record.get("sofa_id")) != sid:
            raise ValueError("dynamic remap unverified Sofa fixture")
    elif len(parts) == 7:
        key_pair = name_pair(parts[2], parts[3], league)
        if parts[1] not in fixture["legacy_days"] or key_pair not in allowed_pairs:
            raise ValueError("dynamic remap legacy target fixture changed")
        if record.get("sofa_id") not in (None, ""):
            raise ValueError("dynamic remap legacy target has inconsistent Sofa identity")
    else:
        raise ValueError("dynamic remap malformed target identity")
    for reference in (before, floor):
        if _number(record.get("open_odd"), "open_odd") != _number(reference.get("open_odd"), "open_odd"):
            raise ValueError("dynamic remap opening odd changed")
        if _number(record.get("min_odd"), "min_odd") > _number(reference.get("min_odd"), "min_odd"):
            raise ValueError("dynamic remap minimum observation lost")
        if _number(record.get("max_odd"), "max_odd") < _number(reference.get("max_odd"), "max_odd"):
            raise ValueError("dynamic remap maximum observation lost")
        for field in ("n_obs", "n_moves", "n_price_moves", "n_line_moves"):
            if reference.get(field) is not None and _number(record.get(field), field) < _number(reference[field], field):
                raise ValueError("dynamic remap observation counter fell: " + field)
        if reference.get("last_ts") is not None and _time(record.get("last_ts")) < _time(reference["last_ts"]):
            raise ValueError("dynamic remap last observation regressed")
    if (_time(record.get("open_ts")) != _time(floor.get("open_ts"))
            or _time(record.get("open_ts")) > _time(before.get("open_ts"))):
        raise ValueError("dynamic remap unverified opening timestamp")
    if record.get("status") not in {"open", "closed", "pending_result", "pending_semantics", "settled", "unavailable"}:
        raise ValueError("dynamic remap status missing/invalid")


def load_evidence(meta, baseline_ids, settled_ids, directory):
    raw = (Path(directory) / EVIDENCE_FILE).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != EVIDENCE_SHA256:
        raise ValueError("dynamic source evidence missing/altered")
    evidence = json.loads(raw)
    if evidence.get("encoding") == "json+gzip+base64":
        decoded = gzip.decompress(base64.b64decode(evidence["data"], validate=True))
        if (hashlib.sha256(decoded).hexdigest() != evidence.get("decoded_sha256")
                or len(decoded) != evidence.get("decoded_bytes")):
            raise ValueError("dynamic source packed payload mismatch")
        evidence = json.loads(decoded)
    if (evidence.get("schema") != 1 or evidence.get("baseline_sha256") != digest(meta)
            or evidence.get("source_commit") != meta["source_commit"]
            or evidence.get("inventory_sha256") != meta["inventory_sha256"]):
        raise ValueError("dynamic source evidence wrong baseline/source")
    sources = {x["path"]: x["sha256"] for x in meta.get("source_files", [])}
    entries = {}
    for entry in evidence.get("entries", []):
        old = entry["old_key"]
        original, floor = entry["original_record"], entry["floor_record"]
        fixture = entry["fixture_contract"]
        if (old in entries or old not in baseline_ids or old in settled_ids
                or len(old.split("|")) != 7 or original.get("status") != "open"):
            raise ValueError("dynamic source duplicate/ineligible original")
        if (sources.get(entry["source_path"]) != entry["source_file_sha256"]
                or digest(original) != entry["original_record_sha256"]
                or digest(floor) != entry["floor_record_sha256"]):
            raise ValueError("dynamic source record/hash mismatch")
        if (not isinstance(fixture.get("name_pairs"), list) or not fixture["name_pairs"]
                or any(not isinstance(p, list) or len(p) != 2 or not all(isinstance(s, str) and s for s in p)
                       for p in fixture["name_pairs"])
                or not isinstance(fixture.get("sofa_ids"), list)
                or any(not isinstance(s, str) or not s.isdigit() for s in fixture["sofa_ids"])
                or not fixture.get("legacy_days") or not fixture.get("league_names")
                or _time(original.get("kickoff")).timestamp() != fixture.get("kickoff_epoch")
                or list(name_pair(original.get("home_raw"), original.get("away_raw"), original.get("league_raw"))) not in fixture["name_pairs"]
                or league_name(original.get("league_raw")) not in fixture["league_names"]):
            raise ValueError("dynamic source fixture contract invalid")
        entries[old] = entry
    if not entries or evidence.get("entry_count") != len(entries):
        raise ValueError("dynamic source evidence count mismatch")
    return sha, entries


def remap_witnesses(records, missing, entries):
    candidates = {old: [] for old in set(missing) & entries.keys()}
    for key, record in records.items():
        if not isinstance(record, dict):
            raise ValueError("invalid raw history record")
        origins = record.get("merged_from_keys") or []
        if not isinstance(origins, list) or any(not isinstance(x, str) for x in origins):
            raise ValueError("invalid raw lineage list")
        for old in origins:
            if old in candidates:
                candidates[old].append(key)
    witnesses, used_targets = [], set()
    for old in sorted(candidates):
        owners = candidates[old]
        if len(owners) != 1:
            raise ValueError("dynamic remap ambiguous/missing baseline owner: " + old)
        target = owners[0]
        if target in used_targets:
            raise ValueError("dynamic remap reused target: " + target)
        record = copy.deepcopy(records[target])
        validate_record(entries[old], target, record)
        witnesses.append({"old_key": old, "target_key": target, "record": record,
                          "record_sha256": digest(record)})
        used_targets.add(target)
    return witnesses


def validate_witnesses(witnesses, entries):
    seen, targets = set(), set()
    for witness in witnesses:
        old, target = witness.get("old_key"), witness.get("target_key")
        if old not in entries or old in seen or target in targets:
            raise ValueError("dynamic remap witness unknown/reused identity or target")
        if witness.get("record_sha256") != digest(witness.get("record")):
            raise ValueError("dynamic remap witness record hash mismatch")
        validate_record(entries[old], target, witness["record"])
        seen.add(old)
        targets.add(target)
    return seen
