# -*- coding: utf-8 -*-
"""build_ops.py — painel de Operação: saúde das capturas, cobertura e avisos.

Lê:
  data/odds/_status/{casa}.json + summary.json + history.jsonl
  valor/data/board.js (se existir) / data/odds/*_latest
  data/odds_history/keys + valor/data/history.js
  data/fixtures/sofa_latest.json

Escreve: valor/data/ops.js  →  window.OPS = {...};
Roda no workflow após captura/board (e localmente antes do deploy).
"""
from __future__ import annotations
import json, sys, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "data" / "odds" / "_status"
ODDS = ROOT / "data" / "odds"
OUT = ROOT / "valor" / "data" / "ops.js"
BRT = timezone(timedelta(hours=-3))

DISP = {
    "betano": "Betano", "superbet": "Superbet", "estrelabet": "EstrelaBet",
    "7k": "7k", "pinnacle": "Pinnacle", "bet365": "bet365", "sofa": "SofaScore",
}
CASAS = ["betano", "superbet", "estrelabet", "7k", "pinnacle", "bet365"]
MERCADOS = [
    "Cartões", "Faltas", "Finalizações", "Chutes no gol", "Escanteios",
    "Impedimentos", "Laterais", "Tiros de meta", "Desarmes",
]


def now_brt():
    return datetime.now(BRT)


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_js_window(path: Path, prefix: str):
    """window.X={...}; → dict."""
    if not path.exists():
        return None
    try:
        t = path.read_text(encoding="utf-8").strip()
        if not t.startswith(prefix):
            return None
        return json.loads(t[len(prefix):].rstrip(";"))
    except Exception:
        return None


def parse_ts_brt(s):
    if not s:
        return None
    raw = str(s).strip()
    try:
        if "T" in raw or raw.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", raw):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            return dt.astimezone(BRT)
        return datetime.strptime(raw[:16], "%Y-%m-%d %H:%M").replace(tzinfo=BRT)
    except Exception:
        return None

def age_mins(ts_brt_str, now=None):
    dt = parse_ts_brt(ts_brt_str)
    if not dt:
        return None
    now = now or now_brt()
    return max(0, int((now - dt).total_seconds() / 60))


def load_casa_status():
    rows = []
    for c in CASAS:
        st = load_json(STATUS / f"{c}.json") or {}
        rows.append({
            "id": c,
            "nome": DISP.get(c, c),
            "ok": bool(st.get("ok")),
            "n_events": st.get("n_events"),
            "n_markets": st.get("n_markets"),
            "market_counts": st.get("market_counts") or {},
            "pointer_valid": st.get("pointer_valid"),
            "min_events": st.get("min_events"),
            "duration_sec": st.get("duration_sec"),
            "ts_brt": st.get("ts_brt"),
            "error": (st.get("error") or None),
            "error_class": st.get("error_class"),
            "proxy_br": st.get("proxy_br"),
            "age_min": age_mins(st.get("ts_brt")),
        })
    # sofa fixture status (best-effort)
    sofa = load_json(STATUS / "sofa.json")
    if sofa:
        rows.append({
            "id": "sofa",
            "nome": "SofaScore",
            "ok": bool(sofa.get("ok")),
            "n_events": sofa.get("n_events") or sofa.get("n_fixtures"),
            "n_markets": None,
            "min_events": sofa.get("min_events"),
            "duration_sec": sofa.get("duration_sec"),
            "ts_brt": sofa.get("ts_brt"),
            "error": sofa.get("error"),
            "error_class": sofa.get("error_class"),
            "proxy_br": sofa.get("proxy_br"),
            "age_min": age_mins(sofa.get("ts_brt")),
            "kind": "fixture",
            "pointer_valid": sofa.get("pointer_valid"),
            "pointer_age_h": sofa.get("pointer_age_h"),
        })
    return rows


