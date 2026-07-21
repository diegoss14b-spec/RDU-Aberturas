# -*- coding: utf-8 -*-
"""build_openclose.py — dataset ABERTURA × FECHAMENTO por jogo+mercado (μ implícito).

Pra cada jogo+mercado LIQUIDADO, congela o par (μ_open, μ_close) por casa e o
consenso (mediana entre casas com dado), junto com as odds cruas, a linha-main,
o resultado real e o lado vencedor da linha. Closing line é benchmark de ouro —
este arquivo alimenta modelos futuros.

Matemática do μ = a MESMA do gráfico do Histórico (valor/js/history.js):
  p_over_fair = (1/over) / (1/over + 1/under)      [de-vig do par O/U]
  μ resolve P(X > L) = p_over_fair sob Poisson      [bisseção, P(X>L)=1−CDF(⌊L⌋)]
A bisseção replica solveMu do JS (mesmos limites/tolerância) pros números baterem.

Honestidade dos endpoints:
  - open válido  = open_odd > 1 e open_ts  pré-kickoff (CLOSE_EPS)
  - close válido = close_odd > 1 e close_ts pré-kickoff (o close OFICIAL do
    history_close — última captura pré-jogo; ponta sem par O/U completo → null)

Saídas:
  data/odds/openclose/openclose.jsonl   (1 linha por jogo+mercado; rewrite idempotente)
  valor/data/openclose.js               (window.OPENCLOSE={...} pro front)
"""
import glob
import json
import math
import statistics
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import parse_history_key
from history_merge import merge_records
from migrate_history_keys import unify_keys_dict
from history_quality import ensure_aware, is_pre_kickoff, parse_ts, valid_decimal_odd

HIST = ROOT / "data" / "odds_history"
OUT_JSONL = ROOT / "data" / "odds" / "openclose" / "openclose.jsonl"
OUT_JS = ROOT / "valor" / "data" / "openclose.js"
BRT = timezone(timedelta(hours=-3))

BOARD_M = {"Cartões", "Faltas", "Finalizações", "Impedimentos", "Laterais", "Tiros de meta",
           "Escanteios", "Chutes no gol", "Desarmes"}


# ---------------------------------------------------------------- μ implícito
def poisson_cdf(k, mu):
    """Réplica exata do poissonCdf do JS (soma incremental, clamp em 1)."""
    if k < 0:
        return 0.0
    t = math.exp(-mu)
    s = t
    for i in range(1, int(k) + 1):
        t *= mu / i
        s += t
    return min(1.0, s)


def p_over_from_mu(L, mu):
    return 1.0 - poisson_cdf(math.floor(L), mu)


def solve_mu(L, p_over):
    """Réplica exata do solveMu do JS (bisseção, tolerância 5e-4)."""
    if not (0.0 < p_over < 1.0) or L is None or L < 0:
        return None
    lo, hi = 1e-6, max(2.0 * L + 10.0, 20.0)
    i = 0
    while i < 40 and p_over_from_mu(L, hi) < p_over:
        hi *= 1.5
        i += 1
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if p_over_from_mu(L, mid) < p_over:
            lo = mid
        else:
            hi = mid
        if hi - lo < 5e-4:
            break
    return (lo + hi) / 2.0


def mu_from_pair(line, over_odd, under_odd):
    """μ do par O/U (de-vig proporcional) — mesma conta do gráfico."""
    if not (valid_decimal_odd(over_odd) and valid_decimal_odd(under_odd)):
        return None
    p_fair = (1.0 / float(over_odd)) / (1.0 / float(over_odd) + 1.0 / float(under_odd))
    return solve_mu(float(line), p_fair)


