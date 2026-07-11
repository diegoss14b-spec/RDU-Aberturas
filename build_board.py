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
from value_pricers import CardsPricer, ShotsPricer, FoulsPricer, CornersPricer

BRT = timezone(timedelta(hours=-3))
# mercados do board (ordem de exibição) + qual tem modelo de valor
# 12/07: Escanteios entra (Diego pediu no comparador de valor — modelo v2 de 11 ligas)
MERCADOS = ["Cartões", "Faltas", "Finalizações", "Escanteios", "Impedimentos", "Laterais", "Tiros de meta"]
MERC_SET = set(MERCADOS)
MODELO = {"Cartões": "cartoes", "Faltas": "faltas", "Finalizações": "finalizacoes", "Escanteios": "escanteios"}
# limiares do flag de valor (secundário)
EV_MIN, EDGE_MIN, MARGIN_CAP, P_LO, P_HI = 0.05, 0.04, 0.12, 0.15, 0.85  # P∈[15,85]% = região calibrada (evita artefato longe do μ)
FUZZ_MIN = 88
# dedup de confronto entre casas (mesmo jogo grafado diferente por cada casa)
GROUP_FUZZ_TIME = 75   # mesmo horário exato + semelhança de nomes ≥ isto → mesmo confronto
GROUP_FUZZ_NAME = 88   # mesmo dia + semelhança de nomes ≥ isto → mesmo confronto (horário pode divergir)
def _gscore(ah, aa, bh, ba):
    # semelhança do confronto; aceita ordem trocada (mercados totais são simétricos)
    return max(min(ratio(ah, bh), ratio(aa, ba)), min(ratio(ah, ba), ratio(aa, bh)))

# ---- normalização de nome de time / liga (igual ao build_value_bets) ----
STOP = {"fc", "cf", "ec", "sc", "ca", "ac", "afc", "club", "clube", "futebol"}
STATE = re.compile(r"[- ]?(pr|sp|rj|mg|rs|go|ce|pe|ba|mt|ms|pa|to|al|se|rn|pb|pi|ap|ac|ro|rr|df)$")
# apelidos → forma canônica (as casas grafam o mesmo time de jeitos diferentes; evita jogo duplicado)
ALIASES = {
    "sport": "sport recife",
    "bragantino": "red bull bragantino", "rb bragantino": "red bull bragantino",
    "vasco": "vasco da gama", "athletico": "athletico paranaense",
    "gremio novorizontino": "novorizontino", "operario": "operario ferroviario",
}
def norm_team(name):
    s = unidecode((name or "").lower()).strip()
    s = STATE.sub("", s); s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in STOP]
    key = " ".join(toks) or s.strip()
    return ALIASES.get(key, key)
