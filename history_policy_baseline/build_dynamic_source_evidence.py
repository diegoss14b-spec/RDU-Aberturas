"""Offline, read-only reproduction of the immutable baseline/fixture index.

Reads exact Git objects and checks every original raw shard against the existing
baseline. Candidate fixture aliases are explicit scoped reviewed inputs, never
fuzzy matches. Nothing here runs automatically in ingest, build, or deployment.
"""
import argparse
import base64
import copy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_policy import digest, sha
from history_policy_dynamic import name_pair, league_name
from history_policy_remap import EVIDENCE_SHA256 as CLOSED_HASH, _time, validate_record as validate_closed_record
from canonical import league_fp, league_incompatible


def build(repo, fixture_commits, aliases_path=None):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])

    meta = json.loads(git("show", "HEAD:history_policy_baseline/baseline.json"))
    inventory_raw = git("show", "HEAD:history_policy_baseline/identities.json.gz")
    if sha(inventory_raw) != meta["inventory_sha256"]:
        raise ValueError("changed original baseline inventory")
    inventory = json.loads(gzip.decompress(inventory_raw))
    raw_ids, settled_ids = set(inventory["raw_ids"]), set(inventory["settled_ids"])
    if len(raw_ids) != meta["raw_count"] or len(settled_ids) != meta["settled_count"]:
        raise ValueError("invalid original inventory counts")
    closed_raw = git("show", "HEAD:history_policy_baseline/remap_evidence_2026-09-08.json")
    if sha(closed_raw) != CLOSED_HASH:
        raise ValueError("changed closed evidence")
    closed = {e["old_key"]: e for e in json.loads(closed_raw)["entries"]}
    sources, originals = {}, {}
    for receipt in meta["source_files"]:
        raw = git("show", meta["source_commit"] + ":" + receipt["path"])
        if sha(raw) != receipt["sha256"]:
            raise ValueError("original shard hash mismatch: " + receipt["path"])
        for key, record in json.loads(raw).items():
            if key.startswith("__") or not isinstance(record, dict):
                continue
            if len(key.split("|")) == 7 and record.get("status") == "open":
                if key in originals or key not in raw_ids or key in settled_ids:
                    raise ValueError("duplicate or ineligible baseline original")
                originals[key] = record
                sources[key] = receipt
    # Load exact fixture snapshots once, including past snapshots whose fixtures
    # have since aged out of the live rolling pointer.
    receipts, fixtures = [], []
    seen_files = set()
    for commit in dict.fromkeys([meta["source_commit"], *fixture_commits]):
        commit = git("rev-parse", commit + "^{commit}").decode().strip()
        pointer_path = "data/fixtures/sofa_latest.json"
        pointer = json.loads(git("show", commit + ":" + pointer_path))
        path = "data/fixtures/" + pointer["file"]
        raw = git("show", commit + ":" + path)
        receipt = {"commit": commit, "path": path, "sha256": sha(raw)}
        if receipt["sha256"] in seen_files:
            continue
        seen_files.add(receipt["sha256"])
        data = json.loads(raw)
        if len(data["fixtures"]) != pointer["n"]:
            raise ValueError("fixture pointer count mismatch")
        receipts.append(receipt)
        for fixture in data["fixtures"]:
            fixtures.append((receipt, fixture))
    aliases = json.loads(Path(aliases_path).read_text(encoding="utf-8")) if aliases_path else {"anchors": []}
    reviewed = {}
    for anchor in aliases["anchors"]:
        raw = git("show", anchor["fixture_commit"] + ":" + anchor["fixture_path"])
        if sha(raw) != anchor["fixture_file_sha256"]:
            raise ValueError("reviewed fixture receipt mismatch")
        hits = [f for f in json.loads(raw)["fixtures"] if str(f.get("sofa_id")) == str(anchor["sofa_id"])]
        if len(hits) != 1 or digest(hits[0]) != anchor["fixture_record_sha256"]:
            raise ValueError("reviewed fixture row mismatch")
        fixture = hits[0]
        if int(fixture["start_ts"]) != anchor["kickoff_epoch"]:
            raise ValueError("reviewed fixture kickoff mismatch")
        scope = (tuple(anchor["source_pair"]), league_name(anchor["source_league"]), anchor["kickoff_epoch"])
        reviewed.setdefault(scope, []).append((anchor, fixture))
    reviewed_legacy = {}
    for anchor in aliases.get("legacy_anchors", []):
        raw = git("show", anchor["target_commit"] + ":" + anchor["target_path"])
        if sha(raw) != anchor["target_file_sha256"]:
            raise ValueError("reviewed legacy target file mismatch")
        record = json.loads(raw)[anchor["target_key"]]
        if digest(record) != anchor["target_record_sha256"]:
            raise ValueError("reviewed legacy target record mismatch")
        original = originals[anchor["old_key"]]
        league = original.get("league_raw") or ""
        scope = (name_pair(original.get("home_raw"), original.get("away_raw"), league),
                 league_name(league), int(_time(original.get("kickoff")).timestamp()))
        claimed_scope = (tuple(anchor["source_pair"]), league_name(anchor["source_league"]), anchor["kickoff_epoch"])
        if scope != claimed_scope or len(anchor["target_key"].split("|")) != 7:
            raise ValueError("reviewed legacy fixture scope mismatch")
        target_pair = name_pair(*anchor["target_key"].split("|")[2:4], league)
        if tuple(anchor["target_pair"]) != target_pair:
            raise ValueError("reviewed legacy target names mismatch")
        if name_pair(record.get("home_raw"), record.get("away_raw"), record.get("league_raw")) != scope[0] or league_name(record.get("league_raw")) != scope[1]:
            raise ValueError("reviewed legacy raw fixture changed")
        validate_closed_record({"old_key": anchor["old_key"], "target_key": anchor["target_key"],
                                "original_record": original, "audited_record": record}, record)
        reviewed_legacy.setdefault(scope, []).append(anchor)

    entries = []
    for old, original in sorted(originals.items()):
        source = sources[old]
        known = closed.get(old)
        if known and digest(original) != known["original_record_sha256"]:
            raise ValueError("closed original differs from actual baseline")
        floor = known["audited_record"] if known else original
        league = original.get("league_raw") or ""
        pair = name_pair(original.get("home_raw"), original.get("away_raw"), league)
        epoch = int(_time(original.get("kickoff")).timestamp())
        pairs = {pair, name_pair(old.split("|")[2], old.split("|")[3], league)}
        leagues = {league_name(league)}
        days = {old.split("|")[1], datetime.fromtimestamp(epoch, timezone(timedelta(hours=-3))).date().isoformat()}
        sids, anchors = set(), []
        if known:
            pairs.add(name_pair(floor.get("home_raw"), floor.get("away_raw"), floor.get("league_raw")))
            if floor.get("home_norm") and floor.get("away_norm"):
                # Only an independently audited closed record can authorize a
                # distinct normalized pair, never mutable current metadata.
                pairs.add(name_pair(floor["home_norm"], floor["away_norm"], floor.get("league_raw")))
            leagues.add(league_name(floor.get("league_raw")))
            parts = known["target_key"].split("|")
            if len(parts) == 5 and parts[1].startswith("sofa:"):
                sids.add(parts[1][5:])
            else:
                pairs.add(name_pair(parts[2], parts[3], league))
                days.add(parts[1])
            anchors.append({"type": "closed-pair", "evidence_sha256": CLOSED_HASH,
                            "target_key": known["target_key"], "audited_commit": known["audited_commit"]})
        # Exact ordered names + exact instant is an anchor, never ratio/gscore.
        # The immutable receipt retains competition/team IDs for independent QA.
        exact = []
        for receipt, fixture in fixtures:
            if fixture.get("start_ts") is None or int(fixture["start_ts"]) != epoch:
                continue
            fp = name_pair(fixture.get("home"), fixture.get("away"), fixture.get("league"))
            if fp not in pairs:
                continue
            if league_incompatible(league_fp(league), league_fp(fixture.get("league"))):
                continue
            exact.append((receipt, fixture))
        unique_ids = {str(f.get("sofa_id")) for _, f in exact if f.get("sofa_id") is not None}
        if len(unique_ids) > 1:
            raise ValueError("ambiguous exact fixture anchor: " + old)
        for receipt, fixture in exact:
            if fixture.get("sofa_id") is None:
                continue
            sids.add(str(fixture["sofa_id"]))
            anchors.append({"type": "exact-fixture", "receipt": receipt,
                            "fixture": fixture, "fixture_sha256": digest(fixture)})
        for anchor, fixture in reviewed.get((pair, league_name(league), epoch), []):
            sids.add(str(fixture["sofa_id"]))
            pairs.add(name_pair(fixture.get("home"), fixture.get("away"), fixture.get("league")))
            anchors.append({"type": "reviewed-scoped-fixture", "scope": anchor,
                            "fixture": fixture, "fixture_sha256": digest(fixture)})
        for anchor in reviewed_legacy.get((pair, league_name(league), epoch), []):
            pairs.add(tuple(anchor["target_pair"]))
            anchors.append({"type": "reviewed-scoped-legacy", "scope": anchor})
        if len(sids) > 1:
            raise ValueError("conflicting immutable fixture IDs: " + old)
        if not all(all(p) for p in pairs):
            raise ValueError("empty original fixture name")
        entry = {"old_key": old, "source_path": source["path"], "source_file_sha256": source["sha256"],
                 "original_record": original, "original_record_sha256": digest(original),
                 "floor_record": copy.deepcopy(floor), "floor_record_sha256": digest(floor),
                 "fixture_contract": {"kickoff_epoch": epoch, "name_pairs": sorted(map(list, pairs)),
                                      "legacy_days": sorted(days), "sofa_ids": sorted(sids),
                                      "league_names": sorted(leagues)},
                 "fixture_anchors": anchors}
        entries.append(entry)
    return {"schema": 1, "baseline_sha256": digest(meta), "source_commit": meta["source_commit"],
            "inventory_sha256": meta["inventory_sha256"], "closed_evidence_sha256": CLOSED_HASH,
            "entry_count": len(entries), "fixture_source_receipts": receipts,
            "reviewed_aliases_sha256": sha(Path(aliases_path).read_bytes()) if aliases_path else None,
            "entries": entries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--fixture-commit", action="append", default=[])
    parser.add_argument("--fixture-aliases", type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--packed", action="store_true", help="Compact immutable JSON envelope with checksummed gzip payload")
    parser.add_argument("--output", type=Path, help="Write generated output only to a new path (or verify identical existing bytes)")
    args = parser.parse_args()
    result = build(args.repo, args.fixture_commit, args.fixture_aliases)
    if args.report_only:
        groups = {}
        for e in result["entries"]:
            f = e["fixture_contract"]
            key = (tuple(name_pair(e["original_record"].get("home_raw"), e["original_record"].get("away_raw"), e["original_record"].get("league_raw"))),
                   league_name(e["original_record"].get("league_raw")), f["kickoff_epoch"])
            group = groups.setdefault(key, {"pair": key[0], "league": key[1], "kickoff_epoch": key[2], "records": 0, "sofa_ids": f["sofa_ids"]})
            group["records"] += 1
        output = json.dumps({"entry_count": result["entry_count"], "groups": list(groups.values())}, ensure_ascii=False, indent=2) + "\n"
    else:
        if args.packed:
            decoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            result = {"encoding": "json+gzip+base64", "decoded_sha256": sha(decoded), "decoded_bytes": len(decoded),
                      "entry_count": result["entry_count"],
                      "data": base64.b64encode(gzip.compress(decoded, mtime=0)).decode("ascii")}
        output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            if args.output.read_text(encoding="utf-8") != output:
                raise ValueError("refusing to replace existing evidence; choose a new output path")
        else:
            args.output.write_text(output, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "sha256": sha(output.encode("utf-8"))}))
    else:
        print(output, end="")
