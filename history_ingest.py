# -*- coding: utf-8 -*-
"""history_ingest.py — P1 do brief: BANCO DE ODDS próprio. A cada rodada de captura, lê os
4 {casa}_latest.json e registra:
  - TICK (append em data/odds_history/ticks/{YYYY-MM-DD}.jsonl) — só quando a odd MUDOU
    (>=0.01) vs a última vista da key, ou na primeira observação (abertura). Mantém o git leve.
  - KEY (upsert em data/odds_history/keys/{YYYY-MM}.json) — open/last/min/max/n_obs/n_moves,
    kickoff, status open|closed|settled.
key = casa|data_jogo|home_norm|away_norm|mercado|linha|lado. Idempotente por rodada."""
import json, re, sys, unicodedata
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
ODDS = ROOT / "data" / "odds"
HIST = ROOT / "data" / "odds_history"
BRT = timezone(timedelta(hours=-3))
CASAS = ["betano", "superbet", "estrelabet", "7k"]

def nrm(s):
    s = unicodedata.normalize("NFD", s or ""); s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

def parse_start(s):
    if s is None: return None
    try:
        if isinstance(s, (int, float)) or (isinstance(s, str) and str(s).isdigit()):
            return datetime.fromtimestamp(int(s) / 1000, tz=BRT)
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(BRT)
    except Exception:
        return None

def load_events(casa):
    """eventos normalizados {name, start, mercados:{canon:[{linha,over,under}]}} da última rodada."""
    ptr = ODDS / f"{casa}_latest.json"
    if not ptr.exists(): return []
    try:
        fn = json.loads(ptr.read_text(encoding="utf-8")).get("file")
        src = ODDS / fn if fn else None
        if not src or not src.exists(): return []
        evs = []
        for ln in src.read_text(encoding="utf-8").strip().split("\n"):
            if not ln.strip(): continue
            e = json.loads(ln)
            if casa == "betano":                       # betano é cru: converter pro formato canônico
                mk = {}
                MKMAP = {"Total de Cartões": "Cartões", "Total de Faltas": "Faltas", "Total de chutes": "Finalizações",
                         "Total de Impedimentos": "Impedimentos", "Total de laterais": "Laterais",
                         "Total de tiros de meta": "Tiros de meta"}
                for aba in ("cartoes", "estatisticas", "principais_ou"):
                    for m in (e.get("markets", {}).get(aba) or []):
                        canon = MKMAP.get(m.get("market"))
                        if canon and m.get("over") and m.get("under") and m.get("line") is not None:
                            mk.setdefault(canon, {})[m["line"]] = {"linha": m["line"], "over": m["over"], "under": m["under"]}
                merc = {c: list(v.values()) for c, v in mk.items()}
            else:
                merc = e.get("mercados") or {}
            if merc:
                evs.append({"name": e.get("name"), "start": e.get("start"), "mercados": merc,
                            "home_raw": (e.get("name") or "").split(" - ")[0].strip(),
                            "away_raw": (e.get("name") or "").split(" - ")[-1].strip()})
        return evs
    except Exception as ex:
        print(f"[ingest] {casa}: erro lendo rodada ({type(ex).__name__})"); return []

def main():
    now = datetime.now(BRT); now_iso = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    (HIST / "ticks").mkdir(parents=True, exist_ok=True)
    (HIST / "keys").mkdir(parents=True, exist_ok=True)
    month = now.strftime("%Y-%m")
    kf = HIST / "keys" / f"{month}.json"
    keys = json.loads(kf.read_text(encoding="utf-8")) if kf.exists() else {}
    tick_f = (HIST / "ticks" / f"{now.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8")

    n_ticks = n_new = n_obs = 0
    for casa in CASAS:
        for ev in load_events(casa):
            dt = parse_start(ev["start"])
            if not dt: continue
            djogo = dt.strftime("%Y-%m-%d")
            h, a = nrm(ev["home_raw"]), nrm(ev["away_raw"])
            for mercado, linhas in (ev["mercados"] or {}).items():
                for l in linhas:
                    linha = l.get("linha")
                    if linha is None: continue
                    for lado, odd in (("over", l.get("over")), ("under", l.get("under"))):
                        if not odd or odd <= 1.01 or odd > 50: continue
                        n_obs += 1
                        key = f"{casa}|{djogo}|{h}|{a}|{mercado}|{linha}|{lado}"
                        k = keys.get(key)
                        changed = (k is None) or (abs((k.get("last_odd") or 0) - odd) >= 0.01)
                        if k is None:
                            keys[key] = k = {"open_odd": odd, "open_ts": now_iso, "open_is_first_seen": True,
                                             "close_odd": None, "close_ts": None,
                                             "last_odd": odd, "last_ts": now_iso,
                                             "n_obs": 0, "n_moves": 0,
                                             "max_odd": odd, "min_odd": odd,
                                             "kickoff": dt.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                             "home_raw": ev["home_raw"], "away_raw": ev["away_raw"],
                                             "result": None, "won": None, "clv_pct": None, "status": "open"}
                            n_new += 1
                        else:
                            if changed and k.get("status") == "open": k["n_moves"] += 1
                        k["n_obs"] += 1
                        if k.get("status") == "open":
                            k["last_odd"] = odd; k["last_ts"] = now_iso
                            k["max_odd"] = max(k["max_odd"], odd); k["min_odd"] = min(k["min_odd"], odd)
                        if changed:
                            tick_f.write(json.dumps({"ts": now_iso, "casa": casa, "kickoff": k["kickoff"],
                                                     "home": h, "away": a, "mercado": mercado,
                                                     "linha": linha, "lado": lado, "odd": odd},
                                                    ensure_ascii=False) + "\n")
                            n_ticks += 1
    tick_f.close()
    kf.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    print(f"[ingest] {n_obs:,} observações · {n_ticks:,} ticks novos (mudanças) · {n_new:,} keys novas (aberturas) · total keys no mês: {len(keys):,}")

if __name__ == "__main__":
    main()
