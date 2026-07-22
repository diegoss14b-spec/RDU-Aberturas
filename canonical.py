# -*- coding: utf-8 -*-
"""canonical.py — identidade canônica de times/jogos (Mesa + Histórico).

SofaScore é a base quando há fixture. Fallback: normalização + aliases + fuzzy.
Usado por build_board, history_ingest, build_history, build_moves, testes.
"""
from __future__ import annotations
import json, os, re
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

STOP = {"fc", "cf", "ec", "sc", "ca", "ac", "afc", "club", "clube", "futebol", "if", "bk",
        # prefixos/sufixos societários que as casas usam de forma inconsistente
        # (SL Benfica vs Benfica, SK Brann vs Brann, IK Start vs Start, ...)
        "sl", "sk", "ik", "fk", "cs", "cd", "ud", "umf"}
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
    # Clube Atlético Piauiense — grafado como "CAP PI" na Superbet e
    # "CA Piauiense" na EstrelaBet (fuzzy nunca junta "cap pi" com o nome cheio).
    # Variantes (F) preservam o gênero (brief 22/07 §7).
    "cap pi": "atletico piauiense", "cap pi f": "atletico piauiense f",
    "piauiense": "atletico piauiense", "piauiense f": "atletico piauiense f",
    "clube atletico piauiense": "atletico piauiense",
    "clube atletico piauiense f": "atletico piauiense f",
    # variantes observadas no banco (auditoria 22/07) que o fuzzy não junta:
    "kuopian palloseura": "kups", "kuopio ps": "kups", "kups kuopio": "kups",
    "kuopion palloseura": "kups",
    "estrela vermelha": "crvena zvezda",  # tradução PT de Red Star Belgrade
}

# limiares match Sofa
SOFA_TIME_TOL_MIN = 45
SOFA_PAIR_MIN = 72
SOFA_PAIR_EXACTISH = 88     # gscore quase exato: dispensa lado "forte" (nomes 100% fracos, ex. Nacional x Universidad)
SOFA_ONE_SIDE = 86
SOFA_ONE_SIDE_TIME = 25
SOFA_SECOND_SIDE = 65       # one_side exige evidência do SEGUNDO lado ≥ isto (§6 brief 22/07)
SOFA_SLOT_TIME = 20
SOFA_SLOT_SECOND = 55       # slot_unique também exige segundo lado
SOFA_TOKEN_MIN = 4

# ---------------------------------------------------------------------------
# Tokens FRACOS (caso Sporting, 22/07/2026): prefixos/genéricos que aparecem em
# dezenas de clubes e NÃO podem sustentar associação unilateral ("Sporting
# Cristal" ≠ "Sporting Kansas City"). Configurável via data/config/weak_tokens.json
# (lista JSON de strings) sem mexer no código.
# ---------------------------------------------------------------------------
_WEAK_DEFAULT = {
    "sporting", "sport", "united", "unidos", "city", "club", "clube", "real",
    "atletico", "atletica", "athletic", "nacional", "national", "deportivo",
    "deportiva", "deportes", "racing", "independiente", "internacional", "inter",
    "universidad", "universitario", "universidade", "america", "juventud",
    "juventude", "union", "olimpia", "olimpic", "olympic", "central", "junior",
}


def _load_weak_tokens():
    cfg = ROOT / "data" / "config" / "weak_tokens.json"
    out = set(_WEAK_DEFAULT)
    try:
        if cfg.is_file():
            extra = json.loads(cfg.read_text(encoding="utf-8"))
            if isinstance(extra, list):
                out |= {unidecode(str(t)).lower().strip() for t in extra if str(t).strip()}
    except Exception:
        pass
    return out


WEAK_TOKENS = _load_weak_tokens()


def n(s):
    return unidecode((s or "").lower())


