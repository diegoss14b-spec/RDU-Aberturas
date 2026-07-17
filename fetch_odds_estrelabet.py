# -*- coding: utf-8 -*-
"""fetch_odds_estrelabet.py — captura odds da EstrelaBet (plataforma ALTENAR) dos mercados
de estatística de JOGO INTEIRO, pra a Mesa de Aberturas. JSON limpo, sem auth.
  lista : GetEvents?sportId=66&hoursRange=N       -> eventos (id,name,startDate,champId) + champs
  detalhe: GetEventDetails?eventId=<id>           -> markets/childMarkets + odds[] (name,line,price)
Mercado limpo de jogo inteiro (ex 'Total cartões','Total de Faltas','Total de Impedimentos');
odd name 'Mais de X'/'Menos de X'. EXCLUI jogador/técnico/tempo/handicap/exatos/time.
Saída: data/odds/estrelabet_{stamp}.jsonl + estrelabet_latest.json (formato normalizado do board)."""
import sys, os, json, re, time, random
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
try:
    import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
except Exception: pass
import requests

ROOT = Path(__file__).resolve().parent
# 11/07: a Altenar passou a rate-limitar IP de datacenter (nuvem capturava ~5 detalhes e era
# cortada; do IP residencial BR funciona 100%). Fix = proxy BR na nuvem, igual betano/7k.
sys.path.insert(0, str(ROOT))
try:
    from capture_common import br_proxies, odds_window, in_window
    PROX = br_proxies()          # nuvem: Decodo BR via env; local: None (direto)
except Exception:
    PROX = None
    def odds_window(): return None       # sem capture_common: modo close desliga, janela cheia
    def in_window(_s, _w): return True
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
BASE = "https://sb2frontend-altenar2.biahosted.com/api/widget"
PARAMS = "culture=pt-BR&timezoneOffset=180&integration=estrelabet&deviceType=2&numFormat=en-GB&countryCode=BR"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0", "Accept": "application/json",
     "Origin": "https://www.estrelabet.bet.br", "Referer": "https://www.estrelabet.bet.br/"}
HOURS = 96
MAX_EVENTS = 200
WORKERS = 8       # detalhes em paralelo — proxy BR é ~3x mais lento/req (sem rate-limit); 200 seq estouram o timeout na nuvem
MIN_EVENTS = 8    # mínimo pro finish() (abaixo = exit 2)
MIN_EFF = MIN_EVENTS  # modo close (ODDS_WINDOW_H) reduz — ver main()

# Mecanismo Altenar/EstrelaBet (validado França vs Espanha 14/07):
#   JOGO : "Total cartões" | "Total de Faltas" | "Totais chutes" | "Totais chutes a Gol"
#          | "Total de Impedimentos" | "Total de Escanteios" | "Total de desarmes"
#   TIME A: "{Time} total cartões" | "{Time} Total de faltas" | "{Time} total de escanteios"
#           | "{Time} total de impedimentos" | "{Time} Total Desarmes"
#   TIME B: "Total de chutes {Time}" | "Total de chutes a Gol {Time}"
# (América-MG Série B só abre cartões/escanteios; jogos big-offer abrem o pacote completo.)
_EXCL_PART = ("jogador", "técnico", "tecnico", "substituto", "cometidas",
              "1º tempo", "2º tempo", "1° tempo", "1ª tempo", "2ª tempo",
              "handicap", "ímpar", "impar", "/par", "exatos", "exato", "vermelho",
              "primeira", "primeiro", "última", "ultimo", "último", "ambas", "corrida",
              "escala", "1x2", "chance", "inclui", "substituto", "escalação")