# ------------------------------------------------- linhas de PARTIDA (não time)
def match_line_set(lines, mercado):
    """Porta fiel do matchLineSet do JS: descarta totais de time misturados."""
    arr = sorted(set(float(x) for x in lines))
    if len(arr) < 2:
        return arr
    max_l, min_l = arr[-1], arr[0]
    if mercado == "Finalizações" and max_l >= 16.5:
        return [L for L in arr if L >= 16.5]
    if mercado == "Faltas" and max_l >= 18:
        return [L for L in arr if L >= 16.5]
    if mercado == "Escanteios" and max_l >= 9 and max_l - min_l >= 4:
        return [L for L in arr if L >= 6.5]
    if mercado == "Chutes no gol" and max_l >= 8 and max_l - min_l >= 4:
        return [L for L in arr if L >= 5.5]
    if max_l - min_l >= 8:
        best_gap, cut = 0.0, -1
        for i in range(1, len(arr)):
            g = arr[i] - arr[i - 1]
            if g > best_gap:
                best_gap, cut = g, i
        if best_gap >= 4 and cut > 0:
            high = arr[cut:]
            if high[-1] >= 12:
                return high
    return arr


def line_key(value):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(val)) if val.is_integer() else str(val)


# ------------------------------------------------------------------- pipeline
def load_keys():
    keys = {}
    for f in sorted(glob.glob(str(HIST / "keys" / "*.json"))):
        try:
            raw = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[openclose] pulei {f}: {type(e).__name__}")
            continue
        for kk, vv in raw.items():
            if kk.startswith("__") or not isinstance(vv, dict):
                continue
            keys[kk] = merge_records(keys[kk], vv) if kk in keys else vv
    keys, _alias, _st = unify_keys_dict(keys)
    return keys


def build_alias_sofa(keys):
    alias = {}
    for kk, vv in keys.items():
        pp = parse_history_key(kk)
        sid = vv.get("sofa_id") or pp.get("sofa_id")
        if not sid:
            continue
        day = pp.get("day") or (vv.get("kickoff") or "")[:10]
        hn = vv.get("home_norm") or pp.get("hn") or ""
        an = vv.get("away_norm") or pp.get("an") or ""
        if day and hn and an:
            alias[(day, hn, an)] = str(sid)
            alias[(day, an, hn)] = str(sid)
    return alias


def gid_of(k, v, alias_sofa):
    p = parse_history_key(k)
    day = p.get("day") or (v.get("kickoff") or "")[:10]
    hn = v.get("home_norm") or p.get("hn") or ""
    an = v.get("away_norm") or p.get("an") or ""
    sid = v.get("sofa_id") or p.get("sofa_id") or alias_sofa.get((day, hn, an))
    return (f"sofa:{sid}" if sid else f"{day}|{hn}|{an}"), p


WINDOW_MIN = 5  # tolerância (min) pro instante da abertura/fechamento entre linhas


def pick_balanced(pairs, ref_line, endpoint):
    """pairs = {line: (over, under, minute|None)} → par mais equilibrado NO INSTANTE
    do endpoint (mesma escolha do gráfico): abertura = só pares da 1ª captura
    (±WINDOW_MIN), fechamento = só pares da última. Sem isso, uma linha de TIME
    congelada horas antes (gap menor) roubaria o lugar da linha de partida.
    Empate no gap: mais perto da ref, depois a menor linha."""
    if not pairs:
        return None
    minutes = [m for (_, _, m) in pairs.values() if m is not None]
    if minutes:
        if endpoint == "open":
            edge = min(minutes)
            keep = {L: p for L, p in pairs.items()
                    if p[2] is not None and p[2] <= edge + WINDOW_MIN}
        else:
            edge = max(minutes)
            keep = {L: p for L, p in pairs.items()
                    if p[2] is not None and p[2] >= edge - WINDOW_MIN}
        if keep:
            pairs = keep
    best = None
    for L, (o, u, _m) in pairs.items():
        gap = abs(float(o) - float(u))
        near = abs(L - ref_line) if ref_line is not None else 0.0
        score = (gap, near, L)
        if best is None or score < best[0]:
            best = (score, L, o, u)
    if best is None:
        return None
    return best[1], best[2], best[3]


def r2(x, nd=3):
    return None if x is None else round(float(x), nd)


