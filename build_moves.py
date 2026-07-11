# -*- coding: utf-8 -*-
"""build_moves.py — monta as SÉRIES de movimentação das linhas pro gráfico da Mesa.
Lê data/odds_history/ticks/*.jsonl (1 tick = mudança de odd; o 1º tick da key é a abertura)
e agrupa por linha SEM a casa (gkey = data|home|away|mercado|linha|lado), com uma série
por casa dentro: window.MOVES = { gkey: { casa: [[epoch_min, odd], ...] } }.
Só linhas com movimento real (≥2 pontos em alguma casa) e mercados do board — controla tamanho.
Roda no workflow depois do build_history.py."""
import json, glob, sys
from pathlib import Path
from datetime import datetime
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "valor" / "data" / "moves.js"
BOARD6 = {"Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta"}


def ts_min(s):
    """ISO com tz -> epoch em MINUTOS (compacto pro JSON)."""
    try:
        return int(datetime.fromisoformat(s).timestamp() // 60)
    except Exception:
        return None


def main():
    series = {}   # gkey -> casa -> [[t,odd],...]
    kicks = {}    # gkey -> kickoff epoch_min (pro marco no gráfico)
    n_ticks = 0
    for f in sorted(glob.glob(str(ROOT / "data/odds_history/ticks/*.jsonl"))):
        for ln in open(f, encoding="utf-8"):
            try:
                t = json.loads(ln)
            except Exception:
                continue
            if t.get("mercado") not in BOARD6:
                continue
            tm = ts_min(t.get("ts") or "")
            odd = t.get("odd")
            if tm is None or not odd:
                continue
            djogo = (t.get("kickoff") or "")[:10]
            gk = f'{djogo}|{t.get("home")}|{t.get("away")}|{t.get("mercado")}|{t.get("linha")}|{t.get("lado")}'
            series.setdefault(gk, {}).setdefault(t.get("casa"), []).append([tm, odd])
            ko = ts_min(t.get("kickoff") or "")
            if ko: kicks[gk] = ko
            n_ticks += 1

    # mantém só linhas com movimento real (alguma casa com 2+ pontos) e ordena por tempo
    out = {}
    for gk, casas in series.items():
        if not any(len(v) >= 2 for v in casas.values()):
            continue
        out[gk] = {c: sorted(v) for c, v in casas.items()}
        if gk in kicks:
            out[gk]["_ko"] = kicks[gk]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.MOVES=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";",
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"[moves] {n_ticks} ticks lidos · {len(out)} linhas com movimento → moves.js ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
