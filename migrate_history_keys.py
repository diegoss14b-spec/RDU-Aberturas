# -*- coding: utf-8 -*-
"""migrate_history_keys.py — reprocessa keys legadas com sofa_id / normalização canônica.

Lê keys/{YYYY-MM}.json, resolve cada key legada via resolve_fixture, reescreve para
formato sofa: quando possível. Mescla duplicatas (mesma casa+sofa+mercado+linha+lado)
mantendo open mais cedo e last/max/min coerentes.
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import resolve_fixture, history_key, load_sofa_fixtures, parse_history_key, parse_start

HIST = ROOT / "data" / "odds_history" / "keys"
BRT = timezone(timedelta(hours=-3))


def merge_rec(a, b):
    """Mescla dois records da mesma key canônica."""
    out = dict(a)
    # open = o mais cedo
    ta, tb = a.get("open_ts") or "", b.get("open_ts") or ""
    if tb and (not ta or tb < ta):
        out["open_odd"] = b.get("open_odd")
        out["open_ts"] = b.get("open_ts")
        out["open_is_first_seen"] = b.get("open_is_first_seen", True)
    # last = o mais tarde
    la, lb = a.get("last_ts") or "", b.get("last_ts") or ""
    if lb and (not la or lb > la):
        out["last_odd"] = b.get("last_odd")
        out["last_ts"] = b.get("last_ts")
    for fld in ("max_odd",):
        vals = [x for x in (a.get(fld), b.get(fld)) if x]
        if vals:
            out[fld] = max(vals)
    for fld in ("min_odd",):
        vals = [x for x in (a.get(fld), b.get(fld)) if x]
        if vals:
            out[fld] = min(vals)
    out["n_obs"] = (a.get("n_obs") or 0) + (b.get("n_obs") or 0)
    out["n_moves"] = (a.get("n_moves") or 0) + (b.get("n_moves") or 0)
    # settled wins
    if b.get("status") == "settled" or a.get("status") == "settled":
        out["status"] = "settled"
        for f in ("result", "won", "clv_pct", "close_odd", "close_ts", "beat_close"):
            if b.get(f) is not None:
                out[f] = b[f]
            elif a.get(f) is not None:
                out[f] = a[f]
    if b.get("sofa_id"):
        out["sofa_id"] = b["sofa_id"]
        out["match_method"] = b.get("match_method") or out.get("match_method")
        out["match_confidence"] = b.get("match_confidence") or out.get("match_confidence")
    # raw names prefer longer/more informative
    for f in ("home_raw", "away_raw"):
        if len(str(b.get(f) or "")) > len(str(out.get(f) or "")):
            out[f] = b[f]
    return out


def migrate_file(path: Path, fixtures):
    keys = json.loads(path.read_text(encoding="utf-8"))
    new = {}
    n_sofa = n_leg = n_merge = 0
    for old_key, rec in keys.items():
        meta = parse_history_key(old_key)
        casa = meta.get("casa") or old_key.split("|")[0]
        mercado = meta.get("mercado")
        linha = meta.get("linha")
        lado = meta.get("lado")
        if not mercado or linha is None or not lado:
            new[old_key] = rec
            continue

        home_raw = rec.get("home_raw") or meta.get("hn") or ""
        away_raw = rec.get("away_raw") or meta.get("an") or ""
        kick = rec.get("kickoff")
        league = rec.get("league") or ""

        # se já é sofa key, só garante campos
        if meta.get("format") == "sofa":
            rec = dict(rec)
            rec["sofa_id"] = meta.get("sofa_id")
            nk = old_key
            n_sofa += 1
        else:
            idt = resolve_fixture(home_raw, away_raw, kick, league=league, fixtures=fixtures)
            day = idt["day"] if idt["day"] != "?" else (meta.get("day") or "")
            hn, an = idt["hn"], idt["an"]
            nk = history_key(casa, day, hn, an, mercado, linha, lado, sofa_id=idt.get("sofa_id"))
            rec = dict(rec)
            rec["home_norm"] = hn
            rec["away_norm"] = an
            if idt.get("sofa_id"):
                rec["sofa_id"] = idt["sofa_id"]
                rec["match_method"] = idt.get("match_method")
                rec["match_confidence"] = idt.get("match_confidence")
                n_sofa += 1
            else:
                n_leg += 1

        if nk in new:
            new[nk] = merge_rec(new[nk], rec)
            n_merge += 1
        else:
            new[nk] = rec

    bak = path.with_suffix(path.suffix + ".bak_pre_migrate")
    if not bak.exists():
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    print(f"[migrate] {path.name}: {len(keys)} → {len(new)} keys · sofa={n_sofa} · leg={n_leg} · merges={n_merge}")
    return len(new)


def main():
    fixtures = load_sofa_fixtures()
    print(f"[migrate] fixtures sofa={len(fixtures)}")
    files = sorted(HIST.glob("*.json"))
    if not files:
        print("[migrate] nenhum keys/*.json")
        return
    for f in files:
        if f.name.endswith(".bak_pre_migrate"):
            continue
        migrate_file(f, fixtures)


if __name__ == "__main__":
    main()