def main():
    keys = load_keys()
    alias_sofa = build_alias_sofa(keys)

    # agrupa: (gid, mercado) -> casa -> line -> {"over": rec, "under": rec}
    groups = {}
    for k, v in keys.items():
        if v.get("status") != "settled":
            continue
        p = parse_history_key(k)
        mercado = p.get("mercado")
        if mercado not in BOARD_M:
            continue
        try:
            line = float(p.get("linha"))
        except (TypeError, ValueError):
            continue
        lado = p.get("lado") or "over"
        casa = p.get("casa") or k.split("|")[0]
        gid, _ = gid_of(k, v, alias_sofa)
        g = groups.setdefault((gid, mercado), {
            "casas": {}, "result": None, "kickoff": None, "jogo": None, "data": None,
        })
        g["casas"].setdefault(casa, {}).setdefault(line, {})[lado] = v
        if v.get("result") is not None and g["result"] is None:
            g["result"] = v.get("result")
        if v.get("kickoff") and (g["kickoff"] is None or v["kickoff"] < g["kickoff"]):
            g["kickoff"] = v["kickoff"]
        home = v.get("home_raw") or v.get("home_norm") or ""
        away = v.get("away_raw") or v.get("away_norm") or ""
        if home and away and not g["jogo"]:
            g["jogo"] = f"{home} x {away}"

    rows = []
    n_skip = 0
    for (gid, mercado), g in groups.items():
        try:
            row = build_row(gid, mercado, g)
        except Exception as e:
            # linha ruim nunca derruba o builder (guarda, como as casas)
            print(f"[openclose] erro em {gid}|{mercado}: {type(e).__name__}: {e}")
            row = None
        if row is None:
            n_skip += 1
            continue
        rows.append(row)

    rows.sort(key=lambda r: (r.get("kickoff_epoch") or 0, r["gid"], r["mercado"]),
              reverse=True)

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    now_brt = datetime.now(BRT)
    payload = {
        "gerado": now_brt.strftime("%Y-%m-%d %H:%M"),
        "gerado_iso": now_brt.isoformat(timespec="seconds"),
        "rows": rows,
    }
    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text("window.OPENCLOSE=" + json.dumps(payload, ensure_ascii=False,
                                                       separators=(",", ":")) + ";",
                      encoding="utf-8")
    n_both = sum(1 for r in rows if r["mu_open"] is not None and r["mu_close"] is not None)
    kb = OUT_JS.stat().st_size / 1024
    print(f"[openclose] {len(rows)} jogo+mercado liquidados ({n_both} com consenso "
          f"open E close) · {n_skip} sem par O/U algum · -> {OUT_JSONL.name} + "
          f"{OUT_JS.name} ({kb:.0f} KB)")


