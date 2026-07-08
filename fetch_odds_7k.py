# -*- coding: utf-8 -*-
"""fetch_odds_7k.py — captura odds do 7k (bet.br, plataforma FSSB) dos mercados de
estatística de JOGO INTEIRO, pra a Mesa de Aberturas.

Fluxo (FSSB "pulse"):
 1. host FSSB via 7k.bet.br/api/sports/anonymous-launch
 2. JWTs anônimos (authorization+session) + header time-area via Playwright (o SPA os gera;
    expiram em ~1 dia → pegar frescos a cada run)
 3. /api/pulse/snapshot/events?lang=BR-PT -> eventos (filtra futebol+prematch+muitos mercados)
 4. por evento: markets/all?markets=<eid>:ALL descobre os MarketType._id dos mercados de
    estatística; depois markets/all?markets=<eid>:<codes> traz Selections COM preço
    (Points=linha, Name=Mais/Menos, DisplayOdds.Decimal=odd).
Saída: data/odds/7k_{stamp}.jsonl + 7k_latest.json (formato normalizado do board)."""
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
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"
MIN_MARKETS = 60     # só jogos com muitos mercados têm os de estatística
MAX_EVENTS = 120

def canon(nm):
    m = (nm or "").lower()
    if "tempo" in m or "jogador" in m or "equipe" in m or "primeiro" in m or "antes" in m: return None
    if "cart" in m and "total" in m: return "Cartões"
    if "falta" in m and "total" in m: return "Faltas"
    if ("chute" in m or "finaliza" in m or "remate" in m) and "total" in m and "gol" not in m: return "Finalizações"
    if "impedi" in m and "total" in m: return "Impedimentos"
    if ("lateral" in m or "arremesso" in m) and "total" in m: return "Laterais"
    if "tiro de meta" in m and "total" in m: return "Tiros de meta"
    return None

def get_host():
    try:
        r = requests.get("https://7k.bet.br/api/sports/anonymous-launch",
                         headers={"User-Agent": UA}, timeout=20)
        m = re.search(r"https://([a-z0-9-]+\.fssb\.io)", r.json().get("url", ""))
        if m: return "https://" + m.group(1)
    except Exception: pass
    return "https://prod20350-kbet-152319626.fssb.io"

def get_jwts(host):
    """navega o 7k e captura authorization+session+time-area de qualquer request /api"""
    from playwright.sync_api import sync_playwright
    grabbed = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, locale="pt-BR")
        page = ctx.new_page()
        def on_req(r):
            h = r.headers or {}
            if "fssb.io/api" in r.url and h.get("authorization") and h.get("session"):
                grabbed.setdefault("authorization", h["authorization"])
                grabbed.setdefault("session", h["session"])
                grabbed.setdefault("time-area", h.get("time-area", "1"))
        page.on("request", on_req)
        try:
            page.goto("https://7k.bet.br/sports?bt-path=%2Fsoccer-1", timeout=45000, wait_until="domcontentloaded")
            for _ in range(20):
                if "authorization" in grabbed: break
                page.wait_for_timeout(1000)
        except Exception: pass
        ctx.close(); b.close()
    return grabbed

def main():
    now = datetime.now(BRT)
    host = get_host()
    jwt = get_jwts(host)
    if "authorization" not in jwt:
        print("[7k] não consegui os JWTs (Playwright) — abortando"); return
    hdr = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "pt-BR",
           "authorization": jwt["authorization"], "session": jwt["session"], "time-area": jwt.get("time-area", "1")}

    def gj(path):
        try:
            r = requests.get(host + path, headers=hdr, timeout=25)
            if r.status_code == 200 and r.text[:1] in "[{": return r.json()
        except Exception: pass
        return None

    evs = gj("/api/pulse/snapshot/events?lang=BR-PT") or []
    cand = [e for e in evs if str(e.get("SportId")) == "1" and not e.get("IsLive")
            and (e.get("TotalActiveMarketsCount") or 0) >= MIN_MARKETS]
    cand.sort(key=lambda e: -(e.get("TotalActiveMarketsCount") or 0))
    cand = cand[:MAX_EVENTS]
    print(f"[7k] snapshot {len(evs)} eventos · {len(cand)} candidatos (futebol+prematch+≥{MIN_MARKETS} mercados)")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"7k_{stamp}.jsonl"
    latest = OUTDIR / "7k_latest.json"
    def write_latest(n): latest.write_text(json.dumps({"file": out_path.name, "n": n, "at": now.isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
    write_latest(0)
    f = open(out_path, "w", encoding="utf-8")
    n_out = 0
    for e in cand:
        eid = e["_id"]
        allm = gj(f"/api/eventlist/eu/markets/all?markets={eid}:ALL")
        time.sleep(random.uniform(0.15, 0.3))
        if not allm: continue
        codes = {}
        for m in allm:
            mt = m.get("MarketType") or {}
            c = canon(mt.get("Name"))
            if c and mt.get("_id"): codes[mt["_id"]] = c
        if not codes: continue
        detm = gj(f"/api/eventlist/eu/markets/all?markets={eid}:" + "|".join(codes))
        time.sleep(random.uniform(0.15, 0.3))
        if not detm: continue
        merc = {}
        for m in detm:
            mt = m.get("MarketType") or {}
            c = codes.get(mt.get("_id")) or canon(mt.get("Name"))
            if not c: continue
            lines = {}
            for s in (m.get("Selections") or []):
                pts = s.get("Points")
                od = ((s.get("DisplayOdds") or {}).get("Decimal")) or s.get("TrueOdds")
                if pts is None or not od: continue
                try: od = float(od)
                except Exception: continue
                if od <= 1: continue
                side = "over" if (s.get("Side") == 1 or "mais" in (s.get("Name") or "").lower() or (s.get("OutcomeType") or "").lower() == "acima") else "under"
                lines.setdefault(float(pts), {})[side] = round(od, 2)
            arr = [{"linha": L, "over": v["over"], "under": v["under"]} for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if arr: merc[c] = arr
        if not merc: continue
        name = (e.get("EventName") or "").replace(" vs ", " - ")
        rec = {"casa": "7k", "event_id": eid, "name": name, "league": e.get("LeagueName"),
               "start": e.get("StartEventDate"), "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
        if n_out % 10 == 0: write_latest(n_out)
    f.close(); write_latest(n_out)
    print(f"[7k] {n_out} jogos com mercado de estatística salvos em {out_path.name}")

if __name__ == "__main__":
    main()
