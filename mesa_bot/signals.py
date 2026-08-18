# -*- coding: utf-8 -*-
"""Extrai sinais de valor de BOARD['jogos'][].valor[]."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

BRT = timezone(timedelta(hours=-3))


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    t = str(s).strip()
    try:
        # 2026-08-17T23:06:23-03:00
        return datetime.fromisoformat(t)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(t[:19], fmt).replace(tzinfo=BRT)
        except ValueError:
            continue
    return None


def board_age_minutes(board: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    """Minutos desde gerado_iso (None se não parseável)."""
    now = now or datetime.now(BRT)
    dt = _parse_iso(board.get("gerado_iso") or board.get("gerado"))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BRT)
    return (now - dt.astimezone(BRT)).total_seconds() / 60.0


def signal_key(sig: Dict[str, Any]) -> str:
    """Chave estável anti-spam: sofa|mercado|linha|lado|casa."""
    sofa = sig.get("sofa_id")
    sofa_s = str(int(sofa)) if sofa is not None else "nosofa"
    linha = sig.get("linha")
    try:
        linha_s = ("%g" % float(linha)) if linha is not None else "?"
    except (TypeError, ValueError):
        linha_s = str(linha)
    return "|".join([
        sofa_s,
        str(sig.get("mercado") or ""),
        linha_s,
        str(sig.get("lado") or ""),
        str(sig.get("casa") or ""),
    ])


def flatten_signals(board: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lista plana de flags actionable com metadados do jogo."""
    out: List[Dict[str, Any]] = []
    for j in board.get("jogos") or []:
        if not isinstance(j, dict):
            continue
        valor = j.get("valor") or []
        if not valor:
            continue
        sofa_id = j.get("sofa_id")
        base = {
            "jogo": j.get("jogo") or "%s × %s" % (j.get("home") or "?", j.get("away") or "?"),
            "home": j.get("home"),
            "away": j.get("away"),
            "liga": j.get("liga") or "",
            "sofa_id": sofa_id,
            "inicio": j.get("inicio"),
            "inicio_iso": j.get("inicio_iso"),
            "game_state": j.get("game_state"),
        }
        for v in valor:
            if not isinstance(v, dict):
                continue
            if v.get("actionable") is False:
                continue
            sig = dict(base)
            sig.update({
                "mercado": v.get("mercado"),
                "linha": v.get("linha"),
                "lado": v.get("lado"),
                "casa": v.get("casa"),
                "odd": v.get("odd"),
                "ev_pct": v.get("ev_pct"),
                "edge_pp": v.get("edge_pp"),
                "nossa_prob": v.get("nossa_prob"),
                "mu": v.get("mu"),
                "mu_cal": v.get("mu_cal"),
                "fair_odd": v.get("fair_odd"),
                "p_push": v.get("p_push"),
                "model_status": v.get("model_status"),
                "model_source": v.get("model_source"),
            })
            # sem sofa_id a Mesa não deveria flaggear — mas defensivo
            if sofa_id is None:
                continue
            sig["key"] = signal_key(sig)
            out.append(sig)
    # maior EV primeiro
    out.sort(key=lambda s: float(s.get("ev_pct") or 0), reverse=True)
    return out
