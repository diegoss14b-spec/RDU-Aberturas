# -*- coding: utf-8 -*-
"""build_history.py — monta o arquivo que a view "Histórico & CLV" consome no site do valor.

FONTE DE VERDADE = data/odds_history/keys/*.json (estado idempotente por chave, superset do
clv/*.jsonl — que é append-only e DUPLICARIA a cada re-settle, então NÃO lemos dele).
Saída = valor/data/history.js  ->  window.HIST = {...};  (UTF-8, ensure_ascii=False)

GATE de honestidade `clv_valido` = status settled ∧ open_odd ∧ close_odd ∧ open_ts < kickoff
(abertura vista ANTES do apito). As linhas capturadas depois do apito (open==close) aparecem
na tabela esmaecidas, mas NÃO entram nas taxas de CLV. Com dados esparsos as métricas ficam
None ("aguardando") em vez de virar um painel de zeros que mente valor.

clv_pct = (open_odd/close_odd - 1)*100  (igual history_settle.py L80; >0 = abertura bateu o fechamento)
won: green do lado (over ganha se result>linha), já gravado por history_settle. Filtro BOARD6 =
os 6 mercados do board (exclui Escanteios/Chutes no gol/Desarmes) p/ não divergir da Mesa.
"""
import json, sys, glob, math, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent
HIST = ROOT / "data" / "odds_history"
OUT = ROOT / "valor" / "data" / "history.js"
BRT = timezone(timedelta(hours=-3))

BOARD6 = {"Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta"}
LIMIARES = {"head": 30, "bucket": 20, "roi": 50}
LADO_PT = {"over": "Mais", "under": "Menos"}


def ts(s):
    """ISO com tz ('2026-07-09T17:00:00-0300') -> datetime aware. None se não parsear."""
    if not s: return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    # fallback p/ offset sem ':' em pythons antigos
    try:
        if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
            s2 = s[:-2] + ":" + s[-2:]
            return datetime.fromisoformat(s2)
    except Exception:
        pass
    return None


def wilson(k, n, z=1.96):
    """IC95 de Wilson p/ proporção k/n. Retorna (lo, hi) em % ou (None,None)."""
    if not n: return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(100 * (c - hw) / d, 1), round(100 * (c + hw) / d, 1))


def agg(rows):
    """rows = dicts com clv_pct(float) e won(bool|None=push). Métricas + IC, ou None se vazio.
    Push (won=None, linha inteira devolvida) sai dos denominadores de green (só decididas)."""
    n = len(rows)
    if not n:
        return {"n": 0, "n_dec": 0, "beat": None, "beat_ci": [None, None],
                "clv": None, "clv_med": None, "green": None, "green_ci": [None, None]}
    clvs = [r["clv_pct"] for r in rows if r.get("clv_pct") is not None]
    beat_k = sum(1 for c in clvs if c > 0)
    dec = [r for r in rows if r.get("won") is not None]   # decididas (exclui push)
    won_k = sum(1 for r in dec if r["won"])
    blo, bhi = wilson(beat_k, len(clvs)) if clvs else (None, None)
    glo, ghi = wilson(won_k, len(dec)) if dec else (None, None)
    return {
        "n": n, "n_dec": len(dec),
        "beat": round(100 * beat_k / len(clvs), 1) if clvs else None,
        "beat_ci": [blo, bhi],
        "clv": round(statistics.fmean(clvs), 2) if clvs else None,
        "clv_med": round(statistics.median(clvs), 2) if clvs else None,
        "green": round(100 * won_k / len(dec), 1) if dec else None,
        "green_ci": [glo, ghi],
    }


def roi(rows, odd_key):
    """ROI hipotético flat 1u apostando na odd `odd_key` (open_odd|close_odd). Push (won=None)
    é devolução de stake → fora do cálculo. None se n de decididas < roi."""
    usable = [r for r in rows if r.get(odd_key) and r.get("won") is not None]
    n = len(usable)
    if n < LIMIARES["roi"]:
        return None
    ret = sum((r[odd_key] if r["won"] else 0.0) for r in usable)
    return round(100 * (ret - n) / n, 2)


