"""Reproduce the closed rename certificate from exact Git snapshots (read-only).

The prior audit supplies candidate pairs only: original and audited full records
are independently recovered from Git and each original shard must match the
unchanged baseline's SHA256. Output is JSON on stdout; no raw data is mutated.
Changing the closed list requires code review and a new independently pinned
certificate hash, not running this script during CI.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_policy import digest, sha
from history_policy_remap import validate_record


def build(repo, audit_path, snapshot):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])

    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    meta = json.loads(git("show", "HEAD:history_policy_baseline/baseline.json"))
    inventory_raw = git("show", "HEAD:history_policy_baseline/identities.json.gz")
    if sha(inventory_raw) != meta["inventory_sha256"] or audit["source_commit"] != meta["source_commit"]:
        raise ValueError("audit does not refer to unchanged baseline")
    inventory = json.loads(gzip.decompress(inventory_raw))
    raw_ids, settled = set(inventory["raw_ids"]), set(inventory["settled_ids"])
    snapshot = git("rev-parse", snapshot + "^{commit}").decode().strip()
    pairs = {}
    for item in audit["details"]:
        if len(item["candidate_keys"]) != 1 or item["old_key"] in pairs:
            raise ValueError("non-unique audit mapping")
        pairs[item["old_key"]] = item["candidate_keys"][0]
    if len(set(pairs.values())) != len(pairs) or set(pairs) & settled:
        raise ValueError("reused target or settled rename")
    sources, originals, targets, current_ids, claims = {}, {}, {}, set(), {k: [] for k in pairs}
    for receipt in meta["source_files"]:
        # All approved source identities are September fixtures. Their exact
        # location is still discovered, and missing originals fail below.
        if not receipt["path"].startswith("data/odds_history/keys/"):
            continue
        raw = git("show", meta["source_commit"] + ":" + receipt["path"])
        if sha(raw) != receipt["sha256"]:
            raise ValueError("original source file differs from frozen baseline")
        for key, record in json.loads(raw).items():
            if key in pairs:
                if key in originals:
                    raise ValueError("duplicate original key")
                originals[key] = record
                sources[key] = receipt
    files = git("ls-tree", "-r", "--name-only", snapshot, "--", "data/odds_history/keys",
                "data/odds_history/_archive/keys").decode().splitlines()
    receipts = []
    target_set = set(pairs.values())
    for path in files:
        if not path.endswith(".json"):
            continue
        raw = git("show", snapshot + ":" + path)
        data = json.loads(raw)
        used = False
        for key, record in data.items():
            if key.startswith("__") or not isinstance(record, dict):
                continue
            current_ids.add(key)
            for old in record.get("merged_from_keys", []):
                if old in claims:
                    claims[old].append(key)
            if key in target_set:
                if key in targets:
                    raise ValueError("duplicate successor record")
                targets[key] = (record, path)
                used = True
        if used:
            receipts.append({"commit": snapshot, "path": path, "sha256": sha(raw),
                             "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()})
    entries = []
    for old, target in sorted(pairs.items()):
        if old not in raw_ids or old in current_ids or claims[old] != [target]:
            raise ValueError("audited old key absent, present literally, or ambiguous: "
                             + repr((old, old in raw_ids, old in current_ids, claims[old], target)))
        before, (after, target_path) = originals[old], targets[target]
        entry = {"old_key": old, "target_key": target,
                 "source_path": sources[old]["path"], "source_file_sha256": sources[old]["sha256"],
                 "original_record": before, "original_record_sha256": digest(before),
                 "audited_record": after, "audited_record_sha256": digest(after),
                 "audited_commit": snapshot, "target_path": target_path,
                 "target_was_baseline_identity": target in raw_ids}
        validate_record(entry, after)
        entries.append(entry)
    return {"schema": 1, "baseline_sha256": digest(meta), "source_commit": meta["source_commit"],
            "audited_commits": [snapshot],
            "candidate_audits": [{"snapshot": snapshot, "sha256": sha(Path(audit_path).read_bytes())}],
            "entry_count": len(entries), "target_files": receipts, "entries": entries}


def combine(first, second):
    if any(first[key] != second[key] for key in ("schema", "baseline_sha256", "source_commit")):
        raise ValueError("supplement uses another source baseline")
    entries = first["entries"] + second["entries"]
    if (len({e["old_key"] for e in entries}) != len(entries)
            or len({e["target_key"] for e in entries}) != len(entries)):
        raise ValueError("supplement repeats an original or target")
    return {**first, "entries": sorted(entries, key=lambda e: e["old_key"]), "entry_count": len(entries),
            "audited_commits": sorted(set(first["audited_commits"] + second["audited_commits"])),
            "candidate_audits": first["candidate_audits"] + second["candidate_audits"],
            "target_files": first["target_files"] + second["target_files"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--supplement-audit", type=Path, action="append", default=[])
    args = parser.parse_args()
    evidence = build(args.repo, args.audit, args.snapshot)
    for supplement_path in args.supplement_audit:
        supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
        evidence = combine(evidence, build(args.repo, supplement_path, supplement["target_commit"]))
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2))
