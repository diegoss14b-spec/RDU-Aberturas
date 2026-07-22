# -*- coding: utf-8 -*-
"""manifest_common.py — utilitários compartilhados do manifesto atômico da Mesa (§8).

Um build da Mesa é o conjunto {board, ops, history, moves, openclose}. O manifesto amarra
esses 5 artefatos a UM build (hash, contagem, schema, timestamp, build_id) para que o deploy
nunca combine artefatos de execuções diferentes e o smoke consiga comparar o que está SERVIDO
com o que foi gerado. Este módulo só usa a biblioteca padrão + o parser único de datas (§10),
para poder ser importado tanto pelos builders quanto pelo smoke em scripts/.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# (caminho relativo à pasta valor/, prefixo do window.X=, nome curto do artefato)
ARTIFACTS = [
    ("/data/board.js", "window.BOARD=", "board"),
    ("/data/ops.js", "window.OPS=", "ops"),
    ("/data/history.js", "window.HIST=", "history"),
    ("/data/moves.js", "window.MOVES=", "moves"),
    ("/data/openclose.js", "window.OPENCLOSE=", "openclose"),
]
ARTIFACT_BY_NAME = {name: (rel, prefix) for rel, prefix, name in ARTIFACTS}
MANIFEST_REL = "/data/manifest.js"
MANIFEST_PREFIX = "window.MANIFEST="
MANIFEST_VERSION = 1


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def strip_window(txt: str, prefix: str | None = None):
    """``window.X={...};`` → objeto. Sem prefixo, corta após o primeiro ``=``."""
    t = (txt or "").strip()
    if prefix and t.startswith(prefix):
        body = t[len(prefix):]
    elif "=" in t:
        body = t.split("=", 1)[1]
    else:
        body = t
    body = body.strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body.strip())


def parse_manifest_text(txt: str) -> dict:
    return strip_window(txt, MANIFEST_PREFIX)


def artifact_count(name: str, data) -> int | None:
    """Contagem canônica por artefato (o que 'encolher' significa em cada um)."""
    if not isinstance(data, dict) and name != "moves":
        return None
    if name == "board":
        return len(data.get("jogos") or [])
    if name == "history":
        banco = data.get("banco") or {}
        return int(banco.get("liquidadas") or 0)
    if name == "openclose":
        return len(data.get("rows") or [])
    if name == "moves":
        return len(data) if isinstance(data, dict) else None
    if name == "ops":
        return len(data.get("casas") or [])
    return None


def artifact_valid_count(name: str, data) -> int | None:
    """Contagem de itens VÁLIDOS que a trava anti-encolhimento protege.
    history → sinais CLV válidos; openclose → linhas; board → jogos."""
    if not isinstance(data, dict):
        return None
    if name == "history":
        banco = data.get("banco") or {}
        # sinais/clv válidos é a métrica que não pode encolher em silêncio
        return int(banco.get("clv_validas") or (data.get("head") or {}).get("n_valid") or 0)
    return artifact_count(name, data)


def artifact_gerado(name: str, data):
    if name == "moves" or not isinstance(data, dict):
        return None  # moves.js é dict puro; o timestamp vem do mtime do arquivo
    return data.get("gerado_iso") or data.get("gerado")


def hash_artifact_files(dirpath) -> dict:
    """{rel: sha256|None} pros 5 artefatos (None = arquivo ausente). Usado em teste."""
    base = Path(dirpath)
    out = {}
    for rel, _prefix, _name in ARTIFACTS:
        f = base / rel.lstrip("/")
        out[rel] = sha256_bytes(f.read_bytes()) if f.is_file() else None
    return out