# nomes de partida (lower). "totais chutes" = forma plural da Estrela no pacote stats.
_MATCH = {
    "total cartões": "Cartões", "total de cartões": "Cartões",
    "total de faltas": "Faltas", "total faltas": "Faltas",
    "total de finalizações": "Finalizações", "total de chutes": "Finalizações",
    "total chutes": "Finalizações", "totais chutes": "Finalizações",
    "total de remates": "Finalizações",
    "total de chutes no gol": "Chutes no gol", "total de chutes a gol": "Chutes no gol",
    "totais chutes a gol": "Chutes no gol", "totais chutes no gol": "Chutes no gol",
    "total de impedimentos": "Impedimentos",
    "total de laterais": "Laterais", "total de arremessos laterais": "Laterais",
    "total de tiros de meta": "Tiros de meta",
    "total de escanteios": "Escanteios", "total escanteios": "Escanteios",
    "total de desarmes": "Desarmes", "total desarmes": "Desarmes",
}
_STAT_TOKENS = (
    r"cart[oõ]es|faltas|finaliza[cç][oõ]es|"
    r"chutes\s+a\s+gol|chutes\s+no\s+gol|chutes|remates|"
    r"impedimentos|laterais|arremessos laterais|tiros de meta|escanteios|desarmes"
)
# A) "{Time} total [de] {stat}"
_TEAM_A = re.compile(
    rf"^(.+?)\s+total\s+(?:de\s+)?({_STAT_TOKENS})$", re.I,
)
# B) "Total [de] {stat} {Time}"  (chutes na Estrela: "Total de chutes França")
_TEAM_B = re.compile(
    rf"^total\s+(?:de\s+)?({_STAT_TOKENS})\s+(.+)$", re.I,
)
_STAT_MAP = {
    "cartões": "Cartões", "cartoes": "Cartões", "faltas": "Faltas",
    "finalizações": "Finalizações", "finalizacoes": "Finalizações",
    "chutes": "Finalizações", "remates": "Finalizações",
    "chutes no gol": "Chutes no gol", "chutes a gol": "Chutes no gol",
    "impedimentos": "Impedimentos",
    "laterais": "Laterais", "arremessos laterais": "Laterais",
    "tiros de meta": "Tiros de meta", "escanteios": "Escanteios",
    "desarmes": "Desarmes",
}

