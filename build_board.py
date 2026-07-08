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
from value_pricers import CardsPricer, ShotsPricer, FoulsPricer

BRT = timezone(timedelta(hours=-3))
# mercados do board (ordem de exibição) + qual tem modelo de valor
# Diego (08/07): mostrar SÓ estes 6; escanteios/chutes-no-gol/desarmes não vão pro site
MERCADOS = ["Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta"]
MERC_SET = set(MERCADOS)
MODELO = {"Cartões": "cartoes", "Faltas": "faltas", "Finalizações": "finalizacoes"}
# limiares do flag de valor (secundário)
EV_MIN, EDGE_MIN, MARGIN_CAP, P_LO, P_HI = 0.05, 0.04, 0.12, 0.08, 0.92
FUZZ_MIN = 88

# ---- normalização de nome de time / liga (igual ao build_value_bets) ----
STOP = {"fc", "cf", "ec", "sc", "ca", "ac", "afc", "club", "clube", "futebol"}
STATE = re.compile(r"[- ]?(pr|sp|rj|mg|rs|go|ce|pe|ba|mt|ms|pa|to|al|se|rn|pb|pi|ap|ac|ro|rr|df)$")
def norm_team(name):
    s = unidecode((name or "").lower()).strip()
    s = STATE.sub("", s); s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in STOP]
    return " ".join(toks) or s.strip()
def _n(s): return unidecode((s or "").lower())
LEAGUE_RULES = [
    (lambda l: "brasileir" in l and ("serie b" in l or "série b" in l or "- b" in l), ("B", "BR-B", None)),
    (lambda l: "brasileir" in l and "serie b" not in l, ("A", "BR-A", "BR")),
    (lambda l: "premier league" in l or ("premier" in l and "ingl" in l), ("PL", "PL", "PL")),
    (lambda l: "laliga" in l or "la liga" in l or ("primera" in l and "espan" in l), ("LL", "LL", "LL")),
    (lambda l: "serie a" in l and ("ital" in l or "itali" in l), ("SA", "SA", "SA")),
    (lambda l: "bundesliga" in l and "2" not in l, ("BU", "BU", "BU")),
    (lambda l: "ligue 1" in l, ("L1", "L1", "L1")),
]
def classify_league(lg):
    l = _n(lg)
    for pred, c in LEAGUE_RULES:
        try:
            if pred(l): return {"cartoes": c[0], "faltas": c[1], "finalizacoes": c[2]}
        except Exception: pass
    return None

# Betano: nome do mercado cru -> mercado canônico do board (só jogo inteiro)
BETANO_MK = {
    "Total de Cartões": "Cartões", "Total de Faltas": "Faltas", "Total de chutes": "Finalizações",
    "Chutes no gol": "Chutes no gol", "Total de Impedimentos": "Impedimentos",
    "Total de laterais": "Laterais", "Total de tiros de meta": "Tiros de meta",
}

def load_betano():
    """-> lista de eventos normalizados {casa, name, league, start, captured, mercados:{canon:[{linha,over,under}]}}"""
    ptr = ROOT / "data/odds/betano_latest.json"
    src = None
    if ptr.exists():
        fn = json.loads(ptr.read_text(encoding="utf-8")).get("file")
        if fn: src = ROOT / "data/odds" / fn
    if not src or not src.exists():
        cs = sorted((ROOT / "data/odds").glob("betano_*.jsonl")); src = cs[-1] if cs else None
    if not src: return [], None
    out = []
    for ln in src.read_text(encoding="utf-8").strip().split("\n"):
        if not ln.strip(): continue
        e = json.loads(ln)
        mk = {}
        for aba in ("cartoes", "estatisticas", "principais_ou"):
            for m in (e.get("markets", {}).get(aba) or []):
                canon = BETANO_MK.get(m.get("market"))
                if not canon: continue
                L = m.get("line")
                lst = mk.setdefault(canon, {})
                if L not in lst and m.get("over") and m.get("under"):
                    lst[L] = {"linha": L, "over": round(m["over"], 2), "under": round(m["under"], 2)}
        mk = {c: sorted(v.values(), key=lambda x: x["linha"]) for c, v in mk.items() if v}
        if mk:
            out.append({"casa": "Betano", "name": e.get("name"), "league": e.get("league"),
                        "start": e.get("start"), "captured": e.get("captured_at"), "mercados": mk})
    return out, src.name


