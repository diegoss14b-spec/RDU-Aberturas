"""Bind the deploy proof to the actual hot + archived history, without Git/network.

The immutable source index authenticates originals; this independent read ensures
that a well-formed public witness still describes the records being persisted.
Never read baseline/source evidence as if it were extra live observations.
"""
import json
from pathlib import Path

from history_merge import merge_records
from history_policy import preservation_proof, validate_preservation


def load_records(history_root):
    root = Path(history_root)
    paths = sorted((root / "keys").glob("*.json")) + sorted((root / "_archive" / "keys").glob("*.json"))
    if not paths:
        raise ValueError("preservation actual inventory missing")
    records = {}
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("preservation actual shard is not an object: " + path.name)
        for key, record in document.items():
            if key.startswith("__"):
                continue
            if not isinstance(record, dict):
                raise ValueError("preservation actual record invalid: " + key)
            records[key] = merge_records(records[key], record) if key in records else record
    return records


def validate_actual_records(proof, records, directory=None):
    validate_preservation(proof, directory)
    actual = preservation_proof(
        records, (key for key, record in records.items() if record.get("status") == "settled"),
        directory, records=records, durable_remaps=proof["schema"] == 3)
    validate_preservation(actual, directory)
    # Includes the real identity digests/counts and the full, detached successor
    # witnesses. A stale/fabricated witness cannot be saved by rehashing the JS.
    if actual != proof:
        raise ValueError("preservation proof differs from actual persisted history")
    return actual


def validate_local_inventory(proof, history_root, directory=None):
    return validate_actual_records(proof, load_records(history_root), directory)
