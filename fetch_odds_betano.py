# -*- coding: utf-8 -*-
"""
fetch_odds_betano.py — captura de odds da BETANO (API pública JSON do site .bet.br).
Fluxo: /api/sport/futebol/jogos-de-hoje/ (lista) → por evento, abas:
  bt=1 Mais/Menos (gols) · bt=4 Escanteios · bt=5 Cartões · bt=6 Estatísticas (chutes/chutes
  no gol/FALTAS/impedimentos/tiros de meta) — exatamente os mercados dos nossos modelos.
Extrai mercados Over/Under em formato compacto: (mercado, linha, odd_over, odd_under).
Salva: data/odds/betano_{YYYY-MM-DD_HHMM}.jsonl (1 evento/linha) + betano_latest.json.
Uso futuro: comparador odds × modelos calibrados → apostas de valor. Só leitura/GET, ritmo educado.
"""
import json, sys, os, time, re
from datetime import datetime
from pathlib import Path
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
from curl_cffi import requests as creq

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/odds"; OUT.mkdir(parents=True, exist_ok=True)
LOG = ROOT / "data/_odds_betano.log"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
     "Accept": "application/json", "Accept-Language": "pt-BR"}
BASE = "https://www.betano.bet.br"
TABS = {"1": "gols", "4": "escanteios", "5": "cartoes", "6": "estatisticas"}
MAX_EVENTS = 60

def log(m):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {m}"
    try: print(line, flush=True)
    except Exception: pass
    try: LOG.open("a", encoding="utf-8").write(line + "\n")
    except Exception: pass

def get(url, tries=3):
    for a in range(tries):
        try:
            r = creq.get(url, headers=H, impersonate="chrome124", timeout=18)
            if r.status_code == 200 and "json" in (r.headers.get("content-type") or ""):
                return r.json()
            time.sleep(2 * (a + 1))
        except Exception:
            time.sleep(2)
    return None

def extract_ou(markets):
    """mercados Over/Under → [{market, line, over, under}] (só pares completos)."""
    out = []
    for m in markets or []:
        name = (m.get("name") or "").strip()
        sels = m.get("selections") or []
        by_line = {}
        for s in sels:
            hcp = s.get("handicap")
            nm = (s.get("fullName") or s.get("name") or "")
            if hcp is None or not s.get("price"): continue
            side = "over" if re.match(r"\s*mais", nm, re.I) else ("under" if re.match(r"\s*menos", nm, re.I) else None)
            if not side: continue
            by_line.setdefault(hcp, {})[side] = s["price"]
        for line, pair in by_line.items():
            if "over" in pair and "under" in pair:
                out.append({"market": name, "line": line, "over": pair["over"], "under": pair["under"]})
    return out

def extract_1x2(markets):
    for m in markets or []:
        if (m.get("name") or "").startswith("Resultado Final"):
            sels = {s.get("name"): s.get("price") for s in (m.get("selections") or [])}
            if all(k in sels for k in ("1", "X", "2")):
                return {"1": sels["1"], "X": sels["X"], "2": sels["2"]}
    return None

def main():
    feed = get(f"{BASE}/api/sport/futebol/jogos-de-hoje/")
    if not feed:
        log("feed indisponível"); return
    events = []
    for b in ((feed.get("data") or {}).get("blocks") or []):
        for ev in (b.get("events") or []):
            events.append({"id": ev.get("id"), "name": ev.get("name"), "url": ev.get("url"),
                           "league": ev.get("leagueName"), "region": ev.get("regionName"),
                           "start": ev.get("startTime")})
    log(f"feed: {len(events)} eventos")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    fp = OUT / f"betano_{stamp}.jsonl"
    n_ok = 0
    with fp.open("w", encoding="utf-8") as f:
        for ev in events[:MAX_EVENTS]:
            if not ev.get("url"): continue
            rec = {"captured_at": datetime.now().isoformat(timespec="seconds"),
                   "event_id": ev["id"], "name": ev["name"], "league": ev["league"],
                   "region": ev["region"], "start": ev["start"], "markets": {}}
            base_ev = get(f"{BASE}/api{ev['url']}")
            if base_ev:
                mks = ((base_ev.get("data") or {}).get("event") or {}).get("markets") or []
                x = extract_1x2(mks)
                if x: rec["markets"]["1x2"] = x
                rec["markets"]["principais_ou"] = extract_ou(mks)
            time.sleep(1.0)
            for bt, label in TABS.items():
                d = get(f"{BASE}/api{ev['url']}?bt={bt}")
                if d:
                    mks = ((d.get("data") or {}).get("event") or {}).get("markets") or []
                    rec["markets"][label] = extract_ou(mks)
                time.sleep(1.0)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_ok += 1
    (OUT / "betano_latest.json").write_text(json.dumps({"file": fp.name, "n": n_ok, "at": stamp},
                                                       ensure_ascii=False), encoding="utf-8")
    log(f"✅ {n_ok} eventos capturados → {fp.name}")

if __name__ == "__main__":
    main()
