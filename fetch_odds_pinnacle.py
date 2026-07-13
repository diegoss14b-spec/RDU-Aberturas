# -*- coding: utf-8 -*-
"""fetch_odds_pinnacle.py — Escanteios + Cartões (Bookings) via API guest Arcadia
(gringa: guest.api.arcadia.pinnacle.com — NÃO é pinnacle.bet.br).

Mecanismo (validado 13–14/07/2026, caso França×Espanha):
  - matchups futebol: /sports/29/matchups?withSpecials=true
  - specials de volume = matchup FILHO type=matchup com parentId = jogo principal
      units='Corners'  → Escanteios  (ex. France (Corners) / Spain (Corners))
      units='Bookings' → Cartões     (ex. France (Bookings) / Spain (Bookings))
    ⚠ type=special + units=Bookings = props de JOGADOR (ignorar)
  - odds: /matchups/{id}/markets/straight  (period 0 = FT)
      s;0;ou;{linha}      → total partida
      s;0;tt;{linha};home → total mandante
      s;0;tt;{linha};away → total visitante
  - preços AMERICANOS → decimal
  - Bookings: linhas ~2.5–4.5 = contagem de cartões (NÃO booking points 10/25)

Saída: 1 linha JSONL por JOGO (parent), mesclando Corners+Bookings em mercados.
"""
import sys, os, json, re, time, random
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"):
    sys.stderr = open(os.devnull, "w")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
except Exception:
    pass
import requests
from capture_common import odds_window, in_window, finish

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "odds"
OUTDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
SPORT = 29  # Soccer
H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.pinnacle.com",
    "Referer": "https://www.pinnacle.com/",
}
MIN_EVENTS = 3
MIN_EFF = MIN_EVENTS
MAX_SPECIALS = 100  # teto de filhos corners/bookings por rodada

# units Arcadia → mercado canônico do board
UNIT_CANON = {
    "corners": "Escanteios",
    "bookings": "Cartões",
    "cards": "Cartões",
}


def am_to_dec(p):
    """American odds (±100+) → decimal."""
    try:
        p = float(p)
    except Exception:
        return None
    if p >= 100:
        return round(1 + p / 100.0, 2)
    if p <= -100:
        return round(1 + 100.0 / abs(p), 2)
    return None


def get(path, tries=3):
    url = BASE + path if path.startswith("/") else path
    for a in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=30)
            if r.status_code == 200 and r.text and r.text[:1] in "[{":
                return r.json()
            if r.status_code == 204:
                return []
            if r.status_code in (401, 403, 429):
                time.sleep(1.5 * (a + 1))
                continue
        except Exception:
            pass
        time.sleep(1.0)
    return None


def _parts_names(m):
    parts = m.get("participants") or []
    home = away = None
    for p in parts:
        al = (p.get("alignment") or "").lower()
        nm = (p.get("name") or "").strip()
        if al == "home":
            home = nm
        elif al == "away":
            away = nm
    if home is None or away is None:
        ordered = sorted(parts, key=lambda p: (p.get("order") is None, p.get("order") or 0))
        names = [(p.get("name") or "").strip() for p in ordered if p.get("name")]
        if len(names) >= 2:
            home = home or names[0]
            away = away or names[1]
    return home or "", away or ""


def _strip_unit_suffix(name, unit=None):
    """'France (Bookings)' / 'Spain (Corners)' → nome limpo."""
    n = (name or "").strip()
    for u in ("Corners", "Bookings", "Cards", "Corner", "Booking", "Card"):
        n = re.sub(rf"\s*\({re.escape(u)}\)\s*$", "", n, flags=re.I)
    if unit:
        n = re.sub(rf"\s*\({re.escape(unit)}\)\s*$", "", n, flags=re.I)
    return n.strip()


