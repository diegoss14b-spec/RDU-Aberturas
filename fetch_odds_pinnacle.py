# -*- coding: utf-8 -*-
"""fetch_odds_pinnacle.py — captura Escanteios (jogo + time) da Pinnacle via API guest Arcadia
(gringa: guest.api.arcadia.pinnacle.com — NÃO é pinnacle.bet.br).

Mecanismo (validado 13/07/2026):
  - matchups futebol: /sports/29/matchups
  - escanteios = matchup FILHO com units='Corners' e parentId = jogo principal
    ex.: 'America Mineiro (Corners) / Londrina (Corners)' parent=jogo gols
  - odds: /matchups/{id}/markets/straight
    s;0;ou;{linha}     → total da partida (period 0 = jogo inteiro)
    s;0;tt;{linha};home → total mandante
    s;0;tt;{linha};away → total visitante
  - preços em AMERICANOS → convertidos p/ decimal

Saída: data/odds/pinnacle_{stamp}.jsonl + pinnacle_latest.json (formato board).
Cartões (units=Bookings): parser preparado; só grava quando houver O/U de partida aberta.
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
MAX_SPECIALS = 80  # teto de matchups de corners/bookings por rodada

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
    # home/away: alignment  home/away or order
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


def _strip_unit_suffix(name, unit):
    """'America Mineiro (Corners)' → 'America Mineiro'."""
    n = (name or "").strip()
    for u in (unit, unit.title(), unit.upper(), "Corners", "Bookings", "Cards"):
        n = re.sub(rf"\s*\({re.escape(u)}\)\s*$", "", n, flags=re.I)
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
    # specials de corners/bookings
    specials = []
    for m in mups:
        units = (m.get("units") or "").strip()
        ul = units.lower()
        if ul not in UNIT_CANON:
            continue
        if not m.get("parentId"):
            continue
        specials.append(m)

    _wh = odds_window()
    if _wh is not None:
        _tot = len(specials)
        specials = [m for m in specials if in_window(m.get("startTime"), _wh)]
        MIN_EFF = min(MIN_EVENTS, 1) if specials else 0
        print(f"[pinnacle] modo close: janela {_wh:g}h -> {len(specials)} de {_tot} specials")

    # prioriza quem tem mais mercados
    specials.sort(key=lambda m: -(m.get("totalMarketCount") or 0))
    specials = specials[:MAX_SPECIALS]
    print(f"[pinnacle] specials corners/bookings: {len(specials)}")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"pinnacle_{stamp}.jsonl"
    latest = OUTDIR / "pinnacle_latest.json"

    def write_latest(n):
        latest.write_text(
            json.dumps({"file": out_path.name, "n": n, "at": now.isoformat(timespec="seconds")}, ensure_ascii=False),
            encoding="utf-8",
        )

    write_latest(0)
    n_out = 0
    with out_path.open("w", encoding="utf-8") as f:
        for m in specials:
            mid = m["id"]
            units = (m.get("units") or "").strip()
            canon = UNIT_CANON[units.lower()]
            parent = by_id.get(m.get("parentId")) or {}
            # nomes: preferir parent (sem sufixo Corners)
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

            merc = {}
            merc_t = {}
            if match_lines:
                merc[canon] = match_lines
            if home_lines or away_lines:
                by_team = {}
                if home_lines:
                    by_team[ph] = home_lines
                if away_lines:
                    by_team[pa] = away_lines
                if by_team:
                    merc_t[canon] = by_team

            if not merc and not merc_t:
                continue

            name = f"{ph} - {pa}"
            rec = {
                "casa": "Pinnacle",
                "event_id": mid,
                "parent_id": m.get("parentId"),
                "name": name,
                "league": league.replace(" Corners", "").replace(" Bookings", "").strip(),
                "start": start,
                "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "mercados": merc,
                "units": units,
            }
            if merc_t:
                rec["mercados_time"] = merc_t
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n_out += 1
            if n_out % 10 == 0:
                write_latest(n_out)

    write_latest(n_out)
    print(f"[pinnacle] {n_out} jogos com escanteios/cartões salvos em {out_path.name}")
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
