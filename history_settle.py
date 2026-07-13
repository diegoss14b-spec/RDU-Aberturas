# -*- coding: utf-8 -*-
"""history_settle.py — liquida as keys fechadas: preenche result (total do mercado no jogo),
won (green/red do OVER: result > linha; under = o inverso) e CLV:
  clv_pct = (odd_open / odd_close − 1) × 100   (>0 = abertura bateu o fechamento)
Fonte de resultados: data/odds_history/results/results_auto.json (gerado no PC a partir do
matches.json do RDUStats por export_results_for_valor.py e commitado) + manual_results.csv
(buracos). Match por data + nomes normalizados (fuzzy leve). Mercados sem fonte ficam
result=null (honesto). VM=1 nos cartões (alinhado ao modelo do site)."""
import json, re, sys, glob, unicodedata, csv
from pathlib import Path
from datetime import datetime
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
try:
    from rapidfuzz import fuzz
    def ratio(a, b): return fuzz.token_set_ratio(a, b)
except Exception:
    import difflib
    def ratio(a, b): return 100 * difflib.SequenceMatcher(None, a, b).ratio()
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import parse_history_key, norm_team

HIST = ROOT / "data" / "odds_history"
RES_AUTO = HIST / "results" / "results_auto.json"
RES_MANUAL = HIST / "results" / "manual_results.csv"
# mercado canônico -> campo no results_auto
FIELD = {"Cartões": "cards", "Faltas": "fouls", "Finalizações": "shots",
         "Impedimentos": "offsides", "Laterais": "throw_ins", "Tiros de meta": "goal_kicks"}

def nrm(s):
    return norm_team(s)

def load_results():
    """-> lista {date, home, away, cards, fouls, shots, offsides, throw_ins, goal_kicks}"""
    out = []
    if RES_AUTO.exists():
        try: out += json.loads(RES_AUTO.read_text(encoding="utf-8"))
        except Exception: pass
    if RES_MANUAL.exists():
        try:
            for row in csv.DictReader(RES_MANUAL.open(encoding="utf-8")):
                r = {"date": row.get("date"), "home": row.get("home"), "away": row.get("away")}
                for f in FIELD.values():
                    v = (row.get(f) or "").strip()
                    r[f] = int(v) if v.lstrip("-").isdigit() else None
                out.append(r)
        except Exception: pass
    for r in out:
        r["_h"] = nrm(r.get("home")); r["_a"] = nrm(r.get("away"))
    return out

def find_result(results, date, h, a):
    best, bs = None, 0
    for r in results:
        if r.get("date") != date: continue
        s = min(ratio(h, r["_h"]), ratio(a, r["_a"]))
        if s > bs: bs, best = s, r
    return best if bs >= 85 else None

def main():
    results = load_results()
    print(f"[settle] resultados disponíveis: {len(results):,}")
    n_settled = n_nores = 0
    clv_rows = []
    for kfp in sorted(glob.glob(str(HIST / "keys" / "*.json"))):
        p = Path(kfp)
        keys = json.loads(p.read_text(encoding="utf-8"))
        changed = False
        for key, k in keys.items():
            if k.get("status") != "closed": continue
            meta = parse_history_key(key)
            casa = meta.get("casa") or key.split("|")[0]
            mercado = meta.get("mercado")
            lado = meta.get("lado") or "over"
            try:
                linha = float(meta.get("linha"))
            except Exception:
                continue
            djogo = (k.get("kickoff") or "")[:10] or meta.get("day") or ""
            h = k.get("home_norm") or nrm(k.get("home_raw") or meta.get("hn") or "")
            a = k.get("away_norm") or nrm(k.get("away_raw") or meta.get("an") or "")
            fld = FIELD.get(mercado)
            r = find_result(results, djogo, h, a) if fld else None
            res = (r or {}).get(fld)
            if res is None:
                n_nores += 1; continue
            k["result"] = res
            if res == linha:                       # linha inteira empatou = push (stake devolvido)
                k["won"] = None
            else:
                over_won = res > linha
                k["won"] = over_won if lado == "over" else (not over_won)
            if k.get("open_odd") and k.get("close_odd"):
                k["clv_pct"] = round((k["open_odd"] / k["close_odd"] - 1) * 100, 2)
                k["beat_close"] = k["clv_pct"] > 0
            k["status"] = "settled"
            n_settled += 1; changed = True
            clv_rows.append({
                "key": key, "casa": casa, "mercado": mercado, "linha": linha, "lado": lado,
                "open_odd": k.get("open_odd"), "close_odd": k.get("close_odd"),
                "clv_pct": k.get("clv_pct"), "beat_close": k.get("beat_close"),
                "result": res, "won": k["won"],
                "kickoff": k.get("kickoff") or djogo,
                "sofa_id": k.get("sofa_id"),
            })
        if changed:
            p.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    if clv_rows:
        (HIST / "clv").mkdir(parents=True, exist_ok=True)
        # agrupa por mês do kickoff (fallback: agora)
        by_month = {}
        for row in clv_rows:
            month = None
            ko = row.get("kickoff") or ""
            if len(str(ko)) >= 7:
                month = str(ko)[:7]
            if not month:
                meta = parse_history_key(row.get("key") or "")
                if meta.get("day") and len(str(meta["day"])) >= 7:
                    month = str(meta["day"])[:7]
            if not month:
                month = datetime.now().strftime("%Y-%m")
            by_month.setdefault(month, []).append(row)
        for month, rows in by_month.items():
            with (HIST / "clv" / f"{month}.jsonl").open("a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[settle] {n_settled:,} keys liquidadas · {n_nores:,} fechadas aguardando resultado")

if __name__ == "__main__":
    main()