def norm_team(name: str) -> str:
    s = n(name).strip()
    # alias ANTES do strip de UF: "CAP PI" perderia o "PI" pro STATE e viraria "cap"
    pre = " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())
    if pre in ALIASES:
        return ALIASES[pre]
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


def strip_weak(s: str) -> str:
    """Remove tokens fracos do nome normalizado. Nome só de tokens fracos → ''."""
    return " ".join(t for t in (s or "").split() if t not in WEAK_TOKENS)


def side_hit_strong(book_side: str, sofa_side: str) -> float:
    """side_hit calculado SEM tokens fracos: 'sporting cristal' × 'sporting kansas
    city' cai de 92 pra ~20. Nome composto só de fracos não sustenta nada (0)."""
    a, b = strip_weak(book_side), strip_weak(sofa_side)
    if not a or not b:
        return 0.0
    r = float(ratio(a, b))
    for ta in tokens(a):
        for tb in tokens(b):
            if ta == tb or ta in tb or tb in ta:
                r = max(r, 92.0)
    return r


def _pair_flags(hn, an):
    """Marcadores (F/B/II/U20...) do confronto, como multiconjunto ordenado."""
    return tuple(sorted((frozenset(_flags(hn)), frozenset(_flags(an)))))


def flags_compatible(hn, an, f_hn, f_an) -> bool:
    """Feminino/B/sub-XX de um lado só = jogos diferentes (guarda de gênero)."""
    return _pair_flags(hn, an) == _pair_flags(f_hn, f_an)


def league_incompatible(a, b) -> bool:
    """Fingerprints de competição CONHECIDOS e diferentes = nunca casar
    automaticamente (MLS × Sul-Americana, brief 22/07 §6)."""
    return bool(a) and bool(b) and a != b


def league_fp(lg: str):
    l = n(lg or "")
    # competições continentais/regionais que já se confundiram (caso Sporting):
    if "sudamericana" in l or "sul-americana" in l or "sul americana" in l or "sulamericana" in l:
        return "sula"
    if "libertadores" in l:
        return "liberta"
    if "major league soccer" in l or re.search(r"\bmls\b", l):
        return "mls"
    if "liga mx" in l or "ligamx" in l or ("mexic" in l and ("liga" in l or "apertura" in l or "clausura" in l)):
        return "mx"
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
        if not src.is_file():
            return []
        data = json.loads(src.read_text(encoding="utf-8"))
        fixtures = data.get("fixtures") or []
        if int(meta.get("n") or 0) != len(fixtures) or not fixtures:
            return []
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