def parse_markets(mkts, period=0):
    """Extrai O/U jogo + team totals do period (0=FT).
    -> (match_lines[{linha,over,under}], home_lines, away_lines)"""
    match, home, away = {}, {}, {}
    for m in mkts or []:
        if m.get("status") != "open":
            continue
        try:
            per = int(m.get("period")) if m.get("period") is not None else 0
        except Exception:
            per = 0
        if per != period:
            continue
        typ = (m.get("type") or "").lower()
        # só totais (ignora moneyline/spread de "mais cartões")
        if typ not in ("total", "team_total"):
            continue
        prices = m.get("prices") or []
        over = under = None
        line = None
        for p in prices:
            des = (p.get("designation") or "").lower()
            dec = am_to_dec(p.get("price"))
            if dec is None or dec <= 1:
                continue
            if line is None and p.get("points") is not None:
                try:
                    line = float(p["points"])
                except Exception:
                    pass
            if des == "over":
                over = dec
            elif des == "under":
                under = dec
        if line is None or not over or not under:
            continue
        row = {"linha": line, "over": over, "under": under}
        if typ == "total":
            match[line] = row
        elif typ == "team_total":
            side = (m.get("side") or "").lower()
            key = m.get("key") or ""
            if not side and key.endswith(";home"):
                side = "home"
            if not side and key.endswith(";away"):
                side = "away"
            if side == "home":
                home[line] = row
            elif side == "away":
                away[line] = row

    def _arr(d):
        return [d[L] for L in sorted(d)]

    return _arr(match), _arr(home), _arr(away)


