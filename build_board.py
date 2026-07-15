# -*- coding: utf-8 -*-
"""
build_board.py — "mesa de aberturas": lista os jogos que as casas abriram mercados de
Cartões / Faltas / Finalizações(chutes) / Chutes no gol / Impedimentos / Laterais /
Tiros de meta, com as LINHAS disponíveis por casa. Primário = consciência do que há pra
analisar. Secundário = flag de VALOR onde temos modelo (Cartões, Faltas, Finalizações).

Gera valor/data/board.js  (window.BOARD = {gerado, casas, jogos:[...]}).
Fontes: data/odds/betano_latest.json (+ superbet/7k/estrelabet quando existirem, mesmo formato).
"""
import json, sys, re, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open("/dev/null", "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
try:
    from unidecode import unidecode
except Exception:
    def unidecode(s): return s
try:
    from rapidfuzz import fuzz
    def ratio(a, b): return fuzz.token_set_ratio(a, b)
except Exception:
    import difflib
    def ratio(a, b): return 100 * difflib.SequenceMatcher(None, a, b).ratio()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import os
# Titular do BOARD.valor: candidate_pricer (modelos novos, PROMOVIDOS pelo Diego em 14/07 —
# "a mesa de aberturas com os modelos novos"). value_pricers (modelos antigos) ficam como
# baseline em dual-run (arquivo paralelo, fora da UI) e como fallback se o bundle dos
# candidatos falhar. FORCE_LEGACY_BOARD=1 força os antigos no board (rollback de emergência).
from value_pricers import (
    CardsPricer, ShotsPricer, FoulsPricer, CornersPricer,
)
from pricing_math import expected_value, edge_vs_market, is_integer_line, fair_odd
from canonical import (
    norm_team, gscore as _gscore, side_hit as _side_hit, match_to_sofa,
    load_sofa_fixtures, parse_start, n as _n, SOFA_TOKEN_MIN,
)

BRT = timezone(timedelta(hours=-3))
# mercados do board (ordem de exibição) + qual tem modelo de valor
# 12/07: Escanteios entra (Diego pediu no comparador de valor — modelo v2 de 11 ligas)
MERCADOS = ["Cartões", "Faltas", "Finalizações", "Chutes no gol", "Escanteios", "Impedimentos", "Laterais", "Tiros de meta", "Desarmes"]
MERC_SET = set(MERCADOS)
MODELO = {"Cartões": "cartoes", "Faltas": "faltas", "Finalizações": "finalizacoes", "Escanteios": "escanteios"}
# limiares do flag de valor (secundário)
# MARGIN_MIN: margem implícita negativa = par promocional/incompatível (brief §4)
EV_MIN, EDGE_MIN = 0.05, 0.04
MARGIN_MIN, MARGIN_CAP = 0.0, 0.12
P_LO, P_HI = 0.15, 0.85  # P∈[15,85]% = região calibrada (evita artefato longe do μ)
MODEL_STATUS = "promoted"
MODEL_SOURCE = "candidate_pricer"
FORCE_LEGACY = os.environ.get("FORCE_LEGACY_BOARD", "").strip() in ("1", "true", "TRUE", "yes")
FUZZ_MIN = 88
# dedup de confronto entre casas (mesmo jogo grafado diferente por cada casa)
GROUP_FUZZ_TIME = 75   # mesmo horário exato + semelhança de nomes ≥ isto → mesmo confronto
GROUP_FUZZ_NAME = 88   # mesmo dia + semelhança de nomes ≥ isto → mesmo confronto (horário pode divergir)

# tupla: (cartoes, faltas, finalizacoes, escanteios)
LEAGUE_RULES = [
    (lambda l: "brasileir" in l and ("serie b" in l or "série b" in l or "- b" in l), ("B", "BR-B", None, "BR-B")),
    (lambda l: "brasileir" in l and "serie b" not in l, ("A", "BR-A", "BR", "BR-A")),
    (lambda l: "premier league" in l or ("premier" in l and "ingl" in l), ("PL", "PL", "PL", "PL")),
    (lambda l: "laliga" in l or "la liga" in l or ("primera" in l and "espan" in l), ("LL", "LL", "LL", "LL")),
    (lambda l: "serie a" in l and ("ital" in l or "itali" in l), ("SA", "SA", "SA", "SA")),
    (lambda l: "bundesliga" in l and "2" not in l, ("BU", "BU", "BU", "BU")),
    (lambda l: "ligue 1" in l, ("L1", "L1", "L1", "L1")),
    # ligas exóticas: só escanteios (modelo v2 tem CSL/BOL/ECU/NOR)
    (lambda l: "chin" in l or "super liga chinesa" in l or "csl" in l, (None, None, None, "CSL")),
    (lambda l: "bolivi" in l or "boliviano" in l, (None, None, None, "BOL")),
    (lambda l: "equador" in l or "ecuad" in l or "ligapro" in l, (None, None, None, "ECU")),
    (lambda l: "norueg" in l or "eliteserien" in l, (None, None, None, "NOR")),
]
def classify_league(lg):
    l = _n(lg)
    for pred, c in LEAGUE_RULES:
        try:
            if pred(l): return {"cartoes": c[0], "faltas": c[1], "finalizacoes": c[2], "escanteios": c[3]}
        except Exception: pass
    return None

# Betano: nome do mercado cru -> mercado canônico do board (só jogo inteiro)
BETANO_MK = {
    "Total de Cartões": "Cartões", "Total de Faltas": "Faltas", "Total de chutes": "Finalizações",
    "Escanteios": "Escanteios",
    "Chutes no gol": "Chutes no gol", "Total de Impedimentos": "Impedimentos",
    "Total de laterais": "Laterais", "Total de tiros de meta": "Tiros de meta",
}
# Betano time: "América-MG Total de Cartões" / "Londrina-PR Total de chutes"
_BETANO_TEAM = re.compile(
    r"^(.+?)\s+Total de\s+(Cart[oõ]es|Faltas|chutes|Escanteios|Impedimentos|laterais|tiros de meta|Chutes no gol)$",
    re.I,
)
_BETANO_STAT = {
    "cartões": "Cartões", "cartoes": "Cartões", "faltas": "Faltas", "chutes": "Finalizações",
    "escanteios": "Escanteios", "impedimentos": "Impedimentos", "laterais": "Laterais",
    "tiros de meta": "Tiros de meta", "chutes no gol": "Chutes no gol",
}

def _betano_team(name):
    mo = _BETANO_TEAM.match((name or "").strip())
    if not mo: return None
    team, stat = mo.group(1).strip(), mo.group(2).strip().lower()
    for k, v in _BETANO_STAT.items():
        if stat == k or unidecode(stat) == unidecode(k):
            return v, team
    return None

# board SEMPRE prefere inventário full (close não encolhe a mesa)
# stale-keep: aceita full de até 12h se a rodada atual falhou
BOARD_MAX_AGE_H = 12


def load_betano():
    """-> lista de eventos normalizados {casa, name, league, start, captured, mercados, mercados_time?}"""
    from capture_common import resolve_odds_pointer
    meta, src = resolve_odds_pointer("betano", prefer_full=True, max_age_h=BOARD_MAX_AGE_H)
    if not src:
        cs = sorted((ROOT / "data/odds").glob("betano_*.jsonl"))
        src = cs[-1] if cs else None
        meta = {}
    if not src:
        return [], None
    out = []
    for ln in src.read_text(encoding="utf-8").strip().split("\n"):
        if not ln.strip(): continue
        e = json.loads(ln)
        mk, mk_t = {}, {}
        for aba in ("cartoes", "estatisticas", "principais_ou", "escanteios"):
            for m in (e.get("markets", {}).get(aba) or []):
                mname = m.get("market") or ""
                L = m.get("line")
                if not (m.get("over") and m.get("under") and L is not None): continue
                row = {"linha": L, "over": round(m["over"], 2), "under": round(m["under"], 2)}
                canon = BETANO_MK.get(mname)
                if canon:
                    lst = mk.setdefault(canon, {})
                    if L not in lst: lst[L] = row
                    continue
                parsed = _betano_team(mname)
                if parsed and parsed[0]:
                    c, team = parsed
                    lst = mk_t.setdefault(c, {}).setdefault(team, {})
                    if L not in lst: lst[L] = row
        mk = {c: sorted(v.values(), key=lambda x: x["linha"]) for c, v in mk.items() if v}
        merc_t = {c: {t: sorted(lines.values(), key=lambda x: x["linha"])
                      for t, lines in teams.items() if lines}
                  for c, teams in mk_t.items()}
        merc_t = {c: t for c, t in merc_t.items() if t}
        if mk or merc_t:
            rec = {"casa": "Betano", "name": e.get("name"), "league": e.get("league"),
                   "start": e.get("start"), "captured": e.get("captured_at"), "mercados": mk}
            if merc_t: rec["mercados_time"] = merc_t
            if meta.get("_stale") or meta.get("mode") == "close":
                rec["_stale"] = True
            out.append(rec)
    return out, src.name


def load_normalized(book, casa_id):
    """lê JSONL via ponteiro full (stale-keep até BOARD_MAX_AGE_H).
    casa_id = id do arquivo (superbet, 7k, estrelabet, pinnacle)."""
    from capture_common import resolve_odds_pointer
    meta, src = resolve_odds_pointer(casa_id, prefer_full=True, max_age_h=BOARD_MAX_AGE_H)
    if not src:
        return []
    out = []
    stale = bool(meta.get("_stale") or meta.get("mode") == "close")
    for ln in src.read_text(encoding="utf-8").strip().split("\n"):
        if not ln.strip(): continue
        e = json.loads(ln)
        if e.get("mercados") or e.get("mercados_time"):
            rec = {"casa": e.get("casa", book), "name": e.get("name"), "league": e.get("league"),
                   "start": e.get("start"), "captured": e.get("captured_at"),
                   "mercados": e.get("mercados") or {}}
            if e.get("mercados_time"): rec["mercados_time"] = e["mercados_time"]
            if stale: rec["_stale"] = True
            out.append(rec)
    return out


def _assign_side(team_name, home, away):
    """Casa/fora por fuzzy/token; retorna 'home' | 'away' | None."""
    if not team_name: return None
    tn = norm_team(team_name)
    rh = _side_hit(tn, home) if home else 0
    ra = _side_hit(tn, away) if away else 0
    if rh >= 68 and rh >= ra: return "home"
    if ra >= 68 and ra > rh: return "away"
    return None


def de_vig(over, under):
    if not over or not under or over <= 1 or under <= 1: return None
    po, pu = 1 / over, 1 / under; tot = po + pu
    return {"p_over": po / tot, "p_under": pu / tot, "margin": tot - 1}


def sanitize_ou_ladder(linhas, margin_min=MARGIN_MIN, margin_max=MARGIN_CAP):
    """Sanea pares O/U de uma ladder (mesma família/casa).

    Rejeita: odds inválidas, margem fora de [min,max], linhas duplicadas,
    e quebras de monotonia (over deve subir com a linha; under descer).
    Retorna (linhas_ok, rejeicoes[{linha,reason}]).
    """
    if not linhas:
        return [], []
    by_line = {}
    rejects = []
    for row in linhas:
        try:
            L = float(row["linha"]); o = float(row["over"]); u = float(row["under"])
        except (KeyError, TypeError, ValueError):
            rejects.append({"linha": row.get("linha"), "reason": "parse"})
            continue
        if not (1.01 < o <= 50 and 1.01 < u <= 50):
            rejects.append({"linha": L, "reason": "odds_range"})
            continue
        if L in by_line:
            rejects.append({"linha": L, "reason": "duplicate_line"})
            continue
        margin = 1.0 / o + 1.0 / u - 1.0
        if margin < margin_min - 1e-9:
            rejects.append({"linha": L, "reason": "margin_low", "margin": round(margin, 4)})
            continue
        if margin > margin_max + 1e-9:
            rejects.append({"linha": L, "reason": "margin_high", "margin": round(margin, 4)})
            continue
        by_line[L] = {"linha": L, "over": o, "under": u,
                      "margin": round(margin, 4),
                      **{k: v for k, v in row.items() if k not in ("linha", "over", "under")}}
    ordered = [by_line[L] for L in sorted(by_line)]
    # monotonia: over não-decrescente, under não-crescente (tolerância 1%)
    ok = []
    prev_o = prev_u = None
    for row in ordered:
        bad = None
        if prev_o is not None and row["over"] + 1e-9 < prev_o * 0.99:
            bad = "mono_over"
        if prev_u is not None and row["under"] > prev_u * 1.01 + 1e-9:
            bad = bad or "mono_under"
        if bad:
            rejects.append({"linha": row["linha"], "reason": bad})
            continue
        ok.append(row)
        prev_o, prev_u = row["over"], row["under"]
    return ok, rejects


def game_state(inicio_str, now=None):
    """upcoming | started | finished | unknown a partir de 'dd/mm HH:MM' (BRT)."""
    now = now or datetime.now(BRT)
    if not inicio_str:
        return "unknown"
    m = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", str(inicio_str).strip())
    if not m:
        return "unknown"
    d, mo, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    y = now.year
    try:
        kick = datetime(y, mo, d, h, mi, tzinfo=BRT)
    except ValueError:
        return "unknown"
    # virada de ano: se kick ficou >12h no passado no calendário local, tenta ano seguinte
    if (kick - now).total_seconds() < -12 * 3600:
        try:
            kick = datetime(y + 1, mo, d, h, mi, tzinfo=BRT)
        except ValueError:
            pass
    delta_h = (now - kick).total_seconds() / 3600.0
    if delta_h < 0:
        return "upcoming"
    if delta_h <= 3.0:  # janela típica de jogo + prolongamento
        return "started"
    return "finished"


def main():
    baseline_status, baseline_source = "baseline", "value_pricers"
    if FORCE_LEGACY:
        # rollback de emergência: modelos antigos no board
        cp, sp, fp, xp = CardsPricer(), ShotsPricer(), FoulsPricer(), CornersPricer()
        model_status, model_source = "production", "value_pricers"
        print("[board] ⚠ FORCE_LEGACY_BOARD=1 — usando value_pricers em BOARD.valor")
    else:
        # titular = candidatos promovidos; se o bundle falhar, cai pros antigos (loga alto)
        try:
            from candidate_pricer import (
                CardsPricer as _CP, ShotsPricer as _SP,
                FoulsPricer as _FP, CornersPricer as _XP,
            )
            cp, sp, fp, xp = _CP(), _SP(), _FP(), _XP()
            if not all(getattr(p, "ok", True) for p in (cp, sp, fp, xp)):
                raise RuntimeError("bundle dos candidatos incompleto (algum pricer .ok=False)")
            model_status, model_source = MODEL_STATUS, MODEL_SOURCE
        except Exception as e:
            print(f"[board] ⚠ candidate_pricer indisponível ({type(e).__name__}: {e}) — fallback value_pricers")
            cp, sp, fp, xp = CardsPricer(), ShotsPricer(), FoulsPricer(), CornersPricer()
            model_status, model_source = "production", "value_pricers"
    PRICERS = {"cartoes": cp, "finalizacoes": sp, "faltas": fp, "escanteios": xp}

    # Baseline dual-run: a OUTRA família roda em paralelo pro arquivo de comparação
    # (nunca grava em j["valor"])
    SHADOW_PRICERS = None
    if model_source == "candidate_pricer":
        try:
            SHADOW_PRICERS = {
                "cartoes": CardsPricer(), "finalizacoes": ShotsPricer(),
                "faltas": FoulsPricer(), "escanteios": CornersPricer(),
            }
        except Exception as e:
            print(f"[board] baseline load skip: {type(e).__name__}: {e}")
    else:
        try:
            from candidate_pricer import (
                CardsPricer as _SCP, ShotsPricer as _SSP,
                FoulsPricer as _SFP, CornersPricer as _SXP,
            )
            SHADOW_PRICERS = {
                "cartoes": _SCP(), "finalizacoes": _SSP(),
                "faltas": _SFP(), "escanteios": _SXP(),
            }
            baseline_status, baseline_source = "shadow", "candidate_pricer"
        except Exception as e:
            print(f"[board] baseline load skip: {type(e).__name__}: {e}")

    import value_pricers as _vp
    if getattr(_vp, "_BUNDLE", None) and _vp._BUNDLE.get("name_idx"):
        # NUVEM / bundle: resolver nome→id sem HTML
        ni = _vp._BUNDLE["name_idx"]
        IDX = {m: {lg: {k: int(v) for k, v in d.items()} for lg, d in ni[m].items()} for m in ni}
    else:
        # PC: constrói dos arquivos locais (produção value_pricers tem .by com names)
        cards_idx = {}
        for (lg, _id), t in getattr(cp, "by", {}).items():
            if isinstance(t, dict) and t.get("name"):
                cards_idx.setdefault(lg, {})[norm_team(t["name"])] = int(t.get("id", _id))
            elif isinstance(t, dict):
                # sem name: usa id do par
                pass
        shots_idx = {}
        sh_path = ROOT / "netlify-deploy/Modelo Preditivo de Finalizacoes v3.html"
        if sh_path.exists():
            sh = sh_path.read_text(encoding="utf-8", errors="replace")
            mt = re.search(r"const T_DATA=`([^`]*)`", sh)
            if mt:
                for lnn in mt.group(1).strip().split("\n"):
                    if lnn:
                        p = lnn.split("\t")
                        shots_idx.setdefault(p[0], {})[norm_team(p[2])] = int(p[1])
        fouls_idx = {}; seen = set()
        matches_path = ROOT / "data/unified/matches.json"
        if matches_path.exists():
            for m in json.loads(matches_path.read_text(encoding="utf-8"))["matches"]:
                comp = m.get("competition")
                if comp not in getattr(FoulsPricer, "LIGAS", set()):
                    continue
                for s in ("home", "away"):
                    t = m.get(s) or {}
                    if t.get("id") and t.get("name") and (comp, t["id"]) not in seen:
                        seen.add((comp, t["id"]))
                        fouls_idx.setdefault(comp, {})[norm_team(t["name"])] = t["id"]
        corners_idx = {}
        xh_path = ROOT / "netlify-deploy/Modelo Preditivo de Escanteios v2.html"
        if xh_path.exists():
            xh = xh_path.read_text(encoding="utf-8", errors="replace")
            xmt = re.search(r"const T_DATA=`([^`]*)`", xh)
            if xmt:
                for lnn in xmt.group(1).strip().split("\n"):
                    if lnn:
                        p = lnn.split("\t")
                        corners_idx.setdefault(p[0], {})[norm_team(p[2])] = int(p[1])
        IDX = {"cartoes": cards_idx, "finalizacoes": shots_idx, "faltas": fouls_idx, "escanteios": corners_idx}

    def match(model, lg, name):
        d = IDX[model].get(lg) or {}; key = norm_team(name)
        if key in d: return d[key]
        best, bid = 0, None
        for k, tid in d.items():
            r = ratio(key, k)
            if r > best: best, bid = r, tid
        return bid if best >= FUZZ_MIN else None

    betano, src = load_betano()
    eventos = betano + load_normalized("Superbet", "superbet") \
                     + load_normalized("7k", "7k") \
                     + load_normalized("EstrelaBet", "estrelabet") \
                     + load_normalized("Pinnacle", "pinnacle")
    casas_ativas = sorted(set(e["casa"] for e in eventos))
    # SofaScore = base canônica de nomes/horários; casas encaixam por horário + fuzzy
    sofa_fx = load_sofa_fixtures()
    print(f"fixtures sofa={len(sofa_fx)}")
    n_sofa_hit = n_fuzzy = 0
    # agrupa por jogo: (1) sofa_id se match, (2) fallback fuzzy entre casas
    jogos = []
    by_sofa = {}  # sofa_id -> j
    for e in eventos:
        parts = [p.strip() for p in (e.get("name") or "").split(" - ")]
        dt = parse_start(e.get("start"))
        # day/ini em BRT (parse_start devolve aware ou naive — normaliza)
        if dt is not None:
            if dt.tzinfo is None:
                dt_brt = dt.replace(tzinfo=BRT)
            else:
                dt_brt = dt.astimezone(BRT)
            day = dt_brt.strftime("%Y-%m-%d")
            ini = dt_brt.strftime("%d/%m %H:%M")
        else:
            day, ini = "?", "?"
        hn = norm_team(parts[0]) if len(parts) == 2 else norm_team(e["name"])
        an = norm_team(parts[1]) if len(parts) == 2 else ""

        j = None
        fx, sc, method = (None, 0, None)
        if len(parts) == 2 and sofa_fx:
            fx, sc, method = match_to_sofa(hn, an, day, dt, sofa_fx, book_league=e.get("league") or "")
            if fx is not None:
                j = by_sofa.get(fx["sofa_id"])
                if j is None:
                    j = {
                        "jogo": f"{fx['home']} - {fx['away']}",
                        "liga": fx.get("league") or e.get("league") or "",
                        "inicio": fx.get("inicio") or ini,
                        "home": fx["home"],
                        "away": fx["away"],
                        "sofa_id": fx["sofa_id"],
                        "casas": set(), "mercados": {}, "times": {}, "valor": [],
                        "_stale_casas": set(),
                        "_parts": [fx["home"], fx["away"]],
                        "_league": fx.get("league") or e.get("league") or "",
                        "_hn": fx["_hn"], "_an": fx["_an"],
                        "_day": fx.get("day_brt") or day, "_ini": fx.get("inicio") or ini,
                    }
                    by_sofa[fx["sofa_id"]] = j
                    jogos.append(j)
                n_sofa_hit += 1

        # fallback: fuzzy entre casas (sem sofa)
        if j is None and len(parts) == 2:
            for jj in jogos:
                if jj.get("sofa_id"):  # não misturar órfão com grupo sofa
                    # mas permite se fuzzy fortíssimo no mesmo horário
                    s = _gscore(hn, an, jj["_hn"], jj["_an"])
                    if jj["_day"] == day and ((jj["_ini"] == ini and s >= GROUP_FUZZ_TIME) or s >= 95):
                        j = jj
                        n_sofa_hit += 1  # colou em grupo sofa por fuzzy
                        break
                else:
                    if jj["_day"] != day:
                        continue
                    s = _gscore(hn, an, jj["_hn"], jj["_an"])
                    if (jj["_ini"] == ini and s >= GROUP_FUZZ_TIME) or s >= GROUP_FUZZ_NAME:
                        j = jj
                        n_fuzzy += 1
                        break

        if j is None:
            j = {"jogo": e["name"], "liga": e["league"], "inicio": ini,
                 "home": parts[0].strip() if len(parts) == 2 else "",
                 "away": parts[1].strip() if len(parts) == 2 else "",
                 "casas": set(), "mercados": {}, "times": {}, "valor": [],
                 "_parts": parts, "_league": e["league"],
                 "_hn": hn, "_an": an, "_day": day, "_ini": ini,
                 "_stale_casas": set()}
            jogos.append(j)
            n_fuzzy += 1
        j.setdefault("_stale_casas", set())
        def _sane(linhas):
            raw = [l for l in linhas
                   if isinstance(l.get("linha"), (int, float))
                   and l.get("over") and l.get("under")
                   and 1.01 < float(l["over"]) <= 50 and 1.01 < float(l["under"]) <= 50]
            ok, rej = sanitize_ou_ladder(raw)
            return ok, rej
        for canon, linhas in (e.get("mercados") or {}).items():
            if canon not in MERC_SET: continue
            linhas, rej = _sane(linhas)
            if rej:
                j.setdefault("_ladder_rej", []).extend(
                    {"casa": e["casa"], "mercado": canon, **r} for r in rej)
            if not linhas: continue
            j["mercados"].setdefault(canon, {})[e["casa"]] = linhas
        # totais por time → times[mercado][home|away].casas[casa]
        for canon, by_team in (e.get("mercados_time") or {}).items():
            if canon not in MERC_SET: continue
            slot = j["times"].setdefault(canon, {
                "home": {"nome": j.get("home") or "", "casas": {}},
                "away": {"nome": j.get("away") or "", "casas": {}},
            })
            for tname, linhas in by_team.items():
                side = _assign_side(tname, hn, an)
                if not side: continue
                linhas, _rej = _sane(linhas)
                if not linhas: continue
                if tname and not slot[side]["nome"]:
                    slot[side]["nome"] = tname
                # se já existe de outra fonte, prefere o nome do confronto
                slot[side]["casas"][e["casa"]] = linhas
        if j["mercados"] or j["times"]:
            j["casas"].add(e["casa"])
            if e.get("_stale"):
                j["_stale_casas"].add(e["casa"])

    print(f"match: sofa_hit={n_sofa_hit} · fuzzy/orphan={n_fuzzy} · grupos={len(jogos)}")
    # flag de VALOR (secundário) onde há modelo
    n_valor = 0
    n_shadow = 0
    n_skip_ko = 0
    n_skip_stale = 0
    now_brt = datetime.now(BRT)
    ladder_rej_all = []
    shadow_rows = []  # arquivo paralelo, nunca em BOARD.valor

    for j in jogos:
        parts = j.pop("_parts"); league = j.pop("_league")
        for _k in ("_hn", "_an", "_day", "_ini"): j.pop(_k, None)   # limpa campos internos do dedup
        if j.get("_ladder_rej"):
            ladder_rej_all.extend(j.pop("_ladder_rej"))
        else:
            j.pop("_ladder_rej", None)

        gs = game_state(j.get("inicio"), now_brt)
        j["game_state"] = gs
        stale_set = set(j.get("_stale_casas") or [])
        actionable_game = gs == "upcoming"

        codes = classify_league(league)
        if codes and len(parts) == 2:
            for canon, model in MODELO.items():
                lg = codes.get(model)
                if lg is None or canon not in j["mercados"]: continue
                hid = match(model, lg, parts[0]); aid = match(model, lg, parts[1])
                if not hid or not aid: continue
                for casa, linhas in j["mercados"][canon].items():
                    casa_stale = casa in stale_set
                    for ln_ in linhas:
                        # produção
                        pr = PRICERS[model].price(lg, hid, aid, ln_["linha"])
                        if pr and actionable_game and not casa_stale:
                            dv = de_vig(ln_["over"], ln_["under"])
                            if dv and MARGIN_MIN - 1e-9 <= dv["margin"] <= MARGIN_CAP + 1e-9:
                                pp = float(pr.get("p_push") or 0.0)
                                for side, oddk in (("over", "over"), ("under", "under")):
                                    our_p = float(pr.get("p_" + side + "_win", pr.get("p_" + side, 0.0)))
                                    if our_p < P_LO or our_p > P_HI: continue
                                    edge = edge_vs_market(our_p, dv["p_" + side], pp)
                                    ev = expected_value(our_p, ln_[oddk], pp)
                                    if ev < EV_MIN or edge < EDGE_MIN: continue
                                    fo = fair_odd(our_p, pp)
                                    j["valor"].append({
                                        "mercado": canon, "linha": ln_["linha"],
                                        "lado": "Mais" if side == "over" else "Menos", "casa": casa,
                                        "odd": ln_[oddk],
                                        "nossa_prob": round(our_p * 100, 1),
                                        "p_push": round(pp * 100, 1),
                                        "push_line": bool(is_integer_line(ln_["linha"])),
                                        "edge_pp": round(edge * 100, 1),
                                        "ev_pct": round(ev * 100, 1),
                                        "fair_odd": round(fo, 2) if fo else None,
                                        "mu": round(pr["mu"], 1),
                                        "actionable": True,
                                        "model_status": model_status,
                                        "model_source": model_source,
                                    })
                                    n_valor += 1
                        elif pr and not actionable_game:
                            n_skip_ko += 1
                        elif pr and casa_stale:
                            n_skip_stale += 1

                        # shadow paralelo (nunca em j["valor"] no caminho normal)
                        if SHADOW_PRICERS and model in SHADOW_PRICERS:
                            spr = SHADOW_PRICERS[model].price(lg, hid, aid, ln_["linha"])
                            if not spr or not actionable_game or casa_stale:
                                continue
                            dv = de_vig(ln_["over"], ln_["under"])
                            if not dv or not (MARGIN_MIN - 1e-9 <= dv["margin"] <= MARGIN_CAP + 1e-9):
                                continue
                            spp = float(spr.get("p_push") or 0.0)
                            for side, oddk in (("over", "over"), ("under", "under")):
                                sp_ = float(spr.get("p_" + side + "_win", spr.get("p_" + side, 0.0)))
                                if sp_ < P_LO or sp_ > P_HI: continue
                                sev = expected_value(sp_, ln_[oddk], spp)
                                sedge = edge_vs_market(sp_, dv["p_" + side], spp)
                                if sev < EV_MIN or sedge < EDGE_MIN: continue
                                shadow_rows.append({
                                    "jogo": j.get("jogo"), "inicio": j.get("inicio"),
                                    "mercado": canon, "linha": ln_["linha"],
                                    "lado": "Mais" if side == "over" else "Menos", "casa": casa,
                                    "odd": ln_[oddk], "ev_pct": round(sev * 100, 1),
                                    "nossa_prob": round(sp_ * 100, 1),
                                    "p_push": round(spp * 100, 1),
                                    "mu": round(spr["mu"], 1),
                                    "model_status": baseline_status,
                                    "model_source": baseline_source,
                                })
                                n_shadow += 1
        j["valor"].sort(key=lambda v: -v["ev_pct"])
        j["casas"] = sorted(j["casas"])
        j["n_mercados"] = len(j["mercados"])
        j["tem_valor"] = len(j["valor"]) > 0
        stale = sorted(j.pop("_stale_casas", set()) or [])
        if stale:
            j["stale_casas"] = stale
        # limpa slots de times vazios
        times_clean = {}
        for c, sides in (j.get("times") or {}).items():
            ok = {}
            for s in ("home", "away"):
                sc = sides.get(s) or {}
                if sc.get("casas"):
                    ok[s] = {"nome": sc.get("nome") or (j.get("home") if s == "home" else j.get("away")) or "",
                             "casas": sc["casas"]}
            if ok: times_clean[c] = ok
        j["times"] = times_clean

    lista = sorted([j for j in jogos if j["mercados"] or j.get("times")],
                   key=lambda j: (not j["tem_valor"], j["inicio"]))
    out = {
        "gerado": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
        "casas": casas_ativas,
        "mercados": MERCADOS,
        "fonte": src,
        "jogos": lista,
        "model": {
            "status": model_status,
            "source": model_source,
            "markets": list(MODELO.keys()),
        },
        "pricing": {
            "ev_formula": "p_win*odd + p_push - 1",
            "edge": "p_cond - p_devig",
            "margin_min": MARGIN_MIN,
            "margin_max": MARGIN_CAP,
            "ev_min": EV_MIN,
            "edge_min": EDGE_MIN,
        },
    }
    print(f"[board] valor flags={n_valor} · skip kickoff/started={n_skip_ko} · skip stale casa={n_skip_stale}"
          f" · shadow flags={n_shadow} · ladder rej rows={len(ladder_rej_all)}")
    # transparência da captura (brief P0 §2.4): quem entrou e quem falhou nesta rodada
    _disp = {"betano": "Betano", "superbet": "Superbet", "estrelabet": "EstrelaBet", "7k": "7k", "pinnacle": "Pinnacle"}
    _stdir = ROOT / "data" / "odds" / "_status"
    if _stdir.exists():
        cap = {"casas_ok": [], "casas_fail": [], "casas_stale": []}
        for _c, _nome in _disp.items():
            _f = _stdir / f"{_c}.json"
            if not _f.exists(): continue
            try: _st = json.loads(_f.read_text(encoding="utf-8"))
            except Exception: continue
            if _st.get("ok"): cap["casas_ok"].append(_nome)
            else: cap["casas_fail"].append({"casa": _nome, "error": (_st.get("error") or "?")[:120],
                                            "error_class": _st.get("error_class")})
        # stale-keep: casas presentes no board via full antigo
        _stale_all = set()
        for _j in lista:
            for _sc in (_j.get("stale_casas") or []):
                _stale_all.add(_sc)
        if _stale_all:
            cap["casas_stale"] = sorted(_stale_all)
        # confiabilidade 7 dias (11/07): lê o history.jsonl das rodadas e agrega por casa
        _hf = _stdir / "history.jsonl"
        if _hf.exists():
            from datetime import timedelta as _td
            _cut = (datetime.now(BRT) - _td(days=7)).strftime("%Y-%m-%d %H:%M")
            _agg = {}
            for _ln in _hf.read_text(encoding="utf-8").splitlines():
                try: _r = json.loads(_ln)
                except Exception: continue
                if (_r.get("ts") or "") < _cut: continue
                for _c, _v in (_r.get("casas") or {}).items():
                    a = _agg.setdefault(_c, {"ok": 0, "total": 0})
                    a["total"] += 1; a["ok"] += 1 if _v.get("ok") else 0
            if _agg:
                cap["hist7"] = {_disp.get(c, c): v for c, v in _agg.items()}
        if cap["casas_ok"] or cap["casas_fail"] or cap.get("casas_stale"):
            out["capture"] = cap
    outdir = ROOT / "valor" / "data"; outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "board.js").write_text("window.BOARD=" + json.dumps(out, ensure_ascii=False) + ";", encoding="utf-8")

    # dual-run paralelo (baseline/shadow) — nunca lido pela UI de valor
    sh_dir = ROOT / "data" / "odds" / "_status"
    sh_dir.mkdir(parents=True, exist_ok=True)
    if shadow_rows:
        shadow_out = {
            "gerado": out["gerado"],
            "model_status": baseline_status,
            "model_source": baseline_source,
            "n": len(shadow_rows),
            "flags": shadow_rows,
        }
        (sh_dir / "shadow_valor.json").write_text(
            json.dumps(shadow_out, ensure_ascii=False), encoding="utf-8")
        print(f"[board] shadow_valor.json · {len(shadow_rows)} flags (fora de BOARD.valor)")
    if ladder_rej_all:
        (sh_dir / "ladder_rejects.json").write_text(
            json.dumps({"gerado": out["gerado"], "n": len(ladder_rej_all),
                        "rejects": ladder_rej_all[:5000]}, ensure_ascii=False),
            encoding="utf-8")

    print(f"casas={casas_ativas} · jogos com mercado={len(lista)} · com valor={sum(1 for j in lista if j['tem_valor'])} · flags de valor={n_valor} · model={model_status}/{model_source}")
    for j in lista[:8]:
        v = f" · VALOR: {j['valor'][0]['mercado']} {j['valor'][0]['lado']} {j['valor'][0]['linha']} EV{j['valor'][0]['ev_pct']:+.0f}%" if j["tem_valor"] else ""
        print(f"  {j['inicio']} · {j['jogo']} · {j['n_mercados']} mercados {list(j['mercados'])}{v}")


if __name__ == "__main__":
    main()
