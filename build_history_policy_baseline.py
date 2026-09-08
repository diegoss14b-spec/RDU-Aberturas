"""Manual one-time inventory from an EXACT Git snapshot; does not approve deploys.

Requires the source manifest and history to agree by hash. The deploy additionally
requires this exact manifest to still be live. Never regenerates accumulated data.
"""
import argparse
import gzip
import json
import subprocess
from pathlib import Path
from history_policy import POLICY, counts, sha
from manifest_common import parse_manifest_text, strip_window


def build(repo, commit, outdir):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])
    commit = git("rev-parse", commit + "^{commit}").decode().strip()
    manifest_raw = git("show", commit + ":valor/data/manifest.js")
    history_raw = git("show", commit + ":valor/data/history.js")
    manifest = parse_manifest_text(manifest_raw.decode())
    history = strip_window(history_raw.decode(), "window.HIST=")
    if history.get("clv_policy") is not None:
        raise ValueError("baseline is not legacy")
    if manifest["artifacts"]["/data/history.js"]["sha256"] != sha(history_raw):
        raise ValueError("baseline manifest/history mismatch")
    files = git("ls-tree", "-r", "--name-only", commit, "--",
                "data/odds_history/keys", "data/odds_history/_archive/keys").decode().splitlines()
    ids, settled, sources = set(), set(), []
    for path in files:
        if not path.endswith(".json"):
            continue
        raw = git("show", commit + ":" + path)
        data = json.loads(raw)
        for key, value in data.items():
            if key.startswith("__") or not isinstance(value, dict):
                continue
            ids.add(key)
            if value.get("status") == "settled":
                settled.add(key)
        sources.append({"path": path, "sha256": sha(raw)})
    if not ids or not settled:
        raise ValueError("baseline raw inventory is empty")
    inventory = gzip.compress(json.dumps({"raw_ids": sorted(ids), "settled_ids": sorted(settled)},
                                         ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)
    meta = {"schema": 1, "target_policy": POLICY, "source_commit": commit,
            "source_build_id": manifest["build_id"], "source_manifest_sha256": sha(manifest_raw),
            "source_history_sha256": sha(history_raw), "inventory_sha256": sha(inventory),
            "raw_count": len(ids), "settled_count": len(settled), "public_counts": counts(history),
            "source_files": sources}
    outdir = Path(outdir)
    if outdir.exists() and any(outdir.iterdir()):
        raise ValueError("output already contains a baseline; choose an empty directory")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "identities.json.gz").write_bytes(inventory)
    (outdir / "baseline.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"commit": commit, "raw_count": len(ids), "settled_count": len(settled),
                      "inventory_bytes": len(inventory), "inventory_sha256": sha(inventory)}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()
    build(Path(__file__).resolve().parent, args.source_commit, args.out_dir)