def _norm_stat(stat):
    s = (stat or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    if s in _STAT_MAP: return _STAT_MAP[s]
    su = (s.replace("ç", "c").replace("õ", "o").replace("á", "a")
           .replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u"))
    for k, v in _STAT_MAP.items():
        ku = (k.replace("ç", "c").replace("õ", "o").replace("á", "a")
               .replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u"))
        if su == ku: return v
    return None

def canon(nm):
    """Total de JOGO INTEIRO (nome sem time)."""
    if not nm: return None
    l = nm.strip().lower()
    if any(b in l for b in _EXCL_PART): return None
    if " - " in l: return None
    return _MATCH.get(l)

def canon_team(nm):
    """Time → (canon, nome_time). Aceita A '{Time} total de faltas' e B 'Total de chutes {Time}'."""
    if not nm: return None
    m = nm.strip()
    ml = m.lower()
    if any(b in ml for b in _EXCL_PART): return None
    if ml in _MATCH: return None
    mo = _TEAM_A.match(m)
    if mo:
        team, stat = mo.group(1).strip(), mo.group(2)
        c = _norm_stat(stat)
        if c and team: return c, team
    mo = _TEAM_B.match(m)
    if mo:
        stat, team = mo.group(1), mo.group(2).strip()
        c = _norm_stat(stat)
        # evita "Total de chutes a Gol" sem time (já é match)
        if c and team and len(team) >= 2: return c, team
    return None

OUTC = re.compile(r"(mais|menos|acima|abaixo|over|under)\s*(?:de)?\s*([\d.]+)", re.I)

def get(url):
    for a in range(3):
        try:
            r = requests.get(url, headers=H, timeout=30, proxies=PROX)
            if r.status_code == 200 and r.text[:1] in "[{": return r.json()
            if r.status_code in (403, 429):   # rate-limit: espera progressiva antes de re-tentar
                time.sleep(3.0 * (a + 1)); continue
        except Exception: pass
        time.sleep(1.2)
    return None

def flat_ids(v):
    out = []
    for x in (v or []):
        if isinstance(x, list): out += flat_ids(x)
        elif x is not None: out.append(x)
    return out

def main():
    now = datetime.now(BRT)
    lst = get(f"{BASE}/GetEvents?{PARAMS}&sportId=66&hoursRange={HOURS}&categoryId=0&championshipIds=0")
    events = (lst or {}).get("events") or []
    _wh = odds_window()
    if _wh is not None:   # modo close: filtra ANTES do sort/cap (senão o top-200 por mercados descarta jogos da janela)
        global MIN_EFF
        _tot = len(events)
        events = [e for e in events if in_window(e.get("startDate"), _wh)]
        MIN_EFF = (min(MIN_EVENTS, 1) if events else 0)   # janela curta: 1+ ok; lista vazia não é falha
        print(f"[estrelabet] modo close: janela {_wh:g}h -> {len(events)} de {_tot} eventos")
    champs = {c["id"]: c.get("name", "") for c in ((lst or {}).get("champs") or [])}
    # ordenar por nº de mercados (os com stats têm muitos) e capar
    def nmk(e): return len(flat_ids(e.get("desktopMarketIds") or e.get("marketIds") or e.get("markets") or []))
    events.sort(key=nmk, reverse=True)
    events = events[:MAX_EVENTS]
    print(f"[estrelabet] GetEvents: {len(events)} eventos (top por nº de mercados, janela {HOURS}h)")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"estrelabet_{stamp}.jsonl"
    from capture_common import write_odds_latest
    def write_latest(n, promote=False):
        write_odds_latest("estrelabet", out_path.name, n,
                          at=now.isoformat(timespec="seconds"), promote_full=promote, min_events=MIN_EFF)

    # Detalhes em PARALELO. O proxy BR (nuvem) é ~3x mais lento por request mas NÃO rate-limita
    # (todos 200 mesmo sequencial) → 200 fetches em série estouravam o timeout e davam exit=2.
    # 8 workers derrubam ~10min→~1,5min. Local (direto) fica ~30s.
    from concurrent.futures import ThreadPoolExecutor
    ids = [e.get("id") for e in events if e.get("id")]
    details = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for eid, d in ex.map(lambda i: (i, get(f"{BASE}/GetEventDetails?{PARAMS}&eventId={i}")), ids):
            if d:
                details[eid] = d
    n_det = len(ids)

    f = open(out_path, "w", encoding="utf-8")
    n_out = 0
    for e in events:
        eid = e.get("id")
        d = details.get(eid)
        if not d: continue
        allm = (d.get("markets") or []) + (d.get("childMarkets") or [])
        odds = {o["id"]: o for o in (d.get("odds") or [])}
        merc, merc_t = {}, {}
        for m in allm:
            mname = m.get("name")
            c = canon(mname)
            ct = None if c else canon_team(mname)
            if not c and not ct: continue
            lines = {}
            for oid in flat_ids(m.get("desktopOddIds") or m.get("mobileOddIds")):
                o = odds.get(oid)
                if not o: continue
                mo = OUTC.search(o.get("name") or "")
                price = o.get("price")
                if not mo or not price or price <= 1: continue
                side = "over" if mo.group(1).lower() in ("mais", "acima", "over") else "under"
                try: L = float(o.get("line") or mo.group(2))
                except Exception: continue
                lines.setdefault(L, {})[side] = round(float(price), 2)
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if not arr: continue
            if c:
                if len(arr) > len(merc.get(c, [])): merc[c] = arr
            else:
                c2, team = ct
                prev = {x["linha"]: x for x in merc_t.get(c2, {}).get(team, [])}
                for row in arr: prev[row["linha"]] = row
                merc_t.setdefault(c2, {})[team] = [prev[L] for L in sorted(prev)]
        merc = {k: v for k, v in merc.items() if v}
        if not merc and not merc_t: continue
        name = (d.get("name") or e.get("name") or "").replace(" vs. ", " - ").replace(" vs ", " - ")
        league = champs.get(e.get("champId")) or (d.get("champ") or {}).get("name", "")
        rec = {"casa": "EstrelaBet", "event_id": eid, "name": name, "league": league,
               "start": e.get("startDate") or d.get("startDate"),
               "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
        if merc_t: rec["mercados_time"] = merc_t
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
    f.close(); write_latest(n_out, promote=None)
    print(f"[estrelabet] {n_det} detalhes · {n_out} jogos com mercado de estatística salvos em {out_path.name}")
    return n_out

if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish
    try:
        _n = main() or 0
        sys.exit(finish("estrelabet", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("estrelabet", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
