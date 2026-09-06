# -*- coding: utf-8 -*-
"""Pure, shared name contracts for captured Betano and 7k events.

No capture, pricing, line/odds selection or publication side effects. Unknown
periods/participants fail closed. Historical and board consumers use the same
contract; raw snapshots remain unchanged.
"""
import re
import unicodedata

from canonical import norm_team

BETANO_MK = {
    "Total de Cartões": "Cartões", "Total de Faltas": "Faltas",
    "Total de chutes": "Finalizações", "Escanteios": "Escanteios",
    "Chutes no gol": "Chutes no gol", "Total de Impedimentos": "Impedimentos",
    "Total de laterais": "Laterais", "Total de tiros de meta": "Tiros de meta",
    "Total de Desarmes": "Desarmes",
}


def _fold(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFD", str(value or ""))
                            if unicodedata.category(c) != "Mn").lower().split())


_MATCH = {_fold(k): v for k, v in BETANO_MK.items()}
_STATS = {_fold(k.removeprefix("Total de ")): v for k, v in BETANO_MK.items()}
_TEAM = re.compile(
    r"^(.+?)\s+(?:Total de\s+(Cart[oõ]es|Faltas|chutes|Escanteios|Impedimentos|"
    r"laterais|tiros de meta|Chutes no gol|Desarmes)|(Chutes no gol))$", re.I)
_UNSUPPORTED = re.compile(r"\b(?:tempo|parte|jogador|jogadora|player|half|quarter)\b", re.I)


def event_participants(name):
    """Only an unambiguous two-part event name is usable for team contracts."""
    parts = [p.strip() for p in str(name or "").replace(" vs. ", " - ")
             .replace(" vs ", " - ").split(" - ")]
    return tuple(parts) if len(parts) == 2 and all(parts) else ()


def betano_team(name, participants=None, league=""):
    """Return (stat, raw participant), never a period or player contract.

    ``participants=None`` is syntax-only (legacy diagnostic callers). Production
    readers pass event_participants, including () for malformed event names.
    """
    raw = str(name or "").strip()
    if _UNSUPPORTED.search(_fold(raw)):
        return None
    match = _TEAM.fullmatch(raw)
    if not match:
        return None
    team = match.group(1).strip()
    stat = _STATS.get(_fold(match.group(2) or match.group(3)))
    if not stat:
        return None
    if participants is not None:
        candidate = norm_team(team, league=league)
        hits = [p for p in participants if candidate == norm_team(p, league=league)]
        if not candidate or len(hits) != 1:
            return None
    return stat, team


def betano_market(name, participants=None, league=""):
    """Return (stat, None) for match O/U, or (stat, raw team) for team O/U."""
    match = _MATCH.get(_fold(name))
    if match:
        return match, None
    return betano_team(name, participants, league)


def normalize_7k_event_name(name):
    """Collapse exact adjacent duplicated participants; preserve real hyphens.

    A malformed historical snapshot can be read safely before the next fetch.
    Existing duplicated-away handling remains; the symmetric three-part home
    case fixes ``OB Odense - OB Odense vs FC Copenhague``.
    """
    raw = str(name or "").replace(" vs ", " - ")
    parts = [p.strip() for p in raw.split(" - ")]
    if len(parts) >= 3 and parts[-1] and parts[-1] == parts[-2]:
        parts = parts[:-1]
    if len(parts) == 3 and parts[0] and parts[0] == parts[1]:
        parts = parts[1:]
    return " - ".join(parts)
