# -*- coding: utf-8 -*-
"""Build pre-match movement series for the History & CLV explorer.

Every plotted tick is strictly before kickoff. A real observed close is stored
separately from kickoff so the UI never labels a synthetic KO point as close.
Legacy identities are remapped to a known Sofa id by exact day/team aliases.
"""
import glob
import json
import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import norm_team, parse_history_key
from history_quality import CLOSE_EPS, ensure_aware, parse_ts

OUT = ROOT / "valor" / "data" / "moves.js"
BOARD_M = {"Cart\u00f5es", "Faltas", "Finaliza\u00e7\u00f5es", "Impedimentos", "Laterais", "Tiros de meta",
           "Escanteios", "Chutes no gol", "Desarmes"}


def parsed(value):
    return ensure_aware(parse_ts(value))


def epoch_min(dt):
    return int(dt.timestamp() // 60) if dt else None


def is_valid_odd(value):
    try:
        odd = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(odd) and odd > 1.0


def is_prematch(dt, kickoff):
    return bool(dt and (not kickoff or dt < kickoff - CLOSE_EPS))


def line_key(value):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(val)) if val.is_integer() else str(val)


def load_key_docs():
    docs = []
    for path in sorted(glob.glob(str(ROOT / "data/odds_history/keys/*.json"))):
        try:
            docs.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except Exception:
            continue
    return docs


def identity(meta, rec, aliases):
    day = meta.get("day") or (rec.get("kickoff") or "")[:10]
    hn = norm_team(rec.get("home_norm") or rec.get("home_raw") or meta.get("hn") or "")
    an = norm_team(rec.get("away_norm") or rec.get("away_raw") or meta.get("an") or "")
    sid = rec.get("sofa_id") or meta.get("sofa_id") or aliases.get((day, hn, an))
    return (f"sofa:{sid}" if sid else f"{day}|{hn}|{an}"), day, hn, an


def main():
    key_docs = load_key_docs()
    aliases = {}
    for keys in key_docs:
        for key, rec in keys.items():
            if key.startswith("__") or not isinstance(rec, dict):
                continue
            meta = parse_history_key(key)
            sid = rec.get("sofa_id") or meta.get("sofa_id")
            if not sid:
                continue
            day = meta.get("day") or (rec.get("kickoff") or "")[:10]
            hn = norm_team(rec.get("home_norm") or rec.get("home_raw") or meta.get("hn") or "")
            an = norm_team(rec.get("away_norm") or rec.get("away_raw") or meta.get("an") or "")
            if day and hn and an:
                aliases[(day, hn, an)] = str(sid)
                aliases[(day, an, hn)] = str(sid)

    series = {}
    kicks = {}
    closes = {}
    last_pre = {}
    n_ticks = 0
    n_post = 0

    for path in sorted(glob.glob(str(ROOT / "data/odds_history/ticks/*.jsonl"))):
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                try:
                    tick = json.loads(raw)
                except Exception:
                    continue
                if tick.get("mercado") not in BOARD_M or not is_valid_odd(tick.get("odd")):
                    continue
                dt = parsed(tick.get("ts"))
                ko = parsed(tick.get("kickoff"))
                if not is_prematch(dt, ko):
                    n_post += 1
                    continue
                day = tick.get("djogo") or (tick.get("kickoff") or "")[:10]
                hn = norm_team(tick.get("home") or "")
                an = norm_team(tick.get("away") or "")
                sid = tick.get("sofa_id") or aliases.get((day, hn, an))
                gid = f"sofa:{sid}" if sid else f"{day}|{hn}|{an}"
                gk = f"{gid}|{tick.get('mercado')}|{line_key(tick.get('linha'))}|{tick.get('lado')}"
                tm = epoch_min(dt)
                if tm is None:
                    continue
                series.setdefault(gk, {}).setdefault(tick.get("casa"), []).append(
                    [tm, float(tick["odd"])]
                )
                if ko:
                    kicks[gk] = epoch_min(ko)
                n_ticks += 1

    # Enrich with exact open/close records. close_ts is real close; last_ts is only last seen.
    for keys in key_docs:
        for key, rec in keys.items():
            if key.startswith("__") or not isinstance(rec, dict):
                continue
            meta = parse_history_key(key)
            market = meta.get("mercado")
            line = meta.get("linha")
            side = meta.get("lado")
            if market not in BOARD_M or line is None or not side:
                continue
            house = meta.get("casa") or key.split("|")[0]
            ko = parsed(rec.get("kickoff"))
            ot = parsed(rec.get("open_ts"))
            if not is_valid_odd(rec.get("open_odd")) or not is_prematch(ot, ko):
                continue
            gid, _, _, _ = identity(meta, rec, aliases)
            gk = f"{gid}|{market}|{line_key(line)}|{side}"
            bucket = series.setdefault(gk, {}).setdefault(house, [])
            bucket.append([epoch_min(ot), float(rec["open_odd"])])

            ct = parsed(rec.get("close_ts"))
            if is_valid_odd(rec.get("close_odd")) and is_prematch(ct, ko):
                bucket.append([epoch_min(ct), float(rec["close_odd"])])
                closes.setdefault(gk, {})[house] = epoch_min(ct)
            else:
                lt = parsed(rec.get("last_ts"))
                if is_valid_odd(rec.get("last_odd")) and is_prematch(lt, ko):
                    bucket.append([epoch_min(lt), float(rec["last_odd"])])
                    last_pre.setdefault(gk, {})[house] = epoch_min(lt)
            if ko:
                kicks[gk] = epoch_min(ko)

    out = {}
    for gk, houses in series.items():
        cleaned = {}
        ko = kicks.get(gk)
        for house, points in houses.items():
            by_t = {}
            for minute, odd in sorted(points):
                if minute is None or (ko is not None and minute >= ko):
                    continue
                by_t[int(minute)] = float(odd)
            arr = [[minute, by_t[minute]] for minute in sorted(by_t)]
            if arr:
                cleaned[house] = arr
        if not any(len(points) >= 2 for points in cleaned.values()):
            continue
        if ko is not None:
            cleaned["_ko"] = ko
        if closes.get(gk):
            cleaned["_close"] = closes[gk]
        if last_pre.get(gk):
            cleaned["_last_pre"] = last_pre[gk]
        out[gk] = cleaned

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.MOVES=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";",
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"[moves] {n_ticks} pre-KO ticks; {n_post} post-KO rejected; "
          f"{len(out)} lines -> moves.js ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
