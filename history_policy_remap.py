"""Closed exception for audited identity renames, never general alias coverage.

The pinned certificate contains exact records from the original frozen Git
source and an audited successor snapshot, with the source-file SHA256 receipts.
Only these old-key/exact-target pairs are eligible. A later rename must receive
new independent evidence; indirect ancestry is deliberately NOT followed.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
from history_quality import parse_ts


EVIDENCE_FILE = "remap_evidence_2026-09-08.json"
EVIDENCE_SHA256 = "ca4e85000c87bad6bb1e6d8c84e0b95668c508025d1bd869d27ec14a7c9a4565"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _market(key):
    parts = key.split("|")
    if len(parts) not in (5, 7) or not all(parts):
        raise ValueError("invalid remap market identity")
    return parts[0], *parts[-3:]


def _number(value, field):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid remap value: " + field)
    return value


def _time(value):
    try:
        result = parse_ts(value)
        if result is None or result.utcoffset() is None:
            raise ValueError("naive timestamp")
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid remap timestamp") from exc


def validate_record(entry, record):
    """Recheck actual record values, not merely a claimed merged_from_keys alias."""
    old, target = entry["old_key"], entry["target_key"]
    before, audited = entry["original_record"], entry["audited_record"]
    if not isinstance(record, dict) or _market(old) != _market(target):
        raise ValueError("remap market contract changed")
    origins = record.get("merged_from_keys")
    if (not isinstance(origins, list) or any(not isinstance(x, str) for x in origins)
            or len(origins) != len(set(origins)) or old not in origins):
        raise ValueError("remap direct lineage missing/invalid")
    if (not before.get("kickoff") or record.get("kickoff") != before["kickoff"]
            or record.get("sofa_id") != audited.get("sofa_id")):
        raise ValueError("remap fixture/kickoff changed")
    # None, bool and non-finite numbers must not masquerade as retained prices.
    for reference in (before, audited):
        if _number(record.get("open_odd"), "open_odd") != _number(reference.get("open_odd"), "open_odd"):
            raise ValueError("remap opening odd changed")
        if _number(record.get("min_odd"), "min_odd") > _number(reference.get("min_odd"), "min_odd"):
            raise ValueError("remap minimum observation lost")
        if _number(record.get("max_odd"), "max_odd") < _number(reference.get("max_odd"), "max_odd"):
            raise ValueError("remap maximum observation lost")
        for field in ("n_obs", "n_moves", "n_price_moves", "n_line_moves"):
            if reference.get(field) is not None:
                if _number(record.get(field), field) < _number(reference[field], field):
                    raise ValueError("remap observation counter fell: " + field)
        if reference.get("last_ts") is not None and _time(record.get("last_ts")) < _time(reference["last_ts"]):
            raise ValueError("remap last observation regressed")
    # Eight audited records acquired an earlier genuine first observation. Do
    # not invent a time or permit any further unverified opening replacement.
    if (record.get("open_ts") != audited.get("open_ts")
            or _time(record.get("open_ts")) > _time(before.get("open_ts"))):
        raise ValueError("remap opening timestamp changed")
    if record.get("status") not in {"open", "closed", "pending_result", "pending_semantics", "settled", "unavailable"}:
        raise ValueError("remap status missing/invalid")


def load_evidence(meta, baseline_ids, baseline_settled, directory):
    raw = (Path(directory) / EVIDENCE_FILE).read_bytes()
    evidence_hash = hashlib.sha256(raw).hexdigest()
    if evidence_hash != EVIDENCE_SHA256:
        raise ValueError("closed remap evidence missing/altered")
    evidence = json.loads(raw)
    if (evidence.get("schema") != 1 or evidence.get("baseline_sha256") != _digest(meta)
            or evidence.get("source_commit") != meta["source_commit"]):
        raise ValueError("closed remap evidence wrong baseline/source")
    source_files = {item["path"]: item["sha256"] for item in meta.get("source_files", [])}
    target_files = {(item["commit"], item["path"]): item for item in evidence.get("target_files", [])}
    entries, targets = {}, set()
    for entry in evidence.get("entries", []):
        old, target = entry["old_key"], entry["target_key"]
        if (old not in baseline_ids or old in baseline_settled or old in entries
                or target in targets or target == old):
            raise ValueError("closed remap duplicate/reused target or ineligible original")
        if (source_files.get(entry["source_path"]) != entry["source_file_sha256"]
                or _digest(entry["original_record"]) != entry["original_record_sha256"]
                or _digest(entry["audited_record"]) != entry["audited_record_sha256"]
                or entry["original_record"].get("status") != "open"):
            raise ValueError("closed remap source record/hash mismatch")
        if (entry.get("audited_commit") not in evidence.get("audited_commits", [])
                or (entry.get("audited_commit"), entry.get("target_path")) not in target_files):
            raise ValueError("closed remap successor snapshot receipt missing")
        validate_record(entry, entry["audited_record"])
        entries[old] = entry
        targets.add(target)
    if not entries or evidence.get("entry_count") != len(entries):
        raise ValueError("closed remap evidence count mismatch")
    return evidence_hash, entries


def remap_witnesses(records, missing, entries):
    """Every missing old identity has exactly one owner, at its pinned target."""
    candidates = {old: [] for old in missing & entries.keys()}
    for key, record in records.items():
        if not isinstance(record, dict):
            raise ValueError("invalid raw history record")
        origins = record.get("merged_from_keys") or []
        if not isinstance(origins, list):
            raise ValueError("invalid raw lineage list")
        for old in origins:
            if not isinstance(old, str):
                raise ValueError("invalid raw lineage identity")
            if old in candidates:
                candidates[old].append(key)
    witnesses = []
    for old in sorted(candidates):
        entry = entries[old]
        target = entry["target_key"]
        if candidates[old] != [target] or target not in records:
            raise ValueError("remap ambiguous/missing or unapproved successor: " + old)
        # build_history performs further in-memory deduplication after this
        # proof: its mutations must never change the already sealed witness.
        record = copy.deepcopy(records[target])
        validate_record(entry, record)
        witnesses.append({"old_key": old, "target_key": target,
                          "record": record, "record_sha256": _digest(record)})
    return witnesses


def validate_witnesses(witnesses, entries):
    seen, targets = set(), set()
    for witness in witnesses:
        old, target = witness.get("old_key"), witness.get("target_key")
        if (old not in entries or old in seen or target in targets
                or target != entries[old]["target_key"]):
            raise ValueError("remap witness unknown/reused identity or target")
        if witness.get("record_sha256") != _digest(witness.get("record")):
            raise ValueError("remap witness record hash mismatch")
        validate_record(entries[old], witness["record"])
        seen.add(old)
        targets.add(target)
    return seen