def main():
    keys = {}
    for f in sorted(glob.glob(str(HIST / "keys" / "*.json"))):
        try:
            keys.update(json.loads(Path(f).read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[history] pulei {f}: {type(e).__name__}")

    def merc_of(key):
        parts = key.split("|")
        return parts[4] if len(parts) >= 5 else None

    # --- conjuntos ---
    settled = [(k, v) for k, v in keys.items()
               if v.get("status") == "settled" and merc_of(k) in BOARD6]
    # GATE: CLV real só quando a abertura foi vista ANTES do apito
    V = []
    for k, v in settled:
        o, c = v.get("open_odd"), v.get("close_odd")
        ots, kts = ts(v.get("open_ts")), ts(v.get("kickoff"))
        if o and c and ots and kts and ots < kts:
            V.append({"clv_pct": v.get("clv_pct"), "won": v.get("won"),
                      "open_odd": o, "close_odd": c,
                      "mercado": merc_of(k), "casa": k.split("|")[0], "lado": k.split("|")[6]})

    n_valid, n_settled = len(V), len(settled)
    head_ok = n_valid >= LIMIARES["head"]

    # green geral = placar sobre as settled DECIDIDAS (exclui push; não é CLV; alta variância)
    settled_dec = [(k, v) for k, v in settled if v.get("won") is not None]
    green_geral = round(100 * sum(1 for _, v in settled_dec if v.get("won")) / len(settled_dec), 1) if settled_dec else None

    # green quando bateu × não bateu o fechamento (a prova de valor do CLV) — com IC de Wilson,
    # só sobre decididas (push fora). A diferença só é "conclusiva" quando os IC não se sobrepõem.
    bk = LIMIARES["bucket"]
    bateu = [r for r in V if r["clv_pct"] is not None and r["clv_pct"] > 0 and r["won"] is not None]
    nao = [r for r in V if r["clv_pct"] is not None and r["clv_pct"] <= 0 and r["won"] is not None]
    gb_k, gn_k = sum(1 for r in bateu if r["won"]), sum(1 for r in nao if r["won"])
    green_bateu = round(100 * gb_k / len(bateu), 1) if len(bateu) >= bk else None
    green_nao = round(100 * gn_k / len(nao), 1) if len(nao) >= bk else None
    green_bateu_ci = list(wilson(gb_k, len(bateu))) if len(bateu) >= bk else [None, None]
    green_nao_ci = list(wilson(gn_k, len(nao))) if len(nao) >= bk else [None, None]
    green_diff_conclusiva = None
    if green_bateu_ci[0] is not None and green_nao_ci[0] is not None:
        green_diff_conclusiva = not (green_bateu_ci[0] <= green_nao_ci[1] and green_nao_ci[0] <= green_bateu_ci[1])

    a = agg(V) if head_ok else {"beat": None, "beat_ci": [None, None], "clv": None, "clv_med": None}
    ra = roi(V, "open_odd") if head_ok else None
    rf = roi(V, "close_odd") if head_ok else None

    head = {
        "n_valid": n_valid, "n_settled": n_settled, "n_settled_dec": len(settled_dec),
        "beat_close_rate": a["beat"], "beat_ci": a["beat_ci"],
        "clv_medio": a["clv"], "clv_mediana": a["clv_med"],
        "green_geral": green_geral,
        "green_bateu": green_bateu, "green_nao": green_nao,
        "green_bateu_ci": green_bateu_ci, "green_nao_ci": green_nao_ci,
        "green_diff_conclusiva": green_diff_conclusiva,
        "n_bateu": len(bateu), "n_nao": len(nao),
        "roi_abertura": ra, "roi_fechamento": rf,
        "roi_delta": (round(ra - rf, 2) if (ra is not None and rf is not None) else None),
    }

    # --- recortes sobre V (só se head_ok) ---
    def cut(field):
        if not head_ok: return []
        groups = {}
        for r in V:
            groups.setdefault(r[field], []).append(r)
        out = []
        for nome, rows in groups.items():
            g = agg(rows)
            small = g["n"] < LIMIARES["bucket"]
            out.append({"nome": LADO_PT.get(nome, nome), "n": g["n"],
                        "beat": g["beat"], "beat_ci": g["beat_ci"],
                        "clv": g["clv"], "clv_med": g["clv_med"],
                        "green": g["green"], "green_ci": g["green_ci"], "small": small})
        out.sort(key=lambda x: -x["n"])
        return out

    recortes = {"mercado": cut("mercado"), "casa": cut("casa"), "lado": cut("lado")}

    # --- tabela de liquidadas (o útil de hoje) ---
    liquidadas = []
    for k, v in settled:
        casa, djogo, h, a2, mercado, linha, lado = k.split("|")
        ots, kts = ts(v.get("open_ts")), ts(v.get("kickoff"))
        clv_valido = bool(v.get("open_odd") and v.get("close_odd") and ots and kts and ots < kts)
        liquidadas.append({
            "gk": f'{djogo}|{h}|{a2}|{mercado}|{linha}|{lado}',
            "jogo": f'{v.get("home_raw") or h} x {v.get("away_raw") or a2}',
            "data": djogo, "casa": casa, "mercado": mercado,
            "linha": float(linha), "lado": LADO_PT.get(lado, lado),
            "open": v.get("open_odd"), "close": v.get("close_odd"),
            "clv": v.get("clv_pct"), "beat": v.get("beat_close"),
            "result": v.get("result"), "won": v.get("won"),
            "n_moves": v.get("n_moves", 0), "kickoff": v.get("kickoff"),
            "clv_valido": clv_valido,
        })
    liquidadas.sort(key=lambda x: x["kickoff"] or "", reverse=True)
    liquidadas = liquidadas[:200]

    # --- linhas abertas com movimento (valor vivo de hoje) ---
    abertas = []
    for k, v in keys.items():
        if v.get("status") not in ("open", "closed"): continue
        if merc_of(k) not in BOARD6: continue
        if not v.get("n_moves"): continue
        casa, djogo, h, a2, mercado, linha, lado = k.split("|")
        op, last = v.get("open_odd"), v.get("last_odd")
        drift = round((last / op - 1) * 100, 2) if (op and last) else None
        abertas.append({
            "gk": f'{djogo}|{h}|{a2}|{mercado}|{linha}|{lado}',
            "jogo": f'{v.get("home_raw") or h} x {v.get("away_raw") or a2}',
            "data": djogo, "casa": casa, "mercado": mercado,
            "linha": float(linha), "lado": LADO_PT.get(lado, lado),
            "open": op, "last": last, "min": v.get("min_odd"), "max": v.get("max_odd"),
            "drift_pct": drift, "n_moves": v.get("n_moves", 0), "kickoff": v.get("kickoff"),
        })
    abertas.sort(key=lambda x: x["kickoff"] or "")

    # banner no MESMO universo das métricas (BOARD6), pra não superestimar o tamanho do banco
    b6 = {k: v for k, v in keys.items() if merc_of(k) in BOARD6}
    banco = {
        "monitoradas": len(b6),
        "liquidadas": n_settled,
        "clv_validas": n_valid,
        "moveu_pct": round(100 * sum(1 for v in b6.values() if (v.get("n_moves") or 0) >= 1) / len(b6), 1) if b6 else 0.0,
    }

    out = {
        "gerado": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
        "limiares": LIMIARES,
        "banco": banco,
        "head": head,
        "recortes": recortes,
        "liquidadas": liquidadas,
        "abertas": abertas,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.HIST=" + json.dumps(out, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"[history] banco {banco['monitoradas']}/{banco['liquidadas']}/{banco['clv_validas']} · "
          f"{len(liquidadas)} na tabela · {len(abertas)} abertas c/ movimento · green_geral {green_geral} · -> {OUT.name}")


if __name__ == "__main__":
    main()
