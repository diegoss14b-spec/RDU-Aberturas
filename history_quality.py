# -*- coding: utf-8 -*-
"""history_quality.py — qualidade de captura pré-jogo + helpers de tempo (P1).

capture_quality:
  full_prematch — open visto ≥3h antes do kickoff (amostra útil p/ CLV)
  late_open     — open antes do apito, mas <3h (tarde)
  no_close      — fechou sem odd válida pré-kickoff
  post_kickoff  — 1ª vista só depois do apito (CLV inválido)
  open          — ainda aberta (sem classificar close)
  unknown       — sem kickoff/open parseável
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))
CLOSE_EPS = timedelta(seconds=45)       # close deve ser antes de kickoff − ε
LATE_OPEN = timedelta(hours=3)          # open < 3h do KO = late
PREMATCH_MIN = timedelta(minutes=2)     # só fecha key se agora ≥ KO − 2min


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        pass
    try:
        s = str(s)
        if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
            s2 = s[:-2] + ":" + s[-2:]
            return datetime.fromisoformat(s2)
    except Exception:
        pass
    return None


def ensure_aware(dt, default_tz=BRT):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=default_tz)
    return dt


def compute_capture_quality(k: dict, now=None) -> str:
    """Classifica qualidade da key (open/closed/settled)."""
    now = ensure_aware(now or datetime.now(BRT))
    ko = ensure_aware(parse_ts(k.get("kickoff")))
    ots = ensure_aware(parse_ts(k.get("open_ts")))
    cts = ensure_aware(parse_ts(k.get("close_ts")))
    status = k.get("status") or "open"

    if not ko or not ots:
        return "unknown"

    # 1ª vista só depois do apito
    if ots >= ko - CLOSE_EPS:
        return "post_kickoff"

    late = (ko - ots) < LATE_OPEN
    base = "late_open" if late else "full_prematch"

    if status == "open":
        return base

    # closed / settled: precisa close válido pré-kickoff
    if not k.get("close_odd") or not cts:
        return "no_close"
    if cts >= ko - CLOSE_EPS:
        return "no_close"
    return base


def is_pre_kickoff(ts, kickoff, eps=CLOSE_EPS) -> bool:
    """True se ts está estritamente antes do kickoff (com ε)."""
    ts = ensure_aware(parse_ts(ts) if not isinstance(ts, datetime) else ts)
    ko = ensure_aware(parse_ts(kickoff) if not isinstance(kickoff, datetime) else kickoff)
    if not ts or not ko:
        return True  # sem KO: não bloqueia update
    return ts < (ko - eps)


def should_close_key(k: dict, now=None) -> bool:
    """True se a key open deve ser congelada agora."""
    now = ensure_aware(now or datetime.now(BRT))
    ko = ensure_aware(parse_ts(k.get("kickoff")))
    if not ko:
        return False
    return now >= (ko - PREMATCH_MIN)


def pick_main_line(linhas_ou):
    """linhas_ou = [{linha, over, under}, ...] → linha mais equilibrada (main)."""
    best, sc = None, 1e9
    for L in linhas_ou or []:
        o, u = L.get("over"), L.get("under")
        ln = L.get("linha")
        if o is None or u is None or ln is None:
            continue
        gap = abs(float(o) - float(u))
        near = abs((float(o) + float(u)) / 2 - 1.9)
        s = gap * 10 + near
        if s < sc:
            sc, best = s, float(ln)
    return best
