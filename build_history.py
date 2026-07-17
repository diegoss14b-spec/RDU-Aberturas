# -*- coding: utf-8 -*-
"""build_history.py — monta o arquivo que a view "Histórico & CLV" consome no site do valor.

FONTE DE VERDADE = data/odds_history/keys/*.json (estado idempotente por chave, superset do
clv/*.jsonl — que é append-only e DUPLICARIA a cada re-settle, então NÃO lemos dele).
Saída = valor/data/history.js  ->  window.HIST = {...};  (UTF-8, ensure_ascii=False)

GATE de honestidade ``is_strict_clv`` = settled + odds válidas + open e close reais
antes do kickoff + qualidade aceita. Push continua sendo CLV válido.

Headline, recortes, ROI e IC usam uma unidade independente por jogo+mercado. A seleção
fixa usa somente informação de abertura (linha mais equilibrada, melhor preço do lado
perto de 2,00); casas, lados e alternativas permanecem apenas no explorador.

clv_pct = (open_odd/close_odd - 1)*100 (>0 = abertura bateu o fechamento).
"""
import json, sys, glob, math, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import parse_history_key
from history_merge import merge_records
from history_quality import (
    compute_capture_quality, ensure_aware, is_strict_clv, parse_ts, strict_clv_reason,
    valid_decimal_odd,
)

HIST = ROOT / "data" / "odds_history"
OUT = ROOT / "valor" / "data" / "history.js"
BRT = timezone(timedelta(hours=-3))

# mercados do board (inclui Escanteios/CG/Desarmes — mesa e histórico alinhados)
BOARD6 = {"Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta",
          "Escanteios", "Chutes no gol", "Desarmes"}
LIMIARES = {"head": 30, "bucket": 20, "roi": 50}
LADO_PT = {"over": "Mais", "under": "Menos"}



def line_key(value):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(val)) if val.is_integer() else str(val)


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
                "clv": None, "clv_med": None, "clv_ci": [None, None],
                "green": None, "green_ci": [None, None]}
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
        "clv_ci": mean_ci(clvs),
        "green": round(100 * won_k / len(dec), 1) if dec else None,
        "green_ci": [glo, ghi],
    }


def mean_ci(values, z=1.96):
    """Normal 95% CI over independent signal units, not raw book rows."""
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if len(vals) < 2:
        return [None, None]
    mean = statistics.fmean(vals)
    se = statistics.stdev(vals) / math.sqrt(len(vals))
    return [round(mean - z * se, 2), round(mean + z * se, 2)]


def roi_stats(rows, odd_key):
    """Flat 1u ROI over one deterministic signal per game+market.

    Win = odd-1, loss = -1, push = 0. Push remains in stake denominator.
    """
    usable = [r for r in rows if valid_decimal_odd(r.get(odd_key))
              and (isinstance(r.get("won"), bool) or r.get("push"))]
    profits = []
    for r in usable:
        if r.get("push"):
            profits.append(0.0)
        elif r["won"]:
            profits.append(float(r[odd_key]) - 1.0)
        else:
            profits.append(-1.0)
    if len(profits) < LIMIARES["roi"]:
        return {"roi": None, "ci": [None, None], "n": len(profits),
                "n_push": sum(1 for r in usable if r.get("push"))}
    ci = mean_ci([100.0 * p for p in profits])
    return {"roi": round(100.0 * statistics.fmean(profits), 2), "ci": ci,
            "n": len(profits), "n_push": sum(1 for r in usable if r.get("push"))}


def select_main_signals(rows):
    """Collapse book/side/alternative rows to one deterministic game+market signal.

    Rule uses opening information only: most balanced line across paired O/U prices,
    then the side whose best available opening price is nearest 2.00 (over wins ties).
    No result or close information is used for selection.
    """
    grouped = {}
    for row in rows:
        grouped.setdefault((row["gid"], row["mercado"]), []).append(row)
    selected = []
    for _signal_key, signal_rows in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        by_line = {}
        for row in signal_rows:
            by_line.setdefault(float(row["linha"]), []).append(row)
        line_scores = []
        for line, line_rows in by_line.items():
            paired = {}
            for row in line_rows:
                sides = paired.setdefault(row["casa"], {})
                sides[row["lado"]] = max(float(row["open_odd"]), sides.get(row["lado"], 0.0))
            gaps = [abs(sides["over"] - sides["under"]) for sides in paired.values()
                    if "over" in sides and "under" in sides]
            line_scores.append((0 if gaps else 1,
                                statistics.fmean(gaps) if gaps else 999.0,
                                line))
        main_line = min(line_scores)[2]
        candidates = [r for r in signal_rows if float(r["linha"]) == main_line]
        best_side = {}
        for side in ("over", "under"):
            side_rows = [r for r in candidates if r["lado"] == side]
            if side_rows:
                best_side[side] = sorted(
                    side_rows,
                    key=lambda r: (-float(r["open_odd"]), str(r["casa"]),
                                   r.get("open_epoch") or 0),
                )[0]
        if not best_side:
            continue
        chosen_side = min(best_side, key=lambda side: (
            abs(float(best_side[side]["open_odd"]) - 2.0),
            0 if side == "over" else 1,
        ))
        chosen = dict(best_side[chosen_side])
        chosen["selection_rule"] = "balanced_main_open_near_2_best_price"
        selected.append(chosen)
    return selected


