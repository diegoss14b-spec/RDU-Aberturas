# -*- coding: utf-8 -*-
"""canonical.py — identidade canônica de times/jogos (Mesa + Histórico).

SofaScore é a base quando há fixture. Fallback: normalização + aliases + fuzzy.
Usado por build_board, history_ingest, build_history, build_moves, testes.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from unidecode import unidecode
except Exception:
    def unidecode(s):  # type: ignore
        return s

try:
    from rapidfuzz import fuzz
    def ratio(a, b):
        return fuzz.token_set_ratio(a or "", b or "")
except Exception:
    import difflib
    def ratio(a, b):
        return 100 * difflib.SequenceMatcher(None, a or "", b or "").ratio()

ROOT = Path(__file__).resolve().parent
BRT = timezone(timedelta(hours=-3))

STOP = {"fc", "cf", "ec", "sc", "ca", "ac", "afc", "club", "clube", "futebol", "if", "bk", "sc"}
# exige separador (espaço/hífen) antes do UF — evita "france"→"fran", "peace"→"pea"
STATE = re.compile(r"[- ](pr|sp|rj|mg|rs|go|ce|pe|ba|mt|ms|pa|to|al|se|rn|pb|pi|ap|ac|ro|rr|df)$")

ALIASES = {
    "sport": "sport recife",
    "bragantino": "red bull bragantino", "rb bragantino": "red bull bragantino",
    "vasco": "vasco da gama", "athletico": "athletico paranaense",
    "gremio novorizontino": "novorizontino", "operario": "operario ferroviario",
    "operario pr": "operario ferroviario", "crb": "crb al", "crb al": "crb al",
    "france": "franca", "spain": "espanha", "england": "inglaterra",
    "argentina": "argentina", "morocco": "marrocos", "marrocos": "marrocos",
    "america mineiro": "america mg", "america mg": "america mg",
    "athletic club": "athletic club mg", "athletic club mg": "athletic club mg",
    "ceara": "ceara", "ceara ce": "ceara", "londrina": "londrina", "londrina pr": "londrina",
    "sao bernardo": "sao bernardo", "botafogo sp": "botafogo sp",
    "vila nova": "vila nova", "vila nova go": "vila nova",
}

# limiares match Sofa
SOFA_TIME_TOL_MIN = 45
SOFA_PAIR_MIN = 72
SOFA_ONE_SIDE = 86
SOFA_ONE_SIDE_TIME = 25
SOFA_SLOT_TIME = 20
SOFA_TOKEN_MIN = 4


def n(s):
    return unidecode((s or "").lower())


def norm_team(name: str) -> str:
    s = n(name).strip()
    s = STATE.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = " ".join(s.split())
    # alias ANTES de dropar stop-words (ex.: "athletic club" → mg)
    if s in ALIASES:
        return ALIASES[s]
    toks = [t for t in s.split() if t not in STOP]
    key = " ".join(toks) or s
    return ALIASES.get(key, key)


def gscore(ah, aa, bh, ba) -> float:
    return max(min(ratio(ah, bh), ratio(aa, ba)), min(ratio(ah, ba), ratio(aa, bh)))


def tokens(s: str):
    return [t for t in (s or "").split() if len(t) >= SOFA_TOKEN_MIN]


def side_hit(book_side: str, sofa_side: str) -> float:
    if not book_side or not sofa_side:
        return 0.0
    r = float(ratio(book_side, sofa_side))
    for a in tokens(book_side):
        for b in tokens(sofa_side):
            if a == b or a in b or b in a:
                r = max(r, 92.0)
    return r


def league_fp(lg: str):
    l = n(lg or "")
    if "serie b" in l or "série b" in l or "serie-b" in l or "br-b" in l:
        return "br-b"
    if "serie c" in l or "série c" in l:
        return "br-c"
    if ("brasileir" in l or "serie a" in l or "br-a" in l) and "serie b" not in l and "série b" not in l:
        return "br-a"
    if "world cup" in l or "copa do mundo" in l or l.strip() == "wc" or "fifa" in l:
        return "wc"
    if "copa" in l and "brasil" in l:
        return "br-cdb"
    if "premier league" in l or ("premier" in l and "ingl" in l):
        return "epl"
    if "laliga" in l or "la liga" in l:
        return "laliga"
    if "champions" in l:
        return "ucl"
    if "allsvenskan" in l:
        return "allsv"
    if "uruguay" in l or "uruguai" in l or "auf" in l:
        return "uy"
    if "ecuad" in l or "ligapro" in l:
        return "ec"
    if "china" in l or "csl" in l:
        return "csl"
    if "russia" in l or "russian" in l:
        return "ru"
    return None


def parse_start(s):
    """ms / s / ISO → datetime BRT aware."""
    if s is None:
        return None
    try:
        if isinstance(s, (int, float)) or (isinstance(s, str) and str(s).strip().isdigit()):
            num = int(float(s))
            if num > 1e11:
                num = num / 1000.0
            return datetime.fromtimestamp(num, tz=BRT)
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None


def load_sofa_fixtures(root: Path | None = None):
    root = root or ROOT
    ptr = root / "data" / "fixtures" / "sofa_latest.json"
    if not ptr.exists():
        return []
    try:
        meta = json.loads(ptr.read_text(encoding="utf-8"))
        src = root / "data" / "fixtures" / meta["file"]
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for f in data.get("fixtures") or []:
        f = dict(f)
        f["_hn"] = norm_team(f.get("home"))
        f["_an"] = norm_team(f.get("away"))
        f["_lfp"] = league_fp(f.get("league") or "") or league_fp(f.get("label") or "")
        out.append(f)
    return out


def _kickoff_delta_min(start_dt, fixture) -> float:
    if not start_dt or not fixture.get("start_ts"):
        return 9999.0
    try:
        fs = datetime.fromtimestamp(int(fixture["start_ts"]), tz=timezone.utc)
        if start_dt.tzinfo is None:
            sdt = start_dt.replace(tzinfo=BRT).astimezone(timezone.utc)
        else:
            sdt = start_dt.astimezone(timezone.utc)
        return abs((sdt - fs).total_seconds()) / 60.0
    except Exception:
        return 9999.0


def match_to_sofa(hn, an, day_brt, start_dt, fixtures, book_league=""):
    """→ (fixture|None, score, method|None)."""
    if not fixtures or not hn:
        return None, 0.0, None
    cands = [f for f in fixtures if f.get("day_brt") == day_brt]
    if not cands:
        return None, 0.0, None
    book_lfp = league_fp(book_league)
    same_lg = [f for f in cands if book_lfp and f.get("_lfp") == book_lfp] if book_lfp else []
    pool = same_lg if same_lg else cands

    best, best_sc, best_m = None, -1.0, None

    def consider(f, sc, method):
        nonlocal best, best_sc, best_m
        if sc > best_sc:
            best, best_sc, best_m = f, sc, method

    for f in pool:
        dt_min = _kickoff_delta_min(start_dt, f)
        if dt_min > 500 and start_dt:
            try:
                t_brt = (start_dt.astimezone(BRT) if start_dt.tzinfo else start_dt).strftime("%H:%M")
                if t_brt == f.get("time_brt"):
                    dt_min = 0
            except Exception:
                pass
        pair = gscore(hn, an, f["_hn"], f["_an"]) if an else 0
        rh = max(side_hit(hn, f["_hn"]), side_hit(hn, f["_an"]))
        ra = max(side_hit(an, f["_hn"]), side_hit(an, f["_an"])) if an else 0
        one = max(rh, ra)
        both_ok = rh >= 70 and ra >= 70

        if dt_min <= SOFA_TIME_TOL_MIN and (pair >= SOFA_PAIR_MIN or both_ok):
            consider(f, max(pair, (rh + ra) / 2) + max(0, 40 - dt_min), "pair")

        if dt_min <= SOFA_ONE_SIDE_TIME and one >= SOFA_ONE_SIDE:
            rivals = 0
            for g in pool:
                if g is f:
                    continue
                try:
                    d2 = abs(int(f["start_ts"]) - int(g["start_ts"])) / 60.0
                except Exception:
                    d2 = 999
                if d2 > SOFA_ONE_SIDE_TIME:
                    continue
                og = max(side_hit(hn, g["_hn"]), side_hit(hn, g["_an"]),
                         side_hit(an, g["_hn"]) if an else 0,
                         side_hit(an, g["_an"]) if an else 0)
                if og >= SOFA_ONE_SIDE - 2:
                    rivals += 1
            if rivals == 0:
                bonus = 5 if book_lfp and f.get("_lfp") == book_lfp else 0
                consider(f, one + max(0, 25 - dt_min) + bonus, "one_side")

        if dt_min <= SOFA_SLOT_TIME and one >= 75:
            slot = []
            for g in pool:
                try:
                    d2 = abs(int(f["start_ts"]) - int(g["start_ts"])) / 60.0
                except Exception:
                    d2 = 999
                if d2 <= SOFA_SLOT_TIME:
                    slot.append(g)
            if len(slot) == 1:
                consider(f, 80 + one * 0.2 + max(0, 20 - dt_min), "slot_unique")

    if best is None and same_lg and pool is same_lg:
        return match_to_sofa(hn, an, day_brt, start_dt, cands, book_league="")

    if best is None:
        return None, 0.0, None
    return best, best_sc, best_m


def resolve_fixture(home_raw, away_raw, start, league="", fixtures=None):
    """Resolve evento de casa → identidade canônica.
    Returns dict: home, away, hn, an, day, sofa_id, match_method, kickoff_iso, match_confidence
    """
    if fixtures is None:
        fixtures = load_sofa_fixtures()
    hn, an = norm_team(home_raw), norm_team(away_raw)
    dt = parse_start(start)
    if dt is not None:
        day = dt.strftime("%Y-%m-%d")
        kick_iso = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    else:
        day = "?"
        kick_iso = None
    fx, sc, method = match_to_sofa(hn, an, day, dt, fixtures, book_league=league or "")
    if fx is not None:
        conf = min(100, int(sc)) if sc else 70
        return {
            "home": fx["home"],
            "away": fx["away"],
            "hn": fx["_hn"],
            "an": fx["_an"],
            "day": fx.get("day_brt") or day,
            "sofa_id": fx.get("sofa_id"),
            "match_method": method or "sofa",
            "kickoff_iso": kick_iso or fx.get("start_utc"),
            "match_confidence": conf,
            "league": fx.get("league") or league,
        }
    return {
        "home": (home_raw or "").strip(),
        "away": (away_raw or "").strip(),
        "hn": hn,
        "an": an,
        "day": day,
        "sofa_id": None,
        "match_method": "unmatched",
        "kickoff_iso": kick_iso,
        "match_confidence": 40 if hn and an else 0,
        "league": league,
    }


def history_key(casa, day, hn, an, mercado, linha, lado, sofa_id=None):
    """Chave canônica do banco de odds.
    Com sofa: casa|sofa:{id}|mercado|linha|lado
    Sem:     casa|day|hn|an|mercado|linha|lado
    """
    lado = "over" if str(lado).lower() in ("over", "mais") else "under"
    if sofa_id:
        return f"{casa}|sofa:{sofa_id}|{mercado}|{linha}|{lado}"
    return f"{casa}|{day}|{hn}|{an}|{mercado}|{linha}|{lado}"


def parse_history_key(key: str):
    """Devolve dict com campos da key (legado ou sofa)."""
    p = key.split("|")
    if len(p) >= 5 and p[1].startswith("sofa:"):
        return {
            "casa": p[0], "sofa_id": p[1].replace("sofa:", "", 1),
            "day": None, "hn": None, "an": None,
            "mercado": p[2], "linha": p[3], "lado": p[4],
            "format": "sofa",
        }
    if len(p) >= 7:
        return {
            "casa": p[0], "day": p[1], "hn": p[2], "an": p[3],
            "mercado": p[4], "linha": p[5], "lado": p[6],
            "sofa_id": None, "format": "legacy",
        }
    return {"casa": p[0] if p else "?", "format": "unknown", "raw": key}