def load_normalized(book, latest_name):
    """lê um JSONL já-normalizado {casa,name,league,start,mercados:{canon:[{linha,over,under}]}}
    (Superbet, 7k, EstrelaBet). -> mesma forma que load_betano."""
    ptr = ROOT / "data/odds" / latest_name
    if not ptr.exists(): return []
    fn = json.loads(ptr.read_text(encoding="utf-8")).get("file")
    src = ROOT / "data/odds" / fn if fn else None
    if not src or not src.exists(): return []
    out = []
    for ln in src.read_text(encoding="utf-8").strip().split("\n"):
        if not ln.strip(): continue
        e = json.loads(ln)
        if e.get("mercados"):
            out.append({"casa": e.get("casa", book), "name": e.get("name"), "league": e.get("league"),
                        "start": e.get("start"), "captured": e.get("captured_at"), "mercados": e["mercados"]})
    return out


def parse_start(s):
    """aceita ms numérico (Betano/Superbet/7k) OU string ISO (EstrelaBet) -> datetime BRT."""
    if s is None: return None
    try:
        if isinstance(s, (int, float)) or (isinstance(s, str) and s.isdigit()):
            return datetime.fromtimestamp(int(s) / 1000, tz=BRT)
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None


def de_vig(over, under):
    if not over or not under or over <= 1 or under <= 1: return None
    po, pu = 1 / over, 1 / under; tot = po + pu
    return {"p_over": po / tot, "p_under": pu / tot, "margin": tot - 1}