def _n(s): return unidecode((s or "").lower())
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
        for aba in ("cartoes", "estatisticas", "principais_ou", "escanteios"):
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
    cp, sp, fp, xp = CardsPricer(), ShotsPricer(), FoulsPricer(), CornersPricer()
    PRICERS = {"cartoes": cp, "finalizacoes": sp, "faltas": fp, "escanteios": xp}
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
        xh = (ROOT / "netlify-deploy/Modelo Preditivo de Escanteios v2.html").read_text(encoding="utf-8", errors="replace")
        xmt = re.search(r"const T_DATA=`([^`]*)`", xh)
        corners_idx = {}
        for lnn in xmt.group(1).strip().split("\n"):
            if lnn:
                p = lnn.split("\t"); corners_idx.setdefault(p[0], {})[norm_team(p[2])] = int(p[1])
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
    eventos = betano + load_normalized("Superbet", "superbet_latest.json") \
                     + load_normalized("7k", "7k_latest.json") \
                     + load_normalized("EstrelaBet", "estrelabet_latest.json")
    casas_ativas = sorted(set(e["casa"] for e in eventos))
    # agrupa por jogo entre casas — dedup FUZZY (cada casa grafa o mesmo confronto diferente):
    # (A) mesmo horário exato + nomes ≥ GROUP_FUZZ_TIME, ou (B) mesmo dia + nomes ≥ GROUP_FUZZ_NAME
    jogos = []
    for e in eventos:
        parts = [p.strip() for p in (e.get("name") or "").split(" - ")]
        dt = parse_start(e.get("start"))
        day = dt.strftime("%Y-%m-%d") if dt else "?"
        ini = dt.strftime("%d/%m %H:%M") if dt else "?"
        hn = norm_team(parts[0]) if len(parts) == 2 else norm_team(e["name"])
        an = norm_team(parts[1]) if len(parts) == 2 else ""
        j = None
        if len(parts) == 2:
            for jj in jogos:
                if jj["_day"] != day: continue
                s = _gscore(hn, an, jj["_hn"], jj["_an"])
                if (jj["_ini"] == ini and s >= GROUP_FUZZ_TIME) or s >= GROUP_FUZZ_NAME:
                    j = jj; break
        if j is None:
            j = {"jogo": e["name"], "liga": e["league"], "inicio": ini,
                 "casas": set(), "mercados": {}, "valor": [], "_parts": parts, "_league": e["league"],
                 "_hn": hn, "_an": an, "_day": day, "_ini": ini}
            jogos.append(j)
        for canon, linhas in e["mercados"].items():
            if canon not in MERC_SET: continue          # só os 6 mercados de interesse
            linhas = [l for l in linhas                 # sanity: odds/linha válidas (brief P0 §2.7)
                      if isinstance(l.get("linha"), (int, float))
                      and l.get("over") and l.get("under")
                      and 1.01 < l["over"] <= 50 and 1.01 < l["under"] <= 50]
            if not linhas: continue
            j["mercados"].setdefault(canon, {})[e["casa"]] = linhas
        if j["mercados"]: j["casas"].add(e["casa"])

    # flag de VALOR (secundário) onde há modelo
    n_valor = 0
    for j in jogos:
        parts = j.pop("_parts"); league = j.pop("_league")
        for _k in ("_hn", "_an", "_day", "_ini"): j.pop(_k, None)   # limpa campos internos do dedup
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

    lista = sorted([j for j in jogos if j["mercados"]], key=lambda j: (not j["tem_valor"], j["inicio"]))
    out = {"gerado": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"), "casas": casas_ativas,
           "mercados": MERCADOS, "fonte": src, "jogos": lista}
    # transparência da captura (brief P0 §2.4): quem entrou e quem falhou nesta rodada
    _disp = {"betano": "Betano", "superbet": "Superbet", "estrelabet": "EstrelaBet", "7k": "7k"}
    _stdir = ROOT / "data" / "odds" / "_status"
    if _stdir.exists():
        cap = {"casas_ok": [], "casas_fail": []}
        for _c, _nome in _disp.items():
            _f = _stdir / f"{_c}.json"
            if not _f.exists(): continue
            try: _st = json.loads(_f.read_text(encoding="utf-8"))
            except Exception: continue
            if _st.get("ok"): cap["casas_ok"].append(_nome)
            else: cap["casas_fail"].append({"casa": _nome, "error": (_st.get("error") or "?")[:120]})
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
        if cap["casas_ok"] or cap["casas_fail"]:
            out["capture"] = cap
    outdir = ROOT / "valor" / "data"; outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "board.js").write_text("window.BOARD=" + json.dumps(out, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"casas={casas_ativas} · jogos com mercado={len(lista)} · com valor={sum(1 for j in lista if j['tem_valor'])} · flags de valor={n_valor}")
    for j in lista[:8]:
        v = f" · VALOR: {j['valor'][0]['mercado']} {j['valor'][0]['lado']} {j['valor'][0]['linha']} EV{j['valor'][0]['ev_pct']:+.0f}%" if j["tem_valor"] else ""
        print(f"  {j['inicio']} · {j['jogo']} · {j['n_mercados']} mercados {list(j['mercados'])}{v}")


if __name__ == "__main__":
    main()
