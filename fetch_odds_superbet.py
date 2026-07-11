# -*- coding: utf-8 -*-
"""fetch_odds_superbet.py — captura odds da Superbet (bet.br) dos mercados de estatística
de JOGO INTEIRO, pra a Mesa de Aberturas. API pública offer (Fastly):
  by-date: /v2/pt-BR/events/by-date?currentStatus=active&offerState=prematch&sportId=5&startDate&endDate
  detalhe: /v2/pt-BR/events/{eventId}  -> campo 'odds' [{marketName,name,price,...}]
  struct : /v2/pt-BR/struct            -> nomes de torneio/categoria
⚠️ o CDN manda header 'content-encoding: gzip' às vezes mentiroso → ler bytes CRUS e
decodificar (gzip senão plain). marketName limpo tipo 'Total de Cartões'; outcome
'Mais de X.5'/'Menos de X.5'. Saída: data/odds/superbet_{stamp}.jsonl + superbet_latest.json,
mesmo formato normalizado do board. pythonw-safe, pacing educado."""
import sys, os, json, gzip, re, time, random
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
from capture_common import odds_window, in_window

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BASE = "https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
     "Accept": "application/json", "Origin": "https://superbet.bet.br", "Referer": "https://superbet.bet.br/"}
BRT = timezone(timedelta(hours=-3))
DAYS = 4          # janela de captura (hoje + N dias)
MAX_EVENTS = 500  # teto de detalhes por rodada
MIN_EVENTS = 10   # mínimo pro finish() (abaixo = exit 2)
MIN_EFF = MIN_EVENTS  # modo close (ODDS_WINDOW_H) reduz — ver main()

def dec(raw):
    try:
        d = gzip.decompress(raw)
        if d[:1] in b"{[": return d.decode("utf-8", "replace")
    except Exception: pass
    return raw.decode("utf-8", "replace")

def get(url, tries=4):
    for _ in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=25, stream=True)
            body = dec(r.raw.read())
            if r.status_code == 200 and body[:1] in "{[":
                return json.loads(body)
            if r.status_code == 404: return None
        except Exception:
            pass
        time.sleep(1.5)
    return None

def canon(mn):
    m = (mn or "").lower()
    if "cart" in m: return "Cartões"
    if "falta" in m: return "Faltas"
    if "escanteio" in m or "corner" in m: return "Escanteios"
    if "chute" in m and ("gol" in m or "alvo" in m or "no gol" in m): return "Chutes no gol"
    if "chute" in m or "finaliza" in m or "remate" in m: return "Finalizações"
    if "impedi" in m: return "Impedimentos"
    if "lateral" in m or "arremesso" in m: return "Laterais"
    if "tiro de meta" in m or "tiro-de-meta" in m or "tiros de meta" in m: return "Tiros de meta"
    if "desarme" in m: return "Desarmes"
    return None

OUTC = re.compile(r"(mais|menos) de\s+([\d.]+)", re.I)

def is_full_game(mn):
    m = (mn or "")
    if ";" in m or "&" in m: return False
    ml = m.lower()
    if "1º tempo" in ml or "2º tempo" in ml or "1° tempo" in ml or "primeiro tempo" in ml: return False
    return m.strip().lower().startswith("total de")

def main():
    struct = get(f"{BASE}/struct") or {}
    tnames = {}
    def walk(o):
        if isinstance(o, dict):
            if o.get("id") and o.get("name") and ("tournament" in json.dumps(o.get("type", "")) or True):
                tnames[str(o["id"])] = o["name"]
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    try: walk(struct.get("data", struct))
    except Exception: pass

    now = datetime.now(BRT)
    d0 = now.strftime("%Y-%m-%d %H:%M:%S")
    d1 = (now + timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    lst = get(f"{BASE}/events/by-date?currentStatus=active&offerState=prematch&sportId=5&startDate={d0}&endDate={d1}")
    events = (lst or {}).get("data") or []
    print(f"[superbet] by-date: {len(events)} eventos (janela {DAYS}d)")
    _wh = odds_window()
    if _wh is not None:   # modo close: kickoff = unixDateMillis (utcDate/matchDate = fallback; matchTimestamp NÃO é kickoff)
        global MIN_EFF
        _tot = len(events)
        events = [e for e in events
                  if in_window(e.get("unixDateMillis") or e.get("utcDate") or e.get("matchDate"), _wh)]
        MIN_EFF = (min(MIN_EVENTS, 1) if events else 0)   # janela curta: 1+ ok; lista vazia não é falha
        print(f"[superbet] modo close: janela {_wh:g}h -> {len(events)} de {_tot} eventos")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"superbet_{stamp}.jsonl"
    latest = OUTDIR / "superbet_latest.json"
    def write_latest(n):
        latest.write_text(json.dumps({"file": out_path.name, "n": n, "at": now.isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
    write_latest(0)   # pointer já aponta pro arquivo em construção (run parcial ainda é usável)
    f = open(out_path, "w", encoding="utf-8")
    n_out = n_det = 0
    for e in events[:MAX_EVENTS]:
        eid = e.get("eventId")
        if not eid: continue
        det = get(f"{BASE}/events/{eid}")
        n_det += 1
        time.sleep(random.uniform(0.2, 0.4))
        if not det or not det.get("data"): continue
        ev = det["data"][0]
        odds = ev.get("odds") or []
        mk = {}
        for o in odds:
            if o.get("status") != "active": continue
            c = canon(o.get("marketName")) if is_full_game(o.get("marketName")) else None
            if not c: continue
            mo = OUTC.search(o.get("name") or o.get("info") or "")
            if not mo: continue
            side = "over" if mo.group(1).lower() == "mais" else "under"
            line = float(mo.group(2))
            price = o.get("price")
            if not price or price <= 1: continue
            key = (c, line)
            mk.setdefault(c, {}).setdefault(line, {})[side] = round(price, 2)
        # só manter linhas com os 2 lados
        merc = {}
        for c, lines in mk.items():
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if arr: merc[c] = arr
        if not merc: continue
        name = (ev.get("matchName") or "").replace("·", " - ")
        league = tnames.get(str(ev.get("tournamentId")), "")
        ts = ev.get("unixDateMillis") or ev.get("matchTimestamp")
        rec = {"casa": "Superbet", "event_id": eid, "name": name, "league": league,
               "start": ts, "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
        if n_out % 15 == 0: write_latest(n_out)
    f.close()
    write_latest(n_out)
    print(f"[superbet] {n_det} detalhes buscados · {n_out} jogos com mercado de estatística salvos em {out_path.name}")
    return n_out

if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish
    try:
        _n = main() or 0
        sys.exit(finish("superbet", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("superbet", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
