# -*- coding: utf-8 -*-
"""Pure, shared name contracts for captured Betano and 7k events.

No capture, pricing or publication side effects. Atomic quote selection only. Unknown
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


# Shared quote contract for board, history reader and capture counters.
_BETANO_TABS = ("cartoes", "estatisticas", "principais_ou", "escanteios")
_BETANO_ASIAN_CARDS = "Asiático (Mais/Menos) Total de Cartões"


def normalize_betano_markets(event):
    """Return (mercados, mercados_time) using one immutable quote pair per line.

    Only the exact full-time Asian cards name is new. Existing name/participant
    contracts remain unchanged. Standard totals beat Asian variants at the same
    numeric line; then fixed tab priority and first complete source row win.
    All supported lines are integer/half, never quarter/split-stake lines.
    The source event is never mutated, and no historical storage is consulted.
    """
    import math

    if not isinstance(event, dict) or not isinstance(event.get("markets"), dict):
        return {}, {}
    participants = event_participants(event.get("name"))
    league = event.get("league") or ""
    selected = {}
    for tab_rank, tab in enumerate(_BETANO_TABS):
        markets = event["markets"].get(tab) or []
        if not isinstance(markets, list):
            continue
        for row_rank, market in enumerate(markets):
            if not isinstance(market, dict):
                continue
            name = str(market.get("market") or "").strip()
            asian = _fold(name) == _fold(_BETANO_ASIAN_CARDS)
            parsed = ("Cartões", None) if asian else betano_market(name, participants, league)
            if parsed is None:
                continue
            # A third outcome cannot be silently converted to a push contract.
            if market.get("three_way") or any(market.get(k) is not None for k in ("exact", "exactly")):
                continue
            values = [market.get(k) for k in ("line", "over", "under")]
            if any(isinstance(v, bool) or v is None for v in values):
                continue
            try:
                line, over, under = map(float, values)
            except (TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(v) for v in (line, over, under)) or line < 0:
                continue
            half_value = line * 2
            if not math.isfinite(half_value):
                continue
            half_units = round(half_value)
            if abs(half_value - half_units) > 1e-9:
                continue
            line = half_units / 2.0
            # Keep the board's existing two-decimal quote representation. Both
            # sides must be valid in the SAME row, before and after rounding.
            if over <= 1 or under <= 1:
                continue
            over, under = round(over, 2), round(under, 2)
            if over <= 1 or under <= 1:
                continue
            canon, team = parsed
            key = (canon, team, line)
            priority = (int(asian), tab_rank, row_rank)
            if key in selected and selected[key][0] <= priority:
                continue
            row = {
                "linha": line, "over": over, "under": under,
                "source_market": name, "source_tab": tab,
                "market_family": "asian_total" if asian else "standard_total",
                # Quote-line semantics only. Do not overload settlement_rule,
                # which the RDU uses separately for card-count/result semantics.
                "settlement": "push_on_equal" if half_units % 2 == 0 else "no_push",
            }
            selected[key] = (priority, row)

    match, teams = {}, {}
    for (canon, team, line), (_, row) in sorted(
            selected.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])):
        if team is None:
            match.setdefault(canon, []).append(row)
        else:
            teams.setdefault(canon, {}).setdefault(team, []).append(row)
    return match, teams
