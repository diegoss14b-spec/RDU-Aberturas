"""Read-only migration plan: never alters keys, ledger, ticks or observed times.

Usage: python review_legacy_settlements.py --keys-dir SNAPSHOT/keys
Output is a JSON plan on stdout. Applying it requires a separately reviewed,
revision-aware migration against a backup, not this command.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from canonical import parse_history_key

def plan(paths):
    counts = Counter()
    sources, candidates = [], []
    for path in paths:
        raw = path.read_bytes()
        sources.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for key, rec in json.loads(raw).items():
            if key.startswith("__") or not isinstance(rec, dict):
                continue
            unknown = rec.get("open_time_verified") is not True or rec.get("close_time_verified") is not True
            counts["records"] += 1
            counts["unknown_observation_time"] += int(unknown)
            if parse_history_key(key).get("mercado") == "Cartões" and rec.get("status") == "settled" and rec.get("settlement_rule") in (None, "r1_fallback"):
                counts["card_semantics_review_required"] += 1
                candidates.append({"key": key, "previous_result": rec.get("result"),
                                   "previous_rule": rec.get("settlement_rule"),
                                   "action": "reconcile_incidents_then_append_revision"})
    return {"schema": 2, "dry_run": True, "mutations": 0, "sources": sources,
            "counts": dict(counts), "candidates": candidates,
            "policy": "Preserve original evidence; never manufacture observed_at or overwrite historical rows."}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(plan(sorted(args.keys_dir.glob("*.json"))), ensure_ascii=False, indent=2))
