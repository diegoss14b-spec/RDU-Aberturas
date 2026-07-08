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
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
BASE = "https://sb2frontend-altenar2.biahosted.com/api/widget"
PARAMS = "culture=pt-BR&timezoneOffset=180&integration=estrelabet&deviceType=2&numFormat=en-GB&countryCode=BR"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0", "Accept": "application/json",
     "Origin": "https://www.estrelabet.bet.br", "Referer": "https://www.estrelabet.bet.br/"}
HOURS = 96
MAX_EVENTS = 200

EXCL = ("jogador", "técnico", "tecnico", "substituto", "cometidas", " - ", "1º tempo", "2º tempo",
        "1° tempo", "handicap", "ímpar", "impar", "/par", "a gol", "exatos", "exato", "vermelho",
        "primeira", "primeiro", "última", "ultimo", "último", "ambas", "corrida", "escanteio", "ao gol")
def canon(nm):
    l = (nm or "").lower()
    if any(b in l for b in EXCL): return None
    if "cart" in l and "total" in l: return "Cartões"
    if "falta" in l and "total" in l: return "Faltas"
    if ("chute" in l or "finaliza" in l or "remate" in l) and "total" in l: return "Finalizações"
    if "impedi" in l and "total" in l: return "Impedimentos"
    if ("lateral" in l or "arremesso" in l) and "total" in l: return "Laterais"
    if "tiro de meta" in l and "total" in l: return "Tiros de meta"
    return None

OUTC = re.compile(r"(mais|menos|acima|abaixo|over|under)\s*(?:de)?\s*([\d.]+)", re.I)

def get(url):
    for _ in range(3):
        try:
            r = requests.get(url, headers=H, timeout=30)
            if r.status_code == 200 and r.text[:1] in "[{": return r.json()
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
    champs = {c["id"]: c.get("name", "") for c in ((lst or {}).get("champs") or [])}
    # ordenar por nº de mercados (os com stats têm muitos) e capar
    def nmk(e): return len(flat_ids(e.get("desktopMarketIds") or e.get("marketIds") or e.get("markets") or []))
    events.sort(key=nmk, reverse=True)
    events = events[:MAX_EVENTS]
    print(f"[estrelabet] GetEvents: {len(events)} eventos (top por nº de mercados, janela {HOURS}h)")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"estrelabet_{stamp}.jsonl"
    latest = OUTDIR / "estrelabet_latest.json"
    def write_latest(n): latest.write_text(json.dumps({"file": out_path.name, "n": n, "at": now.isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
    write_latest(0)
    f = open(out_path, "w", encoding="utf-8")
    n_out = n_det = 0
    for e in events:
        eid = e.get("id")
        if not eid: continue
        d = get(f"{BASE}/GetEventDetails?{PARAMS}&eventId={eid}")
        n_det += 1
        time.sleep(random.uniform(0.2, 0.4))
        if not d: continue
        allm = (d.get("markets") or []) + (d.get("childMarkets") or [])
        odds = {o["id"]: o for o in (d.get("odds") or [])}
        merc = {}
        for m in allm:
            c = canon(m.get("name"))
            if not c: continue
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
            arr = [{"linha": L, "over": v["over"], "under": v["under"]} for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if arr: merc.setdefault(c, [])  # garante chave
            if arr:
                # se já existe o canon (ex 2 mercados de cartões), mantém a lista com mais linhas
                if len(arr) > len(merc.get(c, [])): merc[c] = arr
        merc = {k: v for k, v in merc.items() if v}
        if not merc: continue
        name = (d.get("name") or e.get("name") or "").replace(" vs. ", " - ").replace(" vs ", " - ")
        league = champs.get(e.get("champId")) or (d.get("champ") or {}).get("name", "")
        rec = {"casa": "EstrelaBet", "event_id": eid, "name": name, "league": league,
               "start": e.get("startDate") or d.get("startDate"),
               "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
        if n_out % 15 == 0: write_latest(n_out)
    f.close(); write_latest(n_out)
    print(f"[estrelabet] {n_det} detalhes · {n_out} jogos com mercado de estatística salvos em {out_path.name}")

if __name__ == "__main__":
    main()
