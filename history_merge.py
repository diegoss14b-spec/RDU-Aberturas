# -*- coding: utf-8 -*-
"""Primitivas idempotentes para unir registros duplicados do histórico.

O banco já teve duas identidades para o mesmo jogo (a chave legada por
data/nomes e a chave canônica ``sofa:{id}``).  Este módulo concentra a regra de
merge usada tanto pelo ingest diário quanto pela migração do backlog.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


_STATUS_RANK = {
    "open": 0,
    "unavailable": 1,       # legado: deve voltar para retry no settlement
    "closed": 2,
    "pending_result": 3,
    "settled": 4,
}


def _present(value):
    return value is not None and value != ""


def atomic_write_text(path, text):
    """Substitui um arquivo por rename atômico, sem deixar backup persistente."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _pick_by_ts(a, b, ts_field, *, latest):
    """Escolhe o record com timestamp mais cedo/mais tarde, deterministicamente."""
    ta, tb = str(a.get(ts_field) or ""), str(b.get(ts_field) or "")
    if not ta:
        return b if tb else a
    if not tb:
        return a
    if latest:
        return b if tb > ta else a
    return b if tb < ta else a


def merge_records(a, b):
    """Mescla duas observações da mesma chave canônica sem perder liquidação.

    - abertura mais antiga;
    - última observação e fechamento mais recentes;
    - ``settled`` sempre vence estados intermediários;
    - contadores, extremos, resultado e metadados são preservados.

    A função não modifica os argumentos. Aplicá-la novamente ao record já
    consolidado não acontece após a remoção da chave legada, o que torna a
    migração idempotente em nível de arquivo.
    """
    a, b = dict(a or {}), dict(b or {})
    out = dict(a)
    for field, value in b.items():
        if not _present(out.get(field)) and _present(value):
            out[field] = value

    opened = _pick_by_ts(a, b, "open_ts", latest=False)
    for field in ("open_odd", "open_ts", "open_is_first_seen"):
        if field in opened:
            out[field] = opened[field]

    last = _pick_by_ts(a, b, "last_ts", latest=True)
    for field in ("last_odd", "last_ts"):
        if field in last:
            out[field] = last[field]

    closed = _pick_by_ts(a, b, "close_ts", latest=True)
    for field in ("close_odd", "close_ts"):
        if _present(closed.get(field)):
            out[field] = closed[field]

    max_values = [v for v in (a.get("max_odd"), b.get("max_odd")) if v is not None]
    min_values = [v for v in (a.get("min_odd"), b.get("min_odd")) if v is not None]
    if max_values:
        out["max_odd"] = max(max_values)
    if min_values:
        out["min_odd"] = min(min_values)

    for field in ("n_obs", "n_moves", "n_price_moves", "n_line_moves"):
        values = [v for v in (a.get(field), b.get(field)) if isinstance(v, (int, float))]
        if values:
            out[field] = sum(values)

    status = max(
        (a.get("status") or "open", b.get("status") or "open"),
        key=lambda value: _STATUS_RANK.get(value, 0),
    )
    out["status"] = status

    # Se algum lado já foi liquidado, ele é a fonte autoritativa dos campos de
    # settlement. Em empate, o record com last_ts mais recente vence.
    settled = [r for r in (a, b) if r.get("status") == "settled"]
    if settled:
        winner = settled[0]
        if len(settled) == 2:
            winner = _pick_by_ts(settled[0], settled[1], "last_ts", latest=True)
        for field in (
            "result", "won", "clv_pct", "beat_close", "settled_at",
            "settlement_reason", "settlement_source",
        ):
            if field in winner:
                out[field] = winner[field]
        out["status"] = "settled"

    sofa = b if b.get("sofa_id") else a if a.get("sofa_id") else None
    if sofa:
        out["sofa_id"] = sofa.get("sofa_id")
        for field in ("match_method", "match_confidence", "kickoff"):
            if _present(sofa.get(field)):
                out[field] = sofa[field]

    for field in ("home_raw", "away_raw"):
        values = [str(v) for v in (a.get(field), b.get(field)) if _present(v)]
        if values:
            out[field] = max(values, key=len)

    origins = set()
    for rec in (a, b):
        origins.update(str(x) for x in (rec.get("merged_from_keys") or []) if x)
    if origins:
        out["merged_from_keys"] = sorted(origins)
    return out


def merge_latest_state(a, b):
    """Merge pequeno para ``__main_lines__``: mantém o estado mais recente."""
    a, b = dict(a or {}), dict(b or {})
    ta, tb = str(a.get("ts") or ""), str(b.get("ts") or "")
    winner, other = (b, a) if tb > ta else (a, b)
    out = dict(other)
    out.update(winner)
    return out
