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
import math
import re
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))

# offset colado sem ':' no fim de um horário: '+0000' / '-0300'
_OFFSET_NO_COLON = re.compile(r"(?<=\d)([+-])(\d{2})(\d{2})$")
# separador de data e hora por espaço -> 'T'
_SPACE_SEP = re.compile(r"^(\d{4}-\d{2}-\d{2})[ ](\d{2}:)")
# fração de segundo além de 6 dígitos (3.9 rejeita)
_FRAC_TRIM = re.compile(r"(\.\d{6})\d+")


def parse_iso_flex(s, default_tz=None):
    """Parser ISO-8601 tolerante a fuso — IDÊNTICO no Python 3.9 e no 3.12.

    §10 do brief de auditoria (22/07): o ``datetime.fromisoformat`` do 3.9 rejeita
    ``Z`` e offsets sem ``:`` (``-0300``), então a mesma pendência virava ``age=unknown``
    no Mac (3.9) e idade válida no CI (3.12), escondendo o backlog. Aqui normalizamos
    ANTES do fromisoformat, então a classificação não depende da versão do Python.

    Aceita: ``Z``, ``-03:00``, ``-0300``, separador espaço ou ``T`` e stamps ingênuos.
    Nunca chuta fuso em silêncio: um stamp ingênuo permanece ingênuo, a menos que
    ``default_tz`` seja passado explicitamente (os chamadores usam BRT via ensure_aware).
    Retorna ``None`` só quando é genuinamente impossível parsear — nunca "recente" mudo.
    """
    if s is None:
        return None
    if isinstance(s, datetime):
        if s.tzinfo is None and default_tz is not None:
            return s.replace(tzinfo=default_tz)
        return s
    txt = str(s).strip()
    if not txt:
        return None
    if txt[-1] in ("Z", "z"):
        txt = txt[:-1] + "+00:00"
    txt = _SPACE_SEP.sub(r"\1T\2", txt)
    txt = _OFFSET_NO_COLON.sub(r"\1\2:\3", txt)
    txt = _FRAC_TRIM.sub(r"\1", txt)
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None and default_tz is not None:
        dt = dt.replace(tzinfo=default_tz)
    return dt
CLOSE_EPS = timedelta(seconds=45)       # close deve ser antes de kickoff − ε
LATE_OPEN = timedelta(hours=3)          # open < 3h do KO = late
PREMATCH_MIN = timedelta(minutes=2)     # só fecha key se agora ≥ KO − 2min
ACCEPTED_CLV_QUALITY = frozenset(("full_prematch", "late_open"))


def parse_ts(s):
    """Alias histórico: parser tz-flex, ingênuo permanece ingênuo (o chamador aplica
    ensure_aware p/ o default BRT). Agora cobre também ``Z`` e ``-0300`` no py3.9."""
    if not s:
        return None
    return parse_iso_flex(s)


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

def valid_decimal_odd(value) -> bool:
    """True para uma odd decimal finita e estritamente maior que 1."""
    try:
        odd = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(odd) and odd > 1.0


def strict_clv_reason(k: dict) -> str | None:
    """Motivo de rejeicao do CLV, ou ``None`` quando a linha e valida.

    Este e o unico gate usado por headline, recortes e tabela. Push e um
    resultado perfeitamente valido para CLV: ``won=None`` nao invalida a captura.
    """
    if k.get("status") != "settled":
        return "not_settled"
    if not valid_decimal_odd(k.get("open_odd")):
        return "invalid_open"
    if not valid_decimal_odd(k.get("close_odd")):
        return "invalid_close"

    ko = ensure_aware(parse_ts(k.get("kickoff")))
    ots = ensure_aware(parse_ts(k.get("open_ts")))
    cts = ensure_aware(parse_ts(k.get("close_ts")))
    if not ko:
        return "missing_kickoff"
    if not ots:
        return "missing_open_ts"
    if not cts:
        return "missing_close_ts"
    if ots >= ko - CLOSE_EPS:
        return "open_not_prematch"
    if cts >= ko - CLOSE_EPS:
        return "close_not_prematch"

    raw = k.get("capture_quality") or compute_capture_quality(k)
    band = raw.get("band") if isinstance(raw, dict) else raw
    if band not in ACCEPTED_CLV_QUALITY:
        return "quality_" + str(band or "unknown")
    return None


def is_strict_clv(k: dict) -> bool:
    """Gate canonico de CLV pre-jogo."""
    return strict_clv_reason(k) is None



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