def main():
    cp, sp, fp = CardsPricer(), ShotsPricer(), FoulsPricer()
    PRICERS = {"cartoes": cp, "finalizacoes": sp, "faltas": fp}
    import value_pricers as _vp
    if getattr(_vp, "_BUNDLE", None) and _vp._BUNDLE.get("name_idx"):
        # NUVEM: resolver de nome→id vem do bundle (sem HTML nem matches.json)
        ni = _vp._BUNDLE["name_idx"]
        IDX = {m: {lg: {k: int(v) for k, v in d.items()} for lg, d in ni[m].items()} for m in ni}
    else:
        # PC: constrói dos arquivos locais
        cards_idx = {}
        for (lg, _id), t in cp.by.items():
            cards_idx.setdefault(lg, {})[norm_team(t["name"])] = int(t["id"])
        sh = (ROOT / "netlify-deploy/Modelo Preditivo de Finalizacoes v3.html").read_text(encoding="utf-8", errors="replace")
        mt = re.search(r"const T_DATA=`([^`]*)`", sh)
        shots_idx = {}
        for lnn in mt.group(1).strip().split("\n"):
            if lnn:
                p = lnn.split("\t"); shots_idx.setdefault(p[0], {})[norm_team(p[2])] = int(p[1])
        fouls_idx = {}; seen = set()
        for m in json.loads((ROOT / "data/unified/matches.json").read_text(encoding="utf-8"))["matches"]:
            comp = m.get("competition")
            if comp not in FoulsPricer.LIGAS: continue
            for s in ("home", "away"):
                t = m.get(s) or {}
                if t.get("id") and t.get("name") and (comp, t["id"]) not in seen:
                    seen.add((comp, t["id"])); fouls_idx.setdefault(comp, {})[norm_team(t["name"])] = t["id"]
        IDX = {"cartoes": cards_idx, "finalizacoes": shots_idx, "faltas": fouls_idx}

    def match(model, lg, name):
        d = IDX[model].get(lg) or {}; key = norm_team(name)
        if key in d: return d[key]
        best, bid = 0, None
        for k, tid in d.items():
            r = ratio(key, k)
            if r > best: best, bid = r, tid
        return bid if best >= FUZZ_MIN else None

    betano, src = load_betano()
    eventos = betano + load_normalized("Superbet", "superbet_latest.json") \
                     + load_normalized("7k", "7k_latest.json") \
                     + load_normalized("EstrelaBet", "estrelabet_latest.json")
    casas_ativas = sorted(set(e["casa"] for e in eventos))
    # agrupa por jogo (nome normalizado + dia) — multi-casa
    jogos = {}
    for e in eventos:
        parts = [p.strip() for p in (e.get("name") or "").split(" - ")]
        dt = parse_start(e.get("start"))
        day = dt.strftime("%Y-%m-%d") if dt else "?"
        gkey = (norm_team(parts[0]) if len(parts) == 2 else e["name"], norm_team(parts[1]) if len(parts) == 2 else "", day)
        j = jogos.get(gkey)
        if not j:
            j = {"jogo": e["name"], "liga": e["league"],
                 "inicio": dt.strftime("%d/%m %H:%M") if dt else "?",
                 "casas": set(), "mercados": {}, "valor": [], "_parts": parts, "_league": e["league"]}
            jogos[gkey] = j
        for canon, linhas in e["mercados"].items():
            if canon not in MERC_SET: continue          # só os 6 mercados de interesse
            j["mercados"].setdefault(canon, {})[e["casa"]] = linhas
        if j["mercados"]: j["casas"].add(e["casa"])

    # flag de VALOR (secundário) onde há modelo
    n_valor = 0
    for j in jogos.values():
        parts = j.pop("_parts"); league = j.pop("_league")
        codes = classify_league(league)
        if codes and len(parts) == 2:
            for canon, model in MODELO.items():
                lg = codes.get(model)
                if lg is None or canon not in j["mercados"]: continue
                hid = match(model, lg, parts[0]); aid = match(model, lg, parts[1])
                if not hid or not aid: continue
                for casa, linhas in j["mercados"][canon].items():
                    for ln_ in linhas:
                        pr = PRICERS[model].price(lg, hid, aid, ln_["linha"])
                        if not pr: continue
                        dv = de_vig(ln_["over"], ln_["under"])
                        if not dv or dv["margin"] > MARGIN_CAP: continue
                        for side, oddk in (("over", "over"), ("under", "under")):
                            our_p = pr["p_" + side]
                            if our_p < P_LO or our_p > P_HI: continue
                            edge = our_p - dv["p_" + side]; ev = our_p * ln_[oddk] - 1
                            if ev < EV_MIN or edge < EDGE_MIN: continue
                            j["valor"].append({"mercado": canon, "linha": ln_["linha"],
                                               "lado": "Mais" if side == "over" else "Menos", "casa": casa,
                                               "odd": ln_[oddk], "nossa_prob": round(our_p * 100, 1),
                                               "edge_pp": round(edge * 100, 1), "ev_pct": round(ev * 100, 1),
                                               "mu": round(pr["mu"], 1)})
                            n_valor += 1
        j["valor"].sort(key=lambda v: -v["ev_pct"])
        j["casas"] = sorted(j["casas"])
        j["n_mercados"] = len(j["mercados"])
        j["tem_valor"] = len(j["valor"]) > 0

    lista = sorted([j for j in jogos.values() if j["mercados"]], key=lambda j: (not j["tem_valor"], j["inicio"]))
    out = {"gerado": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"), "casas": casas_ativas,
           "mercados": MERCADOS, "fonte": src, "jogos": lista}
    outdir = ROOT / "valor" / "data"; outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "board.js").write_text("window.BOARD=" + json.dumps(out, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"casas={casas_ativas} · jogos com mercado={len(lista)} · com valor={sum(1 for j in lista if j['tem_valor'])} · flags de valor={n_valor}")
    for j in lista[:8]:
        v = f" · VALOR: {j['valor'][0]['mercado']} {j['valor'][0]['lado']} {j['valor'][0]['linha']} EV{j['valor'][0]['ev_pct']:+.0f}%" if j["tem_valor"] else ""
        print(f"  {j['inicio']} · {j['jogo']} · {j['n_mercados']} mercados {list(j['mercados'])}{v}")


if __name__ == "__main__":
    main()