def build_row(gid, mercado, g):
    ko = g["kickoff"]
    kts = ensure_aware(parse_ts(ko))

    def pair_minute(rec_o, rec_u, field):
        """Minuto em que o par ficou completo (max dos 2 lados), como no gráfico."""
        ms = []
        for rec in (rec_o, rec_u):
            dt = ensure_aware(parse_ts(rec.get(field)))
            if dt is not None:
                ms.append(int(dt.timestamp() // 60))
        return max(ms) if len(ms) == 2 else None

    # pares válidos por casa/linha em cada ponta (open e close, honestos pré-KO)
    all_lines = set()
    open_pairs = {}    # casa -> {line: (o, u, minuto)}
    close_pairs = {}   # casa -> {line: (o, u, minuto)}
    for casa, by_line in g["casas"].items():
        for line, sides in by_line.items():
            all_lines.add(line)
            ov, un = sides.get("over"), sides.get("under")
            if ov and un:
                if (valid_decimal_odd(ov.get("open_odd")) and valid_decimal_odd(un.get("open_odd"))
                        and is_pre_kickoff(ov.get("open_ts"), ko)
                        and is_pre_kickoff(un.get("open_ts"), ko)):
                    open_pairs.setdefault(casa, {})[line] = (
                        float(ov["open_odd"]), float(un["open_odd"]),
                        pair_minute(ov, un, "open_ts"))
                if (valid_decimal_odd(ov.get("close_odd")) and valid_decimal_odd(un.get("close_odd"))
                        and is_pre_kickoff(ov.get("close_ts"), ko)
                        and is_pre_kickoff(un.get("close_ts"), ko)):
                    close_pairs.setdefault(casa, {})[line] = (
                        float(ov["close_odd"]), float(un["close_odd"]),
                        pair_minute(ov, un, "close_ts"))

    if not open_pairs and not close_pairs:
        return None    # nada de par O/U → sem μ possível, fora do dataset

    match_lines = set(match_line_set(all_lines, mercado))

    # linha-main: menor gap médio |o−u| na ABERTURA entre casas com par
    # (mesma regra do select_main_signals/markMainLines); fallback close; mediana.
    def main_line_from(pairs_by_casa):
        gaps = {}
        for casa, pairs in pairs_by_casa.items():
            for L, (o, u, _m) in pairs.items():
                if L in match_lines:
                    gaps.setdefault(L, []).append(abs(o - u))
        scores = [(statistics.fmean(v), L) for L, v in gaps.items()]
        return min(scores)[1] if scores else None

    linha_main = main_line_from(open_pairs)
    if linha_main is None:
        linha_main = main_line_from(close_pairs)
    if linha_main is None:
        ml = sorted(match_lines) or sorted(all_lines)
        linha_main = ml[len(ml) // 2]

    casas_out = {}
    mus_open, mus_close = [], []
    for casa in sorted(set(list(open_pairs) + list(close_pairs))):
        op = {L: p for L, p in (open_pairs.get(casa) or {}).items() if L in match_lines}
        cp = {L: p for L, p in (close_pairs.get(casa) or {}).items() if L in match_lines}
        entry = {"mu_open": None, "open_line": None, "open_over": None, "open_under": None,
                 "mu_close": None, "close_line": None, "close_over": None, "close_under": None,
                 "delta": None}
        pick = pick_balanced(op, linha_main, "open")
        if pick:
            L, o, u = pick
            mu = mu_from_pair(L, o, u)
            if mu is not None:
                entry.update(mu_open=r2(mu), open_line=L, open_over=o, open_under=u)
                mus_open.append(mu)
        pick = pick_balanced(cp, linha_main, "close")
        if pick:
            L, o, u = pick
            mu = mu_from_pair(L, o, u)
            if mu is not None:
                entry.update(mu_close=r2(mu), close_line=L, close_over=o, close_under=u)
                mus_close.append(mu)
        if entry["mu_open"] is not None and entry["mu_close"] is not None:
            entry["delta"] = r2(entry["mu_close"] - entry["mu_open"])
        if entry["mu_open"] is not None or entry["mu_close"] is not None:
            casas_out[casa] = entry

    if not casas_out:
        return None

    c_open = statistics.median(mus_open) if mus_open else None
    c_close = statistics.median(mus_close) if mus_close else None
    delta = (c_close - c_open) if (c_open is not None and c_close is not None) else None

    result = g["result"]
    try:
        result_num = float(result) if result is not None else None
    except (TypeError, ValueError):
        result_num = None
    lado = None
    if result_num is not None:
        if result_num > linha_main:
            lado = "over"
        elif result_num < linha_main:
            lado = "under"
        else:
            lado = "push"

    return {
        "gid": gid,
        "jogo": g["jogo"] or "",
        "data": (ko or "")[:10],
        "kickoff": ko,
        "kickoff_epoch": int(kts.timestamp()) if kts else None,
        "mercado": mercado,
        "linha": linha_main,
        "resultado": result_num,
        "lado": lado,
        "mu_open": r2(c_open),
        "mu_close": r2(c_close),
        "delta": r2(delta),
        "n_open": len(mus_open),
        "n_close": len(mus_close),
        "casas": casas_out,
    }


if __name__ == "__main__":
    main()