def match_to_sofa(hn, an, day_brt, start_dt, fixtures, book_league="", _lfp=None,
                  _rejections=None, _expand=False):
    """→ (fixture|None, score, method|None, info dict).

    Regras endurecidas (brief 22/07 §6 — caso Sporting):
      - liga com fingerprint conhecido e DIFERENTE do fixture = candidato bloqueado
        (MLS × Sul-Americana nunca casam automaticamente);
      - marcadores F/B/II/U20 divergentes = bloqueado (gênero/time B);
      - one_side exige lado forte SEM tokens fracos (≥ SOFA_ONE_SIDE) e evidência
        do SEGUNDO lado (≥ SOFA_SECOND_SIDE): token genérico + horário nunca bastam;
      - pair exige ao menos um lado forte, ou gscore quase exato (≥ SOFA_PAIR_EXACTISH);
      - slot_unique também exige lado forte e segundo lado.
    info = {method, dt_min, rh, ra, strong, second, book_lfp, fx_lfp, rejections}.
    """
    rejections = _rejections if _rejections is not None else []
    info = {"rejections": rejections}
    if not fixtures or not hn:
        return None, 0.0, None, info
    cands = [f for f in fixtures if f.get("day_brt") == day_brt]
    if not cands:
        return None, 0.0, None, info
    book_lfp = _lfp if _lfp is not None else league_fp(book_league)
    info["book_lfp"] = book_lfp
    same_lg = [] if _expand else \
        ([f for f in cands if book_lfp and f.get("_lfp") == book_lfp] if book_lfp else [])
    pool = same_lg if same_lg else cands

    best, best_sc, best_m, best_info = None, -1.0, None, None

    def reject(f, reason, **extra):
        if len(rejections) < 6:
            r = {"sofa_id": f.get("sofa_id"), "fixture": f"{f.get('home')} x {f.get('away')}",
                 "reason": reason}
            r.update(extra)
            rejections.append(r)

    def consider(f, sc, method, ev):
        nonlocal best, best_sc, best_m, best_info
        if sc > best_sc:
            best, best_sc, best_m, best_info = f, sc, method, ev

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
        # combinações lado-a-lado: (forte do lado âncora, evidência plain do outro lado)
        combos = [
            (side_hit_strong(hn, f["_hn"]), side_hit(an, f["_an"]) if an else 0.0),
            (side_hit_strong(hn, f["_an"]), side_hit(an, f["_hn"]) if an else 0.0),
        ]
        if an:
            combos += [
                (side_hit_strong(an, f["_hn"]), side_hit(hn, f["_an"])),
                (side_hit_strong(an, f["_an"]), side_hit(hn, f["_hn"])),
            ]
        strong, second = max(combos, key=lambda t: (t[0], t[1]))
        name_evidence = pair >= SOFA_PAIR_MIN or max(rh, ra) >= 75

        # --- guardas conjuntas (liga + gênero/flags) ---
        if league_incompatible(book_lfp, f.get("_lfp")):
            if name_evidence:
                reject(f, "league_incompatible",
                       book_lfp=book_lfp, fx_lfp=f.get("_lfp"), dt_min=round(dt_min, 1))
            continue
        if an and not flags_compatible(hn, an, f["_hn"], f["_an"]):
            if name_evidence:
                reject(f, "flags_mismatch", dt_min=round(dt_min, 1))
            continue

        ev = {"dt_min": round(dt_min, 1), "rh": round(rh, 1), "ra": round(ra, 1),
              "strong": round(strong, 1), "second": round(second, 1),
              "fx_lfp": f.get("_lfp"), "book_lfp": book_lfp}

        both_ok = (side_hit_strong(hn, f["_hn"]) >= 70 and side_hit_strong(an, f["_an"]) >= 70) or \
                  (side_hit_strong(hn, f["_an"]) >= 70 and side_hit_strong(an, f["_hn"]) >= 70) if an else False
        pair_ok = pair >= SOFA_PAIR_MIN and (strong >= 70 or pair >= SOFA_PAIR_EXACTISH)
        if dt_min <= SOFA_TIME_TOL_MIN and (pair_ok or both_ok):
            consider(f, max(pair, (rh + ra) / 2) + max(0, 40 - dt_min), "pair", ev)
        elif dt_min <= SOFA_TIME_TOL_MIN and pair >= SOFA_PAIR_MIN and not pair_ok:
            reject(f, "pair_weak_tokens_only", **{k: ev[k] for k in ("strong", "dt_min")})

        if dt_min <= SOFA_ONE_SIDE_TIME and strong >= SOFA_ONE_SIDE:
            if second < SOFA_SECOND_SIDE:
                reject(f, "one_side_second_side_weak", **{k: ev[k] for k in ("strong", "second", "dt_min")})
            else:
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
                    consider(f, strong + max(0, 25 - dt_min) + bonus, "one_side", ev)
        elif dt_min <= SOFA_ONE_SIDE_TIME and max(rh, ra) >= SOFA_ONE_SIDE and strong < SOFA_ONE_SIDE:
            reject(f, "one_side_weak_token_only", **{k: ev[k] for k in ("rh", "ra", "strong", "dt_min")})

        if dt_min <= SOFA_SLOT_TIME and strong >= 75 and second >= SOFA_SLOT_SECOND:
            slot = []
            for g in pool:
                try:
                    d2 = abs(int(f["start_ts"]) - int(g["start_ts"])) / 60.0
                except Exception:
                    d2 = 999
                if d2 <= SOFA_SLOT_TIME:
                    slot.append(g)
            if len(slot) == 1:
                consider(f, 80 + strong * 0.2 + max(0, 20 - dt_min), "slot_unique", ev)

    if best is None and same_lg and pool is same_lg and not _expand:
        # re-tenta no dia inteiro, MAS preservando o fingerprint da casa
        # (a incompatibilidade de liga continua valendo na segunda passada)
        return match_to_sofa(hn, an, day_brt, start_dt, cands, book_league="",
                             _lfp=book_lfp, _rejections=rejections, _expand=True)

    if best is None:
        return None, 0.0, None, info
    out_info = dict(best_info or {})
    out_info["rejections"] = rejections
    return best, best_sc, best_m, out_info


