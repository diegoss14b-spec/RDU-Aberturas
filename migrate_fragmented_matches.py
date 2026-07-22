# -*- coding: utf-8 -*-
"""migrate_fragmented_matches.py — reúne partidas FRAGMENTADAS no banco (brief 22/07 §7).

Casos: alias explícito ("CAP PI (F)" ↔ "Clube Atlético Piauiense (F)", preservando
gênero) e virada de meia-noite/fuso (Atlante×América e Tijuana×León gravados
24/07 23:00 pela Superbet e 25/07 00:00 pela 7k).

Aplica exatamente o pipeline do ingest (migrate_keys_dict → unify_keys_dict, já
com aliases novos e guarda de kickoff do canonical) de forma AVULSA, com dry-run
e relatório origem→destino. Observações (n_obs) e movimentos (n_moves) são
SOMADOS no merge — contagem total antes = depois (nada se perde).

Uso:
  python3 migrate_fragmented_matches.py --dry
  python3 migrate_fragmented_matches.py
  python3 migrate_fragmented_matches.py --root /copia/isolada
Idempotente: 2ª execução → 0 mudanças.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from canonical import load_sofa_fixtures  # noqa: E402
from history_merge import atomic_write_text  # noqa: E402
from migrate_history_keys import migrate_keys_dict, unify_keys_dict  # noqa: E402

BRT = timezone(timedelta(hours=-3))


def _totais(keys):
    n = obs = mv = 0
    for k, v in keys.items():
        if str(k).startswith("__") or not isinstance(v, dict):
            continue
        n += 1
        obs += int(v.get("n_obs") or 0)
        mv += int(v.get("n_moves") or 0)
    return {"keys": n, "n_obs": obs, "n_moves": mv}


def migrate_root(root: Path, dry: bool):
    keys_dir = root / "data" / "odds_history" / "keys"
    rep_dir = root / "data" / "odds_history" / "_migration_reports"
    now_iso = datetime.now(BRT).strftime("%Y-%m-%dT%H:%M:%S%z")
    fixtures = load_sofa_fixtures(root)
    report = {"ts": now_iso, "dry": dry, "root": str(root),
              "fixtures": len(fixtures), "files": []}

    total_changed = 0
    for f in sorted(keys_dir.glob("*.json")):
        original = f.read_text(encoding="utf-8")
        try:
            keys0 = json.loads(original)
        except Exception as exc:
            print(f"[frag] pulei {f.name}: {type(exc).__name__}")
            continue
        antes = _totais(keys0)
        # o par migrate+unify tem um pingue-pongue benigno (migrate re-chaveia
        # pelo nome cru; unify refunde no gid canônico) que só estabiliza a
        # SERIALIZAÇÃO na 2ª passada — itera até o ponto fixo (≤3 passadas)
        k2, alias, ustats, mstats = keys0, {}, {}, {}
        prev = original
        for _ in range(3):
            k1, mstats = migrate_keys_dict(k2, fixtures)
            k2, it_alias, it_ustats = unify_keys_dict(k1)
            alias.update(it_alias)
            for kk, vv in (it_ustats or {}).items():
                ustats[kk] = ustats.get(kk, 0) + vv
            cur = json.dumps(k2, ensure_ascii=False)
            if cur == prev:
                break
            prev = cur
        depois = _totais(k2)

        # invariantes: nenhuma observação/movimento se perde no merge
        assert depois["n_obs"] == antes["n_obs"], \
            f"{f.name}: n_obs mudou ({antes['n_obs']} → {depois['n_obs']}) — abortado"
        assert depois["n_moves"] == antes["n_moves"], \
            f"{f.name}: n_moves mudou ({antes['n_moves']} → {depois['n_moves']}) — abortado"

        novo = json.dumps(k2, ensure_ascii=False)
        changed = novo != original
        entry = {
            "file": f.name, "antes": antes, "depois": depois,
            "keys_rekeyed_sofa": mstats.get("sofa"), "key_merges": mstats.get("merges"),
            "gid_merges": len(alias),
            "keys_unidas": ustats.get("key_merges"),
            "alias_origem_destino": dict(sorted(alias.items())),
            "changed": changed,
        }
        report["files"].append(entry)
        print(f"[frag] {f.name}: keys {antes['keys']}→{depois['keys']} · "
              f"gid_merges={len(alias)} · keys unidas={ustats.get('key_merges')} · "
              f"n_obs {antes['n_obs']}={depois['n_obs']} ✓"
              + (" · DRY" if dry else ("" if not changed else " · gravado")))
        for a, b in sorted(alias.items()):
            print(f"        {a}  →  {b}")
        if changed and not dry:
            atomic_write_text(f, novo)
            total_changed += 1

    report["total_files_changed"] = total_changed
    rep_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(BRT).strftime("%Y%m%d_%H%M%S")
    path = rep_dir / f"fragmented_{stamp}{'_dry' if dry else ''}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[frag] relatório → {path}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)
    migrate_root(Path(args.root), args.dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
