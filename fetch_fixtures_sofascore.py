# -*- coding: utf-8 -*-
"""fetch_fixtures_sofascore.py — calendário canônico de jogos (base da unificação de nomes).

Puxa próximos jogos das ligas-alvo na API SofaScore e grava:
  data/fixtures/sofa_{stamp}.json + sofa_latest.json

Uso no build_board: cada odd de casa é encaixada num fixture sofa
(horário ± janela + fuzzy de times, com atalho '1 lado forte no mesmo horário').
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(os.devnull, "w")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
H = {
    "User-Agent": "Mozilla/5.0 Chrome/124.0.0.0",
    "Accept": "*/*",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "x-requested-with": "XMLHttpRequest",
}
# (uniqueTournamentId, label). Season = 1ª de /seasons. 404 soft-skip.
TOURNAMENTS = [
    (325, "BR-A"),
    (390, "BR-B"),
    (373, "BR-CdB"),
    (16, "WC"),
    (7, "UCL"),
    (679, "UEL"),
    (17, "EPL"),
    (8, "LaLiga"),
    (23, "SerieA"),
    (35, "Bundesliga"),
    (34, "Ligue1"),
    (242, "MLS"),
    (648, "Argentina"),
    (136, "CSL"),
    (40, "Allsvenskan"),
    (278, "Uruguay"),
    (240, "Ecuador"),
    (203, "Russia"),
    (20, "Eliteserien"),
]

DAYS_AHEAD = 7          # só grava jogos em [agora-6h, agora+N dias]
MAX_PAGES = 6           # pages de /events/next/{page}


def get(url, tries=3):
    try:
        from curl_cffi import requests as creq
        for a in range(tries):
            try:
                r = creq.get(url, headers=H, impersonate="chrome124", timeout=25)
                if r.status_code == 200 and r.text[:1] in "{[":
                    return r.json()
                if r.status_code == 404:
                    return None
            except Exception:
                pass
            time.sleep(0.8 * (a + 1))
    except ImportError:
        import requests
        for a in range(tries):
            try:
                r = requests.get(url, headers=H, timeout=25)
                if r.status_code == 200 and r.text[:1] in "{[":
                    return r.json()
                if r.status_code == 404:
                    return None
            except Exception:
                pass
            time.sleep(0.8 * (a + 1))
    return None


def season_id(utid):
    d = get(f"https://api.sofascore.com/api/v1/unique-tournament/{utid}/seasons")
    seas = (d or {}).get("seasons") or []
    if not seas:
        return None
    return seas[0].get("id")


def fetch_tournament(utid, label):
    sid = season_id(utid)
    if not sid:
        print(f"[sofa] {label} utid={utid}: sem season")
        return []
    out = []
    for page in range(MAX_PAGES):
        d = get(f"https://api.sofascore.com/api/v1/unique-tournament/{utid}/season/{sid}/events/next/{page}")
        evs = (d or {}).get("events") or []
        if not evs:
            break
        out.extend(evs)
        time.sleep(0.25)
        if len(evs) < 10:
            break
    print(f"[sofa] {label} utid={utid} season={sid}: {len(out)} eventos")
    return out


def normalize_event(e, label):
    home = (e.get("homeTeam") or {})
    away = (e.get("awayTeam") or {})
    ts = e.get("startTimestamp")
    if not ts or not home.get("name") or not away.get("name"):
        return None
    start_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    start_brt = start_utc.astimezone(BRT)
    tour = e.get("tournament") or {}
    ut = tour.get("uniqueTournament") or {}
    return {
        "sofa_id": e.get("id"),
        "home": home.get("name"),
        "away": away.get("name"),
        "home_id": home.get("id"),
        "away_id": away.get("id"),
        "home_code": home.get("nameCode") or home.get("shortName") or "",
        "away_code": away.get("nameCode") or away.get("shortName") or "",
        "start_utc": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_ts": int(ts),
        "day_brt": start_brt.strftime("%Y-%m-%d"),
        "time_brt": start_brt.strftime("%H:%M"),
        "inicio": start_brt.strftime("%d/%m %H:%M"),
        "league": tour.get("name") or ut.get("name") or label,
        "league_id": ut.get("id"),
        "label": label,
        "status": (e.get("status") or {}).get("type") or (e.get("status") or {}).get("description") or "",
    }


def main():
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(hours=6)
    t1 = now + timedelta(days=DAYS_AHEAD)
    seen = set()
    fixtures = []
    for utid, label in TOURNAMENTS:
        try:
            raw = fetch_tournament(utid, label)
        except Exception as ex:
            print(f"[sofa] {label} erro: {ex}")
            continue
        for e in raw:
            eid = e.get("id")
            if not eid or eid in seen:
                continue
            rec = normalize_event(e, label)
            if not rec:
                continue
            st = datetime.fromtimestamp(rec["start_ts"], tz=timezone.utc)
            if st < t0 or st > t1:
                continue
            seen.add(eid)
            fixtures.append(rec)
        time.sleep(0.2)

    fixtures.sort(key=lambda x: x["start_ts"])
    stamp = datetime.now(BRT).strftime("%Y-%m-%d_%H%M")
    payload = {
        "gerado": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
        "fonte": "sofascore",
        "n": len(fixtures),
        "janela_dias": DAYS_AHEAD,
        "fixtures": fixtures,
    }
    outp = OUT / f"sofa_{stamp}.json"
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "sofa_latest.json").write_text(
        json.dumps({"file": outp.name, "n": len(fixtures), "at": payload["gerado"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[sofa] {len(fixtures)} fixtures em janela {DAYS_AHEAD}d → {outp.name}")
    for f in fixtures[:8]:
        print(f"  {f['inicio']} · {f['home']} - {f['away']} · {f['league']}")
    return len(fixtures)


if __name__ == "__main__":
    try:
        n = main() or 0
        sys.exit(0 if n >= 0 else 1)
    except Exception as e:
        print("[sofa] FAIL", e)
        sys.exit(1)
