# -*- coding: utf-8 -*-
"""build_manifest.py — manifesto atômico do build da Mesa (§8 do brief de auditoria).

Roda por ÚLTIMO num full, depois de board/ops/history/moves/openclose. Lê os 5 artefatos,
prova que pertencem ao MESMO build (timestamps próximos) e grava um manifesto com hash,
contagem, schema, timestamp e um build_id único. O deploy e o smoke usam esse manifesto para
garantir que a produção nunca combine artefatos de execuções diferentes nem publique um
artefato defasado (o incidente da auditoria: board fresco + history/moves/openclose atrasados).

Falha (exit≠0) se algum artefato faltar, não parsear, ou for de outro build (fora da janela).
Como o passo `build_openclose` deixou de ser tolerado com `|| echo segue`, um openclose stale
já derruba o pipeline antes daqui; esta é a segunda linha de defesa, agora explícita.

Saída: valor/data/manifest.js  →  window.MANIFEST={...};
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from history_quality import parse_iso_flex  # parser único §10
from manifest_common import (
    ARTIFACTS, MANIFEST_PREFIX, MANIFEST_REL, MANIFEST_VERSION,
    artifact_count, artifact_gerado, artifact_valid_count, sha256_bytes, strip_window,
)

BRT = timezone(timedelta(hours=-3))
VALOR = ROOT / "valor"
# tolerância de "mesmo build": os 5 artefatos têm que ter sido gerados dentro desta janela
SPREAD_MIN = float(os.environ.get("MANIFEST_SPREAD_MIN", "45"))


def _ts_of(name, data, file_path):
    """Timestamp do artefato: gerado_iso quando existe (board/ops/history/openclose),
    senão o mtime do arquivo (moves.js não carimba). Retorna datetime aware (BRT)."""
    g = artifact_gerado(name, data)
    dt = parse_iso_flex(g, default_tz=BRT) if g else None
    if dt is None:
        dt = datetime.fromtimestamp(file_path.stat().st_mtime, tz=BRT)
    return dt.astimezone(BRT)


def _max_source_ts(name, data):
    """Maior timestamp de FONTE do artefato (kickoff/início), best-effort."""
    try:
        if name == "board":
            xs = [j.get("inicio_iso") for j in (data.get("jogos") or []) if j.get("inicio_iso")]
            return max(xs) if xs else None
        if name == "history":
            xs = [r.get("kickoff") for r in (data.get("liquidadas") or []) if r.get("kickoff")]
            return max(xs) if xs else None
        if name == "openclose":
            xs = [r.get("kickoff") for r in (data.get("rows") or []) if r.get("kickoff")]
            return max(xs) if xs else None
    except Exception:
        return None
    return None


def _code_sha():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()[:40]
    except Exception:
        pass
    return os.environ.get("GITHUB_SHA", "")[:40] or None


def main():
    now = datetime.now(BRT)
    artifacts = {}
    tstamps = {}
    problems = []
    for rel, prefix, name in ARTIFACTS:
        f = VALOR / rel.lstrip("/")
        if not f.is_file():
            problems.append(f"artefato ausente: {rel}")
            continue
        raw = f.read_bytes()
        try:
            data = strip_window(raw.decode("utf-8"), prefix)
        except Exception as e:
            problems.append(f"artefato ilegível {rel}: {type(e).__name__}: {e}")
            continue
        ts = _ts_of(name, data, f)
        tstamps[name] = ts
        artifacts[rel] = {
            "name": name,
            "schema": f"{name}/{MANIFEST_VERSION}",
            "sha256": sha256_bytes(raw),
            "bytes": len(raw),
            "count": artifact_count(name, data),
            "valid_count": artifact_valid_count(name, data),
            "gerado_iso": artifact_gerado(name, data),
            "ts": ts.isoformat(timespec="seconds"),
            "max_source_ts": _max_source_ts(name, data),
        }

    if problems:
        for p in problems:
            print(f"[manifest] ❌ {p}")
        print("[manifest] manifesto NÃO gerado — build incompleto/misturado.")
        return 1

    # prova de "mesmo build": todos os artefatos dentro de SPREAD_MIN minutos
    lo, hi = min(tstamps.values()), max(tstamps.values())
    spread = (hi - lo).total_seconds() / 60.0
    if spread > SPREAD_MIN:
        outlier = min(tstamps.items(), key=lambda kv: kv[1])
        newest = max(tstamps.items(), key=lambda kv: kv[1])
        print(f"[manifest] ❌ artefatos de builds diferentes: spread {spread:.0f}min > {SPREAD_MIN:.0f}min "
              f"(mais antigo: {outlier[0]} @ {outlier[1]:%H:%M} · mais novo: {newest[0]} @ {newest[1]:%H:%M})")
        print("[manifest] provável artefato stale (não regenerado nesta rodada). Deploy será bloqueado.")
        return 1

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "build_id": uuid.uuid4().hex,
        "generated_iso": now.isoformat(timespec="seconds"),
        "gerado": now.strftime("%Y-%m-%d %H:%M"),
        "code_sha": _code_sha(),
        "spread_min": round(spread, 2),
        "artifacts": artifacts,
    }
    out = VALOR / MANIFEST_REL.lstrip("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(MANIFEST_PREFIX + json.dumps(manifest, ensure_ascii=False,
                                                separators=(",", ":")) + ";", encoding="utf-8")
    counts = " · ".join(f"{a['name']}={a['count']}" for a in artifacts.values())
    print(f"[manifest] build {manifest['build_id'][:8]} · spread {spread:.1f}min · {counts} → {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