def load_runs(days=7, limit=40):
    hf = STATUS / "history.jsonl"
    if not hf.exists():
        return [], {}
    cut = (now_brt() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    runs, agg = [], defaultdict(lambda: {"ok": 0, "total": 0, "n_sum": 0, "n_cnt": 0})
    for ln in hf.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        ts = r.get("ts") or ""
        if ts < cut:
            continue
        runs.append(r)
        for c, v in (r.get("casas") or {}).items():
            a = agg[c]
            a["total"] += 1
            if v.get("ok"):
                a["ok"] += 1
            n = v.get("n")
            if isinstance(n, (int, float)) and n is not None:
                a["n_sum"] += n
                a["n_cnt"] += 1
    runs.sort(key=lambda x: x.get("ts") or "")
    hist7 = {}
    for c, a in agg.items():
        rate = round(100 * a["ok"] / a["total"], 1) if a["total"] else None
        avg_n = round(a["n_sum"] / a["n_cnt"], 1) if a["n_cnt"] else None
        hist7[DISP.get(c, c)] = {
            "ok": a["ok"], "total": a["total"], "rate": rate, "avg_n": avg_n,
        }
    # últimas N runs (mais recentes no fim do arquivo → pega o final)
    tail = runs[-limit:]
    slim = []
    for r in tail:
        slim.append({
            "ts": r.get("ts"),
            "total": r.get("total"),
            "casas": {
                DISP.get(c, c): {"ok": bool(v.get("ok")), "n": v.get("n")}
                for c, v in (r.get("casas") or {}).items()
            },
        })
    return slim, hist7


def board_coverage(board):
    if not board:
        return None
    jogos = board.get("jogos") or []
    por_mercado = {}
    n_sofa = 0
    n_valor = 0
    casas_board = set(board.get("casas") or [])
    for j in jogos:
        if j.get("sofa_id"):
            n_sofa += 1
        if j.get("valor"):
            n_valor += len(j["valor"])
        for m, per_casa in (j.get("mercados") or {}).items():
            slot = por_mercado.setdefault(m, {
                "jogos": 0, "casas": defaultdict(int), "linhas": 0, "multi_casa": 0,
            })
            slot["jogos"] += 1
            if isinstance(per_casa, dict):
                n_c = 0
                for casa, lines in per_casa.items():
                    slot["casas"][casa] += 1
                    n_c += 1
                    if isinstance(lines, list):
                        slot["linhas"] += len(lines)
                    elif isinstance(lines, dict):
                        slot["linhas"] += len(lines)
                if n_c >= 2:
                    slot["multi_casa"] += 1
        # times (linhas de time) contam cobertura extra de mercado
        for m, sides in (j.get("times") or {}).items():
            slot = por_mercado.setdefault(m, {
                "jogos": 0, "casas": defaultdict(int), "linhas": 0, "multi_casa": 0,
            })
            # não incrementa jogos de novo se já contou no match market
            for side in (sides or {}).values():
                for casa, lines in ((side or {}).get("casas") or {}).items():
                    slot["casas"][casa] += 0  # presença
                    if isinstance(lines, list):
                        slot["linhas"] += len(lines)

    # freeze defaultdicts
    out_m = {}
    for m in MERCADOS:
        if m not in por_mercado:
            continue
        s = por_mercado[m]
        out_m[m] = {
            "jogos": s["jogos"],
            "linhas": s["linhas"],
            "multi_casa": s["multi_casa"],
            "casas": dict(s["casas"]),
        }
    # mercados extras fora da ordem
    for m, s in por_mercado.items():
        if m not in out_m:
            out_m[m] = {
                "jogos": s["jogos"], "linhas": s["linhas"],
                "multi_casa": s["multi_casa"], "casas": dict(s["casas"]),
            }

    # próximos kickoffs (24h)
    now = now_brt()
    soon = []
    for j in jogos:
        ini = j.get("inicio") or ""
        # "dd/mm HH:MM"
        mo = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", ini)
        if not mo:
            continue
        try:
            dt = datetime(now.year, int(mo.group(2)), int(mo.group(1)),
                          int(mo.group(3)), int(mo.group(4)), tzinfo=BRT)
            if dt < now - timedelta(hours=6):
                dt = dt.replace(year=now.year + 1)
            mins = (dt - now).total_seconds() / 60
            if 0 <= mins <= 24 * 60:
                n_casas = len(j.get("casas") or [])
                if isinstance(j.get("casas"), dict):
                    n_casas = len(j["casas"])
                # count from mercados
                cs = set()
                for per in (j.get("mercados") or {}).values():
                    if isinstance(per, dict):
                        cs.update(per.keys())
                soon.append({
                    "jogo": j.get("jogo"),
                    "inicio": ini,
                    "mins": int(mins),
                    "n_mercados": j.get("n_mercados") or len(j.get("mercados") or {}),
                    "n_casas": len(cs) or n_casas,
                    "sofa": bool(j.get("sofa_id")),
                    "tem_valor": bool(j.get("tem_valor") or j.get("valor")),
                })
        except Exception:
            continue
    soon.sort(key=lambda x: x["mins"])

    return {
        "gerado": board.get("gerado"),
        "age_min": age_mins(board.get("gerado")),
        "n_jogos": len(jogos),
        "casas": list(board.get("casas") or sorted(casas_board)),
        "por_mercado": out_m,
        "sofa_matched": n_sofa,
        "sofa_pct": round(100 * n_sofa / len(jogos), 1) if jogos else 0,
        "valor_flags": n_valor,
        "proximos_24h": soon[:25],
        "fonte": board.get("fonte"),
    }


def count_line_moves(days=7):
    """Conta ticks kind=line_move nos últimos N dias."""
    ticks_dir = ROOT / "data" / "odds_history" / "ticks"
    if not ticks_dir.exists():
        return 0
    cut = (now_brt() - timedelta(days=days)).strftime("%Y-%m-%d")
    n = 0
    for f in sorted(ticks_dir.glob("*.jsonl")):
        if f.stem < cut:
            continue
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                try:
                    t = json.loads(ln)
                except Exception:
                    continue
                if t.get("kind") == "line_move":
                    n += 1
        except Exception:
            continue
    return n


def history_health():
    hist = load_js_window(ROOT / "valor" / "data" / "history.js", "window.HIST=")
    if not hist:
        return None
    banco = hist.get("banco") or {}
    head = hist.get("head") or {}
    n_valid = banco.get("clv_validas") if banco.get("clv_validas") is not None else head.get("n_valid")
    lim = (hist.get("limiares") or {}).get("head") or 30
    return {
        "gerado": hist.get("gerado"),
        "monitoradas": banco.get("monitoradas"),
        "liquidadas": banco.get("liquidadas"),
        "clv_validas": n_valid,
        "moveu_pct": banco.get("moveu_pct"),
        "green_geral": head.get("green_geral"),
        "n_abertas": len(hist.get("abertas") or []),
        "n_liquidadas_tab": len(hist.get("liquidadas") or []),
        "quality": banco.get("quality") or {},
        "line_moves_7d": count_line_moves(7),
        "clv_em_formacao": (n_valid or 0) < lim,
        "clv_limiar": lim,
    }


def settlement_health():
    raw = load_json(
        ROOT / "data" / "odds_history" / "results" / "settlement_status.json"
    )
    if not raw:
        return None
    markets = {}
    for market, row in (raw.get("by_market") or {}).items():
        statuses = row.get("status") or {}
        pending = statuses.get("pending_result") or 0
        unavailable = statuses.get("unavailable") or 0
        if pending or unavailable:
            markets[market] = {
                "total": row.get("total") or 0,
                "pending": pending,
                "unavailable": unavailable,
                "age": row.get("pending_age") or {},
                "reasons": row.get("pending_reasons") or {},
            }
    return {
        "generated_at": raw.get("generated_at"),
        "results_rows": raw.get("results_rows"),
        "totals_by_status": raw.get("totals_by_status") or {},
        "backlog": raw.get("backlog") or {},
        "by_market": markets,
        "result_field_coverage": raw.get("result_field_coverage") or {},
    }


def fixtures_info():
    ptr = ROOT / "data" / "fixtures" / "sofa_latest.json"
    meta = load_json(ptr) or {}
    file_name = meta.get("file")
    target_valid = False
    n = 0
    if file_name:
        data = load_json(ROOT / "data" / "fixtures" / file_name) or {}
        n = len(data.get("fixtures") or [])
        target_valid = n > 0 and n == int(meta.get("n") or 0)
    ts = meta.get("at") or meta.get("ts") or meta.get("ts_brt") or meta.get("updated")
    return {
        "file": file_name,
        "n_fixtures": n,
        "ts": ts,
        "target_valid": target_valid,
        "age_min": age_mins(ts),
    }
def build_avisos(summary, casas, board_cov, hist_h, runs):
    avisos = []
    fails = [c for c in casas if c["id"] in CASAS and not c["ok"]]
    if fails:
        avisos.append({
            "level": "warn" if len(fails) <= 2 else "bad",
            "txt": "Captura parcial: " + ", ".join(
                f'{c["nome"]}' + (f' ({c["error"]})' if c.get("error") else "")
                for c in fails
            ),
        })
    if summary and not summary.get("deploy_allowed", True):
        avisos.append({
            "level": "bad",
            "txt": f"Deploy bloqueado na última captura: {summary.get('reason') or 'insuficiente'}",
        })
    if board_cov:
        age = board_cov.get("age_min")
        if age is not None and age > 120:
            avisos.append({
                "level": "bad",
                "txt": f"Mesa defasada: atualizada há {age} min (>2h)",
            })
        elif age is not None and age > 90:
            avisos.append({
                "level": "warn",
                "txt": f"Mesa com {age} min — pode estar desatualizada",
            })
        if board_cov.get("sofa_pct", 100) < 40 and board_cov.get("n_jogos", 0) >= 10:
            avisos.append({
                "level": "warn",
                "txt": f"Só {board_cov['sofa_pct']}% dos jogos casados com SofaScore — nomes/merge mais frágeis",
            })
        # mercado dominante
        pm = board_cov.get("por_mercado") or {}
        if pm:
            top = max(pm.items(), key=lambda x: x[1].get("jogos") or 0)
            total_j = board_cov.get("n_jogos") or 1
            if top[1].get("jogos", 0) >= total_j * 0.7 and len(pm) >= 2:
                avisos.append({
                    "level": "info",
                    "txt": f"Cobertura concentrada em {top[0]} ({top[1]['jogos']} jogos) — outros mercados mais finos",
                })
    if hist_h:
        clv = hist_h.get("clv_validas") or 0
        liq = hist_h.get("liquidadas") or 0
        if liq and clv == 0:
            avisos.append({
                "level": "info",
                "txt": f"Histórico em formação: {liq} liquidadas, 0 CLV pré-jogo válido ainda",
            })
    # casa com taxa 7d baixa
    # (passado via runs hist7 no main)
    return avisos


def main():
    summary = load_json(STATUS / "summary.json") or {}
    casas = load_casa_status()
    runs, hist7 = load_runs(days=7, limit=36)

    board = load_js_window(ROOT / "valor" / "data" / "board.js", "window.BOARD=")
    board_cov = board_coverage(board)
    hist_h = history_health()
    settle_h = settlement_health()
    fx = fixtures_info()

    avisos = build_avisos(summary, casas, board_cov, hist_h, runs)
    if settle_h:
        age = (settle_h.get("backlog") or {}).get("age") or {}
        stale = (age.get("7-30d") or 0) + (age.get("30d+") or 0)
        if stale:
            avisos.append({
                "level": "warn",
                "txt": f"Liquidação atrasada: {stale} keys aguardam resultado há 7+ dias",
            })
    for nome, h in (hist7 or {}).items():
        if h.get("total", 0) >= 3 and (h.get("rate") or 100) < 70:
            avisos.append({
                "level": "warn",
                "txt": f"{nome}: confiabilidade 7d em {h['rate']}% ({h['ok']}/{h['total']} ok)",
            })

    # heatmap casas × rodadas (últimas 14 runs) — compacto p/ sparkline
    heat = {"casas": [DISP[c] for c in CASAS], "cols": []}
    for r in runs[-14:]:
        col = {"ts": (r.get("ts") or "")[5:16], "cells": []}  # mm-dd HH:MM
        cmap = r.get("casas") or {}
        for c in CASAS:
            nome = DISP[c]
            v = cmap.get(nome) or cmap.get(c) or {}
            # history.jsonl usa ids; slim runs usam nomes
            if not v and isinstance(r.get("casas"), dict):
                # raw runs before slim? runs from load_runs are already slim with DISP names
                pass
            ok = v.get("ok") if v else None
            n = v.get("n") if v else None
            col["cells"].append({"ok": ok, "n": n})
        heat["cols"].append(col)

    # contadores head
    n_ok = sum(1 for c in casas if c["id"] in CASAS and c["ok"])
    n_fail = sum(1 for c in casas if c["id"] in CASAS and not c["ok"])
    total_ev = sum((c.get("n_events") or 0) for c in casas if c["id"] in CASAS and c["ok"])

    out = {
        "gerado": now_brt().strftime("%Y-%m-%d %H:%M"),
        "summary": {
            "ts_brt": summary.get("ts_brt"),
            "age_min": age_mins(summary.get("ts_brt")),
            "n_ok": summary.get("n_ok", n_ok),
            "n_fail": summary.get("n_fail", n_fail),
            "total_events": summary.get("total_events", total_ev),
            "deploy_allowed": summary.get("deploy_allowed"),
            "reason": summary.get("reason"),
        },
        "casas": casas,
        "hist7": hist7,
        "runs": runs[-24:],
        "heat": heat,
        "board": board_cov,
        "historico": hist_h,
        "liquidacao": settle_h,
        "fixtures": fx,
        "avisos": avisos,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "window.OPS=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";",
        encoding="utf-8",
    )
    kb = OUT.stat().st_size / 1024
    print(
        f"[ops] casas {n_ok} ok / {n_fail} fail · runs7={len(runs)} · "
        f"avisos={len(avisos)} · board_jogos={(board_cov or {}).get('n_jogos')} → ops.js ({kb:.0f} KB)"
    )


if __name__ == "__main__":
    main()
