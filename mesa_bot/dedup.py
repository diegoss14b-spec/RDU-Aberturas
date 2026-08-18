# -*- coding: utf-8 -*-
"""Anti-spam: não reenviar o mesmo sinal se odd/EV quase iguais."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ODD_REL = 0.02   # reenviar se |Δodd|/odd ≥ 2%
DEFAULT_EV_ABS = 1.0     # ou se EV subiu ≥ 1 p.p.


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"sent": {}, "infra": {}}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"sent": {}, "infra": {}}
    if not isinstance(d, dict):
        return {"sent": {}, "infra": {}}
    d.setdefault("sent", {})
    d.setdefault("infra", {})
    return d


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def should_send(
    sig: Dict[str, Any],
    state: Dict[str, Any],
    *,
    odd_rel: float = DEFAULT_ODD_REL,
    ev_abs: float = DEFAULT_EV_ABS,
) -> Tuple[bool, str]:
    """True se deve enviar. Motivo quando False."""
    key = sig.get("key") or ""
    prev = (state.get("sent") or {}).get(key)
    if not prev:
        return True, "novo"
    odd = _f(sig.get("odd"))
    ev = _f(sig.get("ev_pct"))
    podd = _f(prev.get("odd"))
    pev = _f(prev.get("ev_pct"))
    if odd is None or podd is None or podd <= 0:
        return True, "sem_odd_prev"
    rel = abs(odd - podd) / podd
    if rel >= odd_rel:
        return True, "odd_mudou"
    if ev is not None and pev is not None and (ev - pev) >= ev_abs:
        return True, "ev_subiu"
    return False, "dup"


def mark_sent(state: Dict[str, Any], sig: Dict[str, Any]) -> None:
    key = sig.get("key") or ""
    if not key:
        return
    state.setdefault("sent", {})[key] = {
        "odd": sig.get("odd"),
        "ev_pct": sig.get("ev_pct"),
        "ts": int(time.time()),
        "jogo": sig.get("jogo"),
        "mercado": sig.get("mercado"),
    }


def filter_new(
    signals: List[Dict[str, Any]],
    state: Dict[str, Any],
    *,
    odd_rel: float = DEFAULT_ODD_REL,
    ev_abs: float = DEFAULT_EV_ABS,
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], str]]]:
    """Devolve (a_enviar, [(sig, motivo_skip)])."""
    send, skip = [], []
    for sig in signals:
        ok, reason = should_send(sig, state, odd_rel=odd_rel, ev_abs=ev_abs)
        if ok:
            send.append(sig)
        else:
            skip.append((sig, reason))
    return send, skip


def infra_cooldown_ok(
    state: Dict[str, Any],
    kind: str = "board_stale",
    *,
    cooldown_sec: int = 3600,
) -> bool:
    """True se pode mandar alerta de infra (1× por cooldown)."""
    last = ((state.get("infra") or {}).get(kind) or {}).get("ts") or 0
    return (int(time.time()) - int(last)) >= cooldown_sec


def mark_infra(state: Dict[str, Any], kind: str = "board_stale") -> None:
    state.setdefault("infra", {})[kind] = {"ts": int(time.time())}


def prune_sent(state: Dict[str, Any], *, max_age_sec: int = 7 * 86400) -> int:
    """Remove entradas velhas do estado. Retorna quantas apagou."""
    now = int(time.time())
    sent = state.get("sent") or {}
    drop = [k for k, v in sent.items()
            if now - int((v or {}).get("ts") or 0) > max_age_sec]
    for k in drop:
        sent.pop(k, None)
    return len(drop)
