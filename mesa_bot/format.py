# -*- coding: utf-8 -*-
"""Formatação HTML das mensagens Telegram (sinais Mesa)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

BRT = timezone(timedelta(hours=-3))

# Prévia no site RDU (Fase 2 enriquecerá; deep-link já útil)
DEFAULT_PREVIA_BASE = "https://rdustats.netlify.app/Previa%20do%20Jogo.html"


def _esc(s: Any) -> str:
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _fmt_linha(line: Any) -> str:
    try:
        return "%g" % float(line)
    except (TypeError, ValueError):
        return str(line or "")


def _quando(inicio_iso: Optional[str], inicio: Optional[str] = None,
            now: Optional[datetime] = None) -> str:
    now = now or datetime.now(BRT)
    dt = None
    if inicio_iso:
        try:
            dt = datetime.fromisoformat(str(inicio_iso).strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            dt = dt.astimezone(BRT)
        except ValueError:
            dt = None
    if dt is None:
        return str(inicio or "?")
    hoje = now.astimezone(BRT).date()
    if dt.date() == hoje:
        dia = "hoje"
    elif dt.date() == hoje + timedelta(days=1):
        dia = "amanhã"
    else:
        dia = dt.strftime("%d/%m")
    return "%s %s" % (dia, dt.strftime("%H:%M"))


def previa_url(
    sig: Dict[str, Any],
    *,
    base: str = DEFAULT_PREVIA_BASE,
) -> Optional[str]:
    """Deep-link Prévia (?lg=&hm=&aw=). Liga/times como na Mesa — fuzzy no front."""
    liga = (sig.get("liga") or "").strip()
    home = (sig.get("home") or "").strip()
    away = (sig.get("away") or "").strip()
    if not (liga and home and away):
        return None
    q = urlencode({"lg": liga, "hm": home, "aw": away}, quote_via=quote)
    return "%s?%s" % (base, q)


def format_signal(
    sig: Dict[str, Any],
    *,
    previa_base: str = DEFAULT_PREVIA_BASE,
    now: Optional[datetime] = None,
) -> str:
    """
    MESA · Finalizações Mais 27.5
    Náutico × Ceará (Série B) · amanhã 00:30
    Superbet 2.07 · μ 28.0 · P 52% · EV +6.7%
    """
    mercado = _esc(sig.get("mercado") or "?")
    lado = _esc(sig.get("lado") or "?")
    linha = _fmt_linha(sig.get("linha"))
    jogo = _esc(sig.get("jogo") or "?")
    liga = (sig.get("liga") or "").strip()
    liga_txt = " (%s)" % _esc(liga) if liga else ""
    quando = _quando(sig.get("inicio_iso"), sig.get("inicio"), now=now)
    casa = _esc(sig.get("casa") or "?")
    try:
        odd = float(sig.get("odd") or 0)
        odd_s = "%.2f" % odd
    except (TypeError, ValueError):
        odd_s = str(sig.get("odd") or "?")
    mu = sig.get("mu_cal") if sig.get("mu_cal") is not None else sig.get("mu")
    try:
        mu_s = "%.1f" % float(mu)
    except (TypeError, ValueError):
        mu_s = "?"
    try:
        p = float(sig.get("nossa_prob") or 0)
        p_s = "%.0f%%" % p
    except (TypeError, ValueError):
        p_s = "?"
    try:
        ev = float(sig.get("ev_pct") or 0)
        ev_s = "%+.1f%%" % ev
    except (TypeError, ValueError):
        ev_s = "?"

    lines = [
        "📊 <b>VALOR · %s %s %s</b>" % (mercado, lado, linha),
        "⚽ %s%s · %s" % (jogo, liga_txt, quando),
        "🏠 %s <b>%s</b> · μ %s · P %s · EV <b>%s</b>"
        % (casa, odd_s, mu_s, p_s, ev_s),
    ]
    url = previa_url(sig, base=previa_base)
    if url:
        lines.append('🔗 <a href="%s">Prévia do jogo</a>' % _esc(url))
    return "\n".join(lines)


def format_board_stale(age_min: float, gerado: str) -> str:
    return (
        "⚠️ <b>MESA PARADA</b>\n"
        "board.js com <b>%.0f min</b> (gerado: %s).\n"
        "O juiz ainda avalia as odds do board; recovery fica com o watchdog."
        % (age_min, _esc(gerado or "?"))
    )


def format_summary(n_new: int, n_skip: int, n_total: int, gerado: str) -> str:
    return (
        "ℹ️ <b>MESA bot</b> — ciclo ok\n"
        "sinais (juiz): %d · novos: %d · dedup: %d\n"
        "gerado: %s"
        % (n_total, n_new, n_skip, _esc(gerado or "?"))
    )
