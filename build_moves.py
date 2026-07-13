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
sys.path.insert(0, str(ROOT))
from canonical import parse_history_key

OUT = ROOT / "valor" / "data" / "moves.js"
BOARD_M = {"Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta",
           "Escanteios", "Chutes no gol", "Desarmes"}


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
            if t.get("mercado") not in BOARD_M:
                continue
            tm = ts_min(t.get("ts") or "")
            odd = t.get("odd")
            if tm is None or not odd:
                continue
            djogo = t.get("djogo") or (t.get("kickoff") or "")[:10]
            sid = t.get("sofa_id")
            if sid:
                gid = f"sofa:{sid}"
            else:
                gid = f'{djogo}|{t.get("home")}|{t.get("away")}'
            gk = f'{gid}|{t.get("mercado")}|{t.get("linha")}|{t.get("lado")}'
            series.setdefault(gk, {}).setdefault(t.get("casa"), []).append([tm, float(odd)])
            ko = ts_min(t.get("kickoff") or "")
            if ko: kicks[gk] = ko
            n_ticks += 1

    # enriquece com open→close das keys (mesmo sem tick intermediário = 2 pontos pro gráfico)
    for f in sorted(glob.glob(str(ROOT / "data/odds_history/keys/*.json"))):
        try:
            keys = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in keys.items():
            if k.startswith("__") or not isinstance(v, dict):
                continue
            meta = parse_history_key(k)
            if meta.get("format") == "unknown":
                continue
            casa = meta.get("casa") or k.split("|")[0]
            merc = meta.get("mercado")
            linha = meta.get("linha")
            lado = meta.get("lado")
            if merc not in BOARD_M or linha is None or not lado:
                continue
            o, c = v.get("open_odd"), v.get("close_odd") or v.get("last_odd")
            ot, ct = ts_min(v.get("open_ts") or ""), ts_min(v.get("close_ts") or v.get("last_ts") or "")
            if not o or not c or ot is None:
                continue
            if ct is None:
                ct = ot
            sid = v.get("sofa_id") or meta.get("sofa_id")
            djogo = (v.get("kickoff") or "")[:10] or meta.get("day") or ""
            hn = v.get("home_norm") or meta.get("hn") or ""
            an = v.get("away_norm") or meta.get("an") or ""
            if sid:
                gid = f"sofa:{sid}"
            else:
                gid = f"{djogo}|{hn}|{an}"
            gk = f"{gid}|{merc}|{linha}|{lado}"
            bucket = series.setdefault(gk, {}).setdefault(casa, [])
            # só injeta se a série ainda está vazia/curta (não sobrescreve ticks densos)
            if len(bucket) < 2:
                bucket.append([ot, float(o)])
                if ct != ot or float(c) != float(o):
                    bucket.append([ct, float(c)])
            ko = ts_min(v.get("kickoff") or "")
            if ko:
                kicks[gk] = ko

    # mantém linhas com ≥2 pontos em alguma casa (movimento ou open≠close) e ordena
    out = {}
    for gk, casas in series.items():
        cleaned = {}
        for c, pts in casas.items():
            # dedup por minuto (último odd do minuto)
            by_t = {}
            for t, o in sorted(pts):
                by_t[t] = o
            arr = [[t, by_t[t]] for t in sorted(by_t)]
            if arr:
                cleaned[c] = arr
        if not any(len(v) >= 2 for v in cleaned.values()):
            continue
        out[gk] = cleaned
        if gk in kicks:
            out[gk]["_ko"] = kicks[gk]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.MOVES=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";",
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"[moves] {n_ticks} ticks lidos · {len(out)} linhas com movimento → moves.js ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