def main():
    keys = {}
    for f in sorted(glob.glob(str(HIST / "keys" / "*.json"))):
        try:
            raw = json.loads(Path(f).read_text(encoding="utf-8"))
            for kk, vv in raw.items():
                if kk.startswith("__") or not isinstance(vv, dict):
                    continue
                keys[kk] = merge_records(keys[kk], vv) if kk in keys else vv
        except Exception as e:
            print(f"[history] pulei {f}: {type(e).__name__}")

    def merc_of(key):
        return parse_history_key(key).get("mercado")

    # Remap exact legacy identities to a Sofa id already present elsewhere in the bank.
    alias_sofa = {}
    for kk, vv in keys.items():
        pp = parse_history_key(kk)
        sid = vv.get("sofa_id") or pp.get("sofa_id")
        if not sid:
            continue
        day = pp.get("day") or (vv.get("kickoff") or "")[:10]
        hn = vv.get("home_norm") or pp.get("hn") or ""
        an = vv.get("away_norm") or pp.get("an") or ""
        if day and hn and an:
            alias_sofa[(day, hn, an)] = str(sid)
            alias_sofa[(day, an, hn)] = str(sid)

    def row_ids(k, v):
        """IDs estáveis pro explorador/gráficos (legado + sofa)."""
        p = parse_history_key(k)
        mercado = p.get("mercado") or ""
        linha = p.get("linha")
        lado = p.get("lado") or "over"
        casa = p.get("casa") or k.split("|")[0]
        day = p.get("day") or (v.get("kickoff") or "")[:10]
        hn = v.get("home_norm") or p.get("hn") or ""
        an = v.get("away_norm") or p.get("an") or ""
        sid = v.get("sofa_id") or p.get("sofa_id") or alias_sofa.get((day, hn, an))
        if sid:
            gid = f"sofa:{sid}"
        else:
            gid = f"{day}|{hn}|{an}"
        line = float(linha)
        gk = f"{gid}|{mercado}|{line_key(line)}|{lado}"
        return casa, gid, gk, mercado, line, lado

    # --- strict CLV rows and independent signal units ---
    settled = [(k, v) for k, v in keys.items()
               if v.get("status") == "settled" and merc_of(k) in BOARD6]
    strict_rows = []
    for k, v in settled:
        if not is_strict_clv(v):
            continue
        casa, gid, gk, mercado, linha, lado = row_ids(k, v)
        try:
            line_num = float(linha)
            result_num = float(v["result"]) if v.get("result") is not None else None
        except (TypeError, ValueError):
            continue
        clv = v.get("clv_pct")
        if clv is None:
            clv = (float(v["open_odd"]) / float(v["close_odd"]) - 1.0) * 100.0
        ots = ensure_aware(parse_ts(v.get("open_ts")))
        kts = ensure_aware(parse_ts(v.get("kickoff")))
        push = bool(v.get("won") is None and result_num is not None
                    and abs(result_num - line_num) < 1e-9)
        strict_rows.append({
            "clv_pct": float(clv), "won": v.get("won"), "push": push,
            "open_odd": float(v["open_odd"]), "close_odd": float(v["close_odd"]),
            "mercado": mercado, "casa": casa, "lado": lado, "linha": line_num,
            "gid": gid, "gk": gk,
            "open_epoch": int(ots.timestamp()) if ots else None,
            "kickoff_epoch": int(kts.timestamp()) if kts else None,
        })

    # Headline, cuts, ROI and CIs use one independent unit per game+market.
    V = select_main_signals(strict_rows)
    n_valid_rows = len(strict_rows)
    n_valid = len(V)
    n_games = len({r["gid"] for r in V})
    n_settled = len(settled)
    head_ok = n_valid >= LIMIARES["head"]

    # General score over selected signals only; pushes stay neutral.
    settled_dec = [r for r in V if isinstance(r.get("won"), bool)]
    green_geral = (round(100 * sum(1 for r in settled_dec if r["won"]) / len(settled_dec), 1)
                   if settled_dec else None)

    # green quando bateu × não bateu o fechamento (a prova de valor do CLV) — com IC de Wilson,
    # só sobre decididas (push fora). A diferença só é "conclusiva" quando os IC não se sobrepõem.
    bk = LIMIARES["bucket"]
    bateu = [r for r in V if r["clv_pct"] is not None and r["clv_pct"] > 0 and isinstance(r.get("won"), bool)]
    nao = [r for r in V if r["clv_pct"] is not None and r["clv_pct"] <= 0 and isinstance(r.get("won"), bool)]
    gb_k, gn_k = sum(1 for r in bateu if r["won"]), sum(1 for r in nao if r["won"])
    green_bateu = round(100 * gb_k / len(bateu), 1) if len(bateu) >= bk else None
    green_nao = round(100 * gn_k / len(nao), 1) if len(nao) >= bk else None
    green_bateu_ci = list(wilson(gb_k, len(bateu))) if len(bateu) >= bk else [None, None]
    green_nao_ci = list(wilson(gn_k, len(nao))) if len(nao) >= bk else [None, None]
    green_diff_conclusiva = None
    if green_bateu_ci[0] is not None and green_nao_ci[0] is not None:
        green_diff_conclusiva = not (green_bateu_ci[0] <= green_nao_ci[1] and green_nao_ci[0] <= green_bateu_ci[1])

    a = agg(V) if head_ok else {"beat": None, "beat_ci": [None, None], "clv": None,
                                     "clv_med": None, "clv_ci": [None, None], "green_ci": [None, None]}
    ra_s = roi_stats(V, "open_odd") if head_ok else {"roi": None, "ci": [None, None], "n": 0, "n_push": 0}
    rf_s = roi_stats(V, "close_odd") if head_ok else {"roi": None, "ci": [None, None], "n": 0, "n_push": 0}
    ra, rf = ra_s["roi"], rf_s["roi"]

    head = {
        "n_valid": n_valid, "n_valid_rows": n_valid_rows, "n_games": n_games,
        "n_settled": n_settled, "n_settled_dec": len(settled_dec),
        "beat_close_rate": a["beat"], "beat_ci": a["beat_ci"],
        "clv_medio": a["clv"], "clv_mediana": a["clv_med"], "clv_ci": a["clv_ci"],
        "green_geral": green_geral, "green_geral_ci": a["green_ci"],
        "green_bateu": green_bateu, "green_nao": green_nao,
        "green_bateu_ci": green_bateu_ci, "green_nao_ci": green_nao_ci,
        "green_diff_conclusiva": green_diff_conclusiva,
        "n_bateu": len(bateu), "n_nao": len(nao),
        "roi_abertura": ra, "roi_abertura_ci": ra_s["ci"],
        "roi_fechamento": rf, "roi_fechamento_ci": rf_s["ci"],
        "roi_n": ra_s["n"], "roi_pushes": ra_s["n_push"],
        "roi_delta": (round(ra - rf, 2) if (ra is not None and rf is not None) else None),
        "ci_unit": "signal_game_market",
        "selection_rule": "balanced_main_open_near_2_best_price",
        "em_formacao": n_valid < LIMIARES["head"],
        "limiar_clv": LIMIARES["head"],
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

    # --- tabela de liquidadas ---
    liquidadas = []
    for k, v in settled:
        casa, gid, gk, mercado, linha, lado = row_ids(k, v)
        ots = ensure_aware(parse_ts(v.get("open_ts")))
        cts = ensure_aware(parse_ts(v.get("close_ts")))
        kts = ensure_aware(parse_ts(v.get("kickoff")))
        q_raw = v.get("capture_quality") or compute_capture_quality(v)
        clv_valido = is_strict_clv(v)
        clv_invalid_reason = strict_clv_reason(v)
        q_out = q_raw.get("band") if isinstance(q_raw, dict) else q_raw
        clv_out = v.get("clv_pct")
        if clv_valido and clv_out is None:
            clv_out = round((float(v["open_odd"]) / float(v["close_odd"]) - 1.0) * 100.0, 4)
        home = v.get("home_raw") or v.get("home_norm") or ""
        away = v.get("away_raw") or v.get("away_norm") or ""
        liquidadas.append({
            "gk": gk, "gid": gid,
            "jogo": f"{home} x {away}".strip(" x"),
            "data": (v.get("kickoff") or "")[:10], "casa": casa, "mercado": mercado,
            "linha": float(linha), "lado": LADO_PT.get(lado, lado),
            "open": v.get("open_odd"), "close": v.get("close_odd"),
            "clv": clv_out, "beat": v.get("beat_close"),
            "result": v.get("result"), "won": v.get("won"),
            "push": bool(v.get("won") is None and v.get("result") is not None
                         and abs(float(v.get("result")) - float(linha)) < 1e-9),
            "n_moves": v.get("n_moves", 0), "n_obs": v.get("n_obs", 0),
            "kickoff": v.get("kickoff"),
            "kickoff_epoch": int(kts.timestamp()) if kts else None,
            "open_epoch": int(ots.timestamp()) if ots else None,
            "close_epoch": int(cts.timestamp()) if cts else None,
            "clv_valido": clv_valido, "clv_invalid_reason": clv_invalid_reason,
            "quality": q_out,
            "sofa_id": v.get("sofa_id"), "match_method": v.get("match_method"),
        })
    liquidadas.sort(key=lambda x: x.get("kickoff_epoch") or 0, reverse=True)
    liquidadas_total = len(liquidadas)
    liquidadas_limit = 300
    liquidadas = liquidadas[:liquidadas_limit]

    # --- abertas (todas) p/ explorador; aba Abertas filtra n_moves no JS ---
    abertas = []
    for k, v in keys.items():
        if v.get("status") not in ("open", "closed"):
            continue
        if merc_of(k) not in BOARD6:
            continue
        casa, gid, gk, mercado, linha, lado = row_ids(k, v)
        op, last = v.get("open_odd"), v.get("last_odd")
        drift = round((last / op - 1) * 100, 2) if (op and last) else None
        q = v.get("capture_quality") or compute_capture_quality(v)
        home = v.get("home_raw") or v.get("home_norm") or ""
        away = v.get("away_raw") or v.get("away_norm") or ""
        abertas.append({
            "gk": gk, "gid": gid,
            "jogo": f"{home} x {away}".strip(" x"),
            "data": (v.get("kickoff") or "")[:10], "casa": casa, "mercado": mercado,
            "linha": float(linha), "lado": LADO_PT.get(lado, lado),
            "open": op, "last": last, "min": v.get("min_odd"), "max": v.get("max_odd"),
            "drift_pct": drift, "n_moves": v.get("n_moves", 0), "kickoff": v.get("kickoff"),
            "kickoff_epoch": int(ensure_aware(parse_ts(v.get("kickoff"))).timestamp())
                              if ensure_aware(parse_ts(v.get("kickoff"))) else None,
            "quality": q,
            "sofa_id": v.get("sofa_id"), "match_method": v.get("match_method"),
        })
    abertas.sort(key=lambda x: (x.get("kickoff_epoch") or 0, -(x.get("n_moves") or 0)))

    # banner no MESMO universo das métricas (BOARD6), pra não superestimar o tamanho do banco
    b6 = {k: v for k, v in keys.items() if merc_of(k) in BOARD6}
    q_counts = {}
    for v in b6.values():
        q = v.get("capture_quality") or compute_capture_quality(v)
        q = q.get("band") if isinstance(q, dict) else q
        q_counts[q] = q_counts.get(q, 0) + 1
    banco = {
        "monitoradas": len(b6),
        "liquidadas": n_settled,
        "clv_validas": n_valid_rows,
        "sinais_clv": n_valid,
        "jogos_clv": n_games,
        "moveu_pct": round(100 * sum(1 for v in b6.values() if (v.get("n_moves") or 0) >= 1) / len(b6), 1) if b6 else 0.0,
        "quality": q_counts,
    }

    now_brt = datetime.now(BRT)
    out = {
        "gerado": now_brt.strftime("%Y-%m-%d %H:%M"),
        "gerado_iso": now_brt.isoformat(timespec="seconds"),
        "limiares": LIMIARES,
        "banco": banco,
        "head": head,
        "recortes": recortes,
        "liquidadas": liquidadas,
        "liquidadas_total": liquidadas_total,
        "liquidadas_limit": liquidadas_limit,
        "abertas": abertas,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.HIST=" + json.dumps(out, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"[history] banco {banco['monitoradas']}/{banco['liquidadas']}/{banco['clv_validas']} · "
          f"{len(liquidadas)} na tabela · {len(abertas)} abertas c/ movimento · green_geral {green_geral} · -> {OUT.name}")


if __name__ == "__main__":
    main()