def main():
    global MIN_EFF
    now = datetime.now(BRT)
    mups = get(f"/sports/{SPORT}/matchups?withSpecials=true&brandId=0") or []
    print(f"[pinnacle] matchups: {len(mups)}")
    if not mups:
        print("[pinnacle] sem matchups (API guest vazia/bloqueada)")
        return 0

    by_id = {m["id"]: m for m in mups if m.get("id")}

    # só matchup-filho O/U (NÃO specials de jogador)
    specials = []
    n_skip_special = 0
    for m in mups:
        units = (m.get("units") or "").strip()
        ul = units.lower()
        if ul not in UNIT_CANON:
            continue
        if not m.get("parentId"):
            continue
        # props de elenco: type=special (ex. "Rodri / Gavi / …" Bookings)
        if (m.get("type") or "").lower() != "matchup":
            n_skip_special += 1
            continue
        specials.append(m)

    _wh = odds_window()
    if _wh is not None:
        _tot = len(specials)
        specials = [m for m in specials if in_window(m.get("startTime"), _wh)]
        MIN_EFF = min(MIN_EVENTS, 1) if specials else 0
        print(f"[pinnacle] modo close: janela {_wh:g}h -> {len(specials)} de {_tot} specials")

    # prioriza bookings (poucos) + quem tem mais mercados; nunca cortar o único Bookings
    def _prio(m):
        ul = (m.get("units") or "").lower()
        book_boost = 1000 if ul in ("bookings", "cards") else 0
        return -(book_boost + (m.get("totalMarketCount") or 0))

    specials.sort(key=_prio)
    specials = specials[:MAX_SPECIALS]
    n_corners = sum(1 for m in specials if (m.get("units") or "").lower() == "corners")
    n_books = sum(1 for m in specials if (m.get("units") or "").lower() in ("bookings", "cards"))
    print(
        f"[pinnacle] filhos O/U: {len(specials)} "
        f"(corners={n_corners} bookings={n_books} · skip specials/props={n_skip_special})"
    )

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"pinnacle_{stamp}.jsonl"
    from capture_common import write_odds_latest

    def write_latest(n, promote=False):
        write_odds_latest(
            "pinnacle", out_path.name, n,
            at=now.isoformat(timespec="seconds"), promote_full=promote,
        )

    write_latest(0, promote=False)

    # parent_id → rec acumulado (Corners + Bookings no mesmo jogo)
    by_parent = {}
    n_fetch_ok = 0

    for m in specials:
        mid = m["id"]
        units = (m.get("units") or "").strip()
        canon = UNIT_CANON[units.lower()]
        pid = m.get("parentId")
        parent = by_id.get(pid) or {}

        if parent:
            ph, pa = _parts_names(parent)
            league = (parent.get("league") or {}).get("name") or (m.get("league") or {}).get("name") or ""
            start = parent.get("startTime") or m.get("startTime")
        else:
            ph, pa = _parts_names(m)
            ph, pa = _strip_unit_suffix(ph, units), _strip_unit_suffix(pa, units)
            league = (m.get("league") or {}).get("name") or ""
            start = m.get("startTime")
        ph = _strip_unit_suffix(ph, units) or ph
        pa = _strip_unit_suffix(pa, units) or pa
        if not ph or not pa:
            continue

        mkts = get(f"/matchups/{mid}/markets/straight")
        time.sleep(random.uniform(0.12, 0.28))
        if not mkts:
            continue
        match_lines, home_lines, away_lines = parse_markets(mkts, period=0)
        if not match_lines and not home_lines and not away_lines:
            continue
        n_fetch_ok += 1

        key = pid or mid
        rec = by_parent.get(key)
        if rec is None:
            league_clean = (
                league.replace(" Corners", "")
                .replace(" Bookings", "")
                .replace(" Cards", "")
                .strip()
            )
            rec = {
                "casa": "Pinnacle",
                "event_id": mid,
                "parent_id": pid,
                "name": f"{ph} - {pa}",
                "league": league_clean,
                "start": start,
                "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "mercados": {},
                "units_seen": [],
                "child_ids": {},
            }
            by_parent[key] = rec

        rec["child_ids"][canon] = mid
        rec["units_seen"].append(units)
        if match_lines:
            rec["mercados"][canon] = match_lines
        if home_lines or away_lines:
            merc_t = rec.setdefault("mercados_time", {})
            by_team = merc_t.setdefault(canon, {})
            if home_lines:
                by_team[ph] = home_lines
            if away_lines:
                by_team[pa] = away_lines

    # grava 1 linha por jogo
    n_out = 0
    n_with_cards = 0
    n_with_corners = 0
    with out_path.open("w", encoding="utf-8") as f:
        for rec in by_parent.values():
            if not rec.get("mercados") and not rec.get("mercados_time"):
                continue
            # serialização limpa
            out = {
                "casa": rec["casa"],
                "event_id": rec["event_id"],
                "parent_id": rec.get("parent_id"),
                "name": rec["name"],
                "league": rec["league"],
                "start": rec["start"],
                "captured_at": rec["captured_at"],
                "mercados": rec["mercados"],
                "units": "+".join(sorted(set(rec.get("units_seen") or []))) or None,
                "child_ids": rec.get("child_ids") or {},
            }
            if rec.get("mercados_time"):
                out["mercados_time"] = rec["mercados_time"]
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
            n_out += 1
            if "Cartões" in rec["mercados"] or "Cartões" in (rec.get("mercados_time") or {}):
                n_with_cards += 1
            if "Escanteios" in rec["mercados"] or "Escanteios" in (rec.get("mercados_time") or {}):
                n_with_corners += 1

    write_latest(n_out, promote=None)
    print(
        f"[pinnacle] {n_out} jogos salvos ({n_with_corners} escanteios · {n_with_cards} cartões) "
        f"· fetch_ok={n_fetch_ok} → {out_path.name}"
    )
    # highlight FRA-ESP se presente
    for rec in by_parent.values():
        nm = (rec.get("name") or "").lower()
        if "france" in nm and "spain" in nm:
            print(f"[pinnacle] FRA×ESP mercados={list(rec.get('mercados') or {})} child_ids={rec.get('child_ids')}")
            if "Cartões" in (rec.get("mercados") or {}):
                print(f"  Cartões: {rec['mercados']['Cartões']}")
            break
    return n_out


if __name__ == "__main__":
    import time as _t
    _t0 = _t.time()
    try:
        _n = main() or 0
        sys.exit(finish("pinnacle", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("pinnacle", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