# teto de confiança por método — one_side nunca satura em 100 (§6 req. 8)
_METHOD_CONF_CAP = {"pair": 95, "one_side": 85, "slot_unique": 70}


def resolve_fixture(home_raw, away_raw, start, league="", fixtures=None):
    """Resolve evento de casa → identidade canônica.
    Returns dict: home, away, hn, an, day, sofa_id, match_method, kickoff_iso,
    match_confidence, match_evidence (método/scores/liga/rejeições).
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
    fx, sc, method, info = match_to_sofa(hn, an, day, dt, fixtures, book_league=league or "")
    if fx is not None:
        cap = _METHOD_CONF_CAP.get(method or "", 90)
        base = info.get("strong") if method == "one_side" else sc
        conf = min(cap, int(base)) if base else min(cap, 70)
        evidence = {k: info.get(k) for k in
                    ("dt_min", "rh", "ra", "strong", "second", "book_lfp", "fx_lfp")}
        evidence["method"] = method
        if info.get("rejections"):
            evidence["rejections"] = info["rejections"][:3]
        # kickoff canônico = do fixture Sofa (autoridade; conserta virada de fuso)
        fx_kick = None
        if fx.get("start_ts"):
            try:
                fx_kick = datetime.fromtimestamp(int(fx["start_ts"]), tz=timezone.utc) \
                    .astimezone(BRT).strftime("%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                fx_kick = None
        return {
            "home": fx["home"],
            "away": fx["away"],
            "hn": fx["_hn"],
            "an": fx["_an"],
            "day": fx.get("day_brt") or day,
            "sofa_id": fx.get("sofa_id"),
            "match_method": method or "sofa",
            "kickoff_iso": fx_kick or kick_iso or fx.get("start_utc"),
            "match_confidence": conf,
            "match_evidence": evidence,
            "league": fx.get("league") or league,
        }
    out = {
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
    if info.get("rejections"):
        out["match_rejections"] = info["rejections"][:3]
    return out


# ---------------------------------------------------------------------------
# Unificação fuzzy de identidades de jogo (dedup do histórico, 20/07/2026).
# O mesmo jogo aparecia 2x+ no banco porque cada casa grafa os times de um jeito
# ("SL Benfica" vs "Benfica", "Náutico" vs "Náutico Capibaribe") e/ou grava a
# data local vs UTC (±1 dia). Aqui juntamos gids do MESMO confronto.
# ---------------------------------------------------------------------------
UNIFY_MIN = 90          # semelhança mínima do par (gscore) pra considerar mesmo jogo
# tolerância de kickoff pra unir gids em dias civis diferentes (virada de fuso):
# 23:00 × 00:00 do dia seguinte é o MESMO jogo; 21h de diferença NÃO é.
UNIFY_KICK_TOL_MIN = int(os.environ.get("UNIFY_KICK_TOL_MIN", "75"))
# marcadores de time B/feminino/reserva: se um lado tem e o outro não, NÃO junta
FLAG_TOKENS = {"f", "fem", "w", "b", "ii", "r", "res", "sub", "jr",
               "u17", "u19", "u20", "u21", "u23"}


def _flags(name):
    return {t for t in (name or "").split() if t in FLAG_TOKENS}


def _day_delta(a, b):
    try:
        da = datetime.strptime(a, "%Y-%m-%d")
        db = datetime.strptime(b, "%Y-%m-%d")
        return abs((da - db).days)
    except Exception:
        return 99


def unify_gids(games):
    """games: {gid: {day, hn, an, n, sofa, kick_ts?}} → {gid_antigo: gid_canônico}.

    Junta gids do mesmo confronto (dia ±1 + nomes fuzzy ≥ UNIFY_MIN, com guarda
    de marcadores F/B/II/U20). Canônico = sofa > mais registros > gid menor.
    Nunca junta dois gids sofa distintos (Sofa é autoridade).
    Guarda de kickoff (22/07): quando os dois lados têm kick_ts (epoch s), só une
    se |Δ| ≤ UNIFY_KICK_TOL_MIN — evita colar partidas DISTINTAS do mesmo time em
    dias adjacentes, mantendo a união legítima de virada de fuso (23:00 × 00:00).
    """
    by_day = {}
    for gid, g in games.items():
        if g.get("day"):
            by_day.setdefault(g["day"], []).append(gid)

    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    roots_sofa = {gid: (gid if g.get("sofa") else None) for gid, g in games.items()}

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        sa, sb = roots_sofa.get(ra), roots_sofa.get(rb)
        if sa and sb and sa != sb:
            return  # dois jogos Sofa diferentes — não junta
        parent[rb] = ra
        roots_sofa[ra] = sa or sb

    days = sorted(by_day)
    for i, day in enumerate(days):
        pool = list(by_day[day])
        # ±1 dia (fuso local vs UTC)
        for j in (i + 1, i + 2):
            if j < len(days) and _day_delta(day, days[j]) <= 1:
                pool += by_day[days[j]]
        pool = sorted(set(pool))
        for x in range(len(pool)):
            ga = games[pool[x]]
            if not ga.get("hn") or not ga.get("an"):
                continue
            for y in range(x + 1, len(pool)):
                gb = games[pool[y]]
                if not gb.get("hn") or not gb.get("an"):
                    continue
                if ga.get("sofa") and gb.get("sofa"):
                    continue
                if _day_delta(ga.get("day") or "", gb.get("day") or "") > 1:
                    continue
                ka, kb = ga.get("kick_ts"), gb.get("kick_ts")
                if ka and kb and abs(float(ka) - float(kb)) > UNIFY_KICK_TOL_MIN * 60:
                    continue  # kickoffs reais longe demais: partidas distintas
                straight = min(ratio(ga["hn"], gb["hn"]), ratio(ga["an"], gb["an"]))
                crossed = min(ratio(ga["hn"], gb["an"]), ratio(ga["an"], gb["hn"]))
                s = max(straight, crossed)
                if s < UNIFY_MIN:
                    continue
                if straight >= crossed:
                    sides = ((ga["hn"], gb["hn"]), (ga["an"], gb["an"]))
                else:
                    sides = ((ga["hn"], gb["an"]), (ga["an"], gb["hn"]))
                if any(_flags(p) != _flags(q) for p, q in sides):
                    continue  # feminino/B/sub-XX de um lado só — jogos diferentes
                union(pool[x], pool[y])

    clusters = {}
    for gid in games:
        clusters.setdefault(find(gid), []).append(gid)

    alias = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        def rank(gid):
            g = games[gid]
            return (0 if g.get("sofa") else 1, -int(g.get("n") or 0), gid)
        canonical = sorted(members, key=rank)[0]
        for gid in members:
            if gid != canonical:
                alias[gid] = canonical
    return alias


def history_key(casa, day, hn, an, mercado, linha, lado, sofa_id=None):
    """Chave canônica do banco de odds.
    Com sofa: casa|sofa:{id}|mercado|linha|lado
    Sem:     casa|day|hn|an|mercado|linha|lado
    """
    lado = "over" if str(lado).lower() in ("over", "mais") else "under"
    if sofa_id:
        return f"{casa}|sofa:{sofa_id}|{mercado}|{linha}|{lado}"
    return f"{casa}|{day}|{hn}|{an}|{mercado}|{linha}|{lado}"


# ---------------------------------------------------------------------------
# Pureza de identidade por sofa_id (brief 22/07 §6 req. 7): um mesmo evento Sofa
# não pode conter pares crus INCOMPATÍVEIS (ex.: Cristal×Bragantino e KC×Minnesota
# nas mesmas chaves). O gate usa isto pra bloquear publicação.
# ---------------------------------------------------------------------------
PURITY_SIDE_MIN = 55  # cada lado precisa ser compatível ≥ isto pra ser o MESMO jogo


def _side_compat(a, b):
    return side_hit(a, b) >= PURITY_SIDE_MIN


def same_game_pairs(p, q):
    """Dois pares crus são o MESMO jogo se existir orientação em que os DOIS
    lados são compatíveis. Grafias do mesmo clube ('Hearts'/'Heart of Midlothian',
    'AGF'/'AGF Aarhus') passam; jogos diferentes falham em pelo menos um lado
    ('Sporting Cristal×Bragantino' vs 'Sporting KC×Minnesota': home 92, away 40)."""
    return (_side_compat(p[0], q[0]) and _side_compat(p[1], q[1])) or \
           (_side_compat(p[0], q[1]) and _side_compat(p[1], q[0]))


def sofa_purity(keys, only_ids=None):
    """keys = dict do banco (chave → registro). → {sofa_id: relatório}.

    Agrupa os pares crus (home_raw, away_raw normalizados) de cada sofa_id em
    clusters de "mesmo jogo" (compatibilidade lado-a-lado, ligação simples).
    n_clusters > 1 ⇒ identidade IMPURA (duas partidas reais sob o mesmo id).
    """
    by_sid = {}
    for k, v in (keys or {}).items():
        if str(k).startswith("__") or not isinstance(v, dict):
            continue
        meta = parse_history_key(k)
        sid = v.get("sofa_id") or meta.get("sofa_id")
        if not sid:
            continue
        if only_ids is not None and str(sid) not in only_ids:
            continue
        hr = norm_team(v.get("home_raw") or v.get("home_norm") or "")
        ar = norm_team(v.get("away_raw") or v.get("away_norm") or "")
        if not hr or not ar:
            continue
        by_sid.setdefault(str(sid), {}).setdefault((hr, ar), 0)
        by_sid[str(sid)][(hr, ar)] += 1
    out = {}
    for sid, pair_counts in by_sid.items():
        clusters = []
        for p in sorted(pair_counts):
            placed = False
            for c in clusters:
                if any(same_game_pairs(p, q) for q in c):
                    c.append(p)
                    placed = True
                    break
            if not placed:
                clusters.append([p])
        # ligação simples pode deixar clusters que se tocam por um membro tardio —
        # funde clusters que compartilhem compatibilidade
        merged = []
        for c in clusters:
            target = None
            for m in merged:
                if any(same_game_pairs(p, q) for p in c for q in m):
                    target = m
                    break
            if target is not None:
                target.extend(c)
            else:
                merged.append(c)
        out[sid] = {
            "n_pairs": len(pair_counts),
            "n_keys": sum(pair_counts.values()),
            "n_clusters": len(merged),
            "impure": len(merged) > 1,
            "clusters": [[" x ".join(p) for p in c] for c in merged],
            "cluster_keys": [sum(pair_counts[p] for p in c) for c in merged],
        }
    return out


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
