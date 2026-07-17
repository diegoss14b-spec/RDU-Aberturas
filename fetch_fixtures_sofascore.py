# -*- coding: utf-8 -*-
"""Calendário canônico SofaScore com promoção e fallback atômicos/observáveis."""
import sys, os, json, time, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
from capture_common import (
    _atomic_write_text, _cleanup_snapshot_versions, _immutable_text_snapshot,
    pointer_age_hours, classify_error, STATUS_DIR,
)

H = {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0", "Accept": "*/*",
     "Origin": "https://www.sofascore.com", "Referer": "https://www.sofascore.com/",
     "x-requested-with": "XMLHttpRequest"}
TOURNAMENTS = [
    (325, "BR-A"), (390, "BR-B"), (373, "BR-CdB"), (16, "WC"),
    (7, "UCL"), (679, "UEL"), (17, "EPL"), (8, "LaLiga"),
    (23, "SerieA"), (35, "Bundesliga"), (34, "Ligue1"), (242, "MLS"),
    (648, "Argentina"), (136, "CSL"), (40, "Allsvenskan"), (278, "Uruguay"),
    (240, "Ecuador"), (203, "Russia"), (20, "Eliteserien"),
]
DAYS_AHEAD = 7
MAX_PAGES = 6
STABLE_FILE = "sofa_latest_data.json"


def get(url, tries=3):
    try:
        from curl_cffi import requests as creq
        getter = lambda u: creq.get(u, headers=H, impersonate="chrome124", timeout=25)
    except ImportError:
        import requests
        getter = lambda u: requests.get(u, headers=H, timeout=25)
    for a in range(tries):
        try:
            r = getter(url)
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
    return seas[0].get("id") if seas else None


def fetch_tournament(utid, label):
    sid = season_id(utid)
    if not sid:
        print(f"[sofa] {label} utid={utid}: sem season")
        return []
    out = []
    for page in range(MAX_PAGES):
        d = get(f"https://api.sofascore.com/api/v1/unique-tournament/{utid}/season/{sid}/events/next/{page}")
        evs = (d or {}).get("events") or []
        if not evs: break
        out.extend(evs)
        time.sleep(0.25)
        if len(evs) < 10: break
    print(f"[sofa] {label} utid={utid} season={sid}: {len(out)} eventos")
    return out


def normalize_event(e, label):
    home, away = e.get("homeTeam") or {}, e.get("awayTeam") or {}
    ts = e.get("startTimestamp")
    if not ts or not home.get("name") or not away.get("name"): return None
    start_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    start_brt = start_utc.astimezone(BRT)
    tour = e.get("tournament") or {}; ut = tour.get("uniqueTournament") or {}
    return {
        "sofa_id": e.get("id"), "home": home.get("name"), "away": away.get("name"),
        "home_id": home.get("id"), "away_id": away.get("id"),
        "home_code": home.get("nameCode") or home.get("shortName") or "",
        "away_code": away.get("nameCode") or away.get("shortName") or "",
        "start_utc": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "start_ts": int(ts),
        "day_brt": start_brt.strftime("%Y-%m-%d"), "time_brt": start_brt.strftime("%H:%M"),
        "inicio": start_brt.strftime("%d/%m %H:%M"),
        "league": tour.get("name") or ut.get("name") or label,
        "league_id": ut.get("id"), "label": label,
        "status": (e.get("status") or {}).get("type") or (e.get("status") or {}).get("description") or "",
    }


def pointer_info():
    ptr = OUT / "sofa_latest.json"
    try:
        meta = json.loads(ptr.read_text(encoding="utf-8"))
        src = OUT / meta["file"]
        data = json.loads(src.read_text(encoding="utf-8"))
        n = len(data.get("fixtures") or [])
        valid = n > 0 and n == int(meta.get("n") or 0)
        return meta, src, n, valid, pointer_age_hours(meta)
    except Exception:
        return {}, None, 0, False, None


def write_status(ok, n, min_required, promoted, error=None, t0=None):
    meta, src, pointer_n, valid, age_h = pointer_info()
    now = datetime.now(timezone.utc)
    st = {
        "casa": "sofa", "kind": "fixture", "ok": bool(ok),
        "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_brt": now.astimezone(BRT).strftime("%Y-%m-%d %H:%M"),
        "n_events": int(n or 0), "n_fixtures": int(n or 0), "n_markets": 0,
        "min_events": min_required, "promoted": bool(promoted),
        "pointer_valid": bool(valid), "pointer_file": meta.get("file"),
        "pointer_at": meta.get("at") or meta.get("ts"), "pointer_n": pointer_n,
        "pointer_age_h": round(age_h, 3) if age_h is not None else None,
        "duration_sec": round(time.time() - t0, 1) if t0 else None,
        "error": str(error)[:300] if error else None,
        "error_class": classify_error(error) if error else None,
    }
    _atomic_write_text(STATUS_DIR / "sofa.json", json.dumps(st, ensure_ascii=False, indent=1))
    return st


def promote_fixture_snapshot(payload):
    """Promove fixture imutável e troca o pointer somente como último passo."""
    previous_meta, previous_src, _, previous_valid, _ = pointer_info()
    if not previous_valid:
        previous_meta, previous_src = {}, None
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    stable = _immutable_text_snapshot(OUT / "_snapshots", "sofa", ".json", text)
    check = json.loads(stable.read_text(encoding="utf-8"))
    n = len(check.get("fixtures") or [])
    if n <= 0 or n != len(payload.get("fixtures") or []):
        raise ValueError("snapshot Sofa imutável falhou na revalidação")
    rel = stable.relative_to(OUT).as_posix()
    pointer = {"file": rel, "n": n, "at": payload["at"], "ts_utc": payload["ts_utc"]}
    # O pointer anterior e seu alvo permanecem válidos até este rename atômico.
    _atomic_write_text(OUT / "sofa_latest.json", json.dumps(pointer, ensure_ascii=False))
    # Mantém a geração anterior para leitores que já tenham lido o pointer antigo.
    _cleanup_snapshot_versions(OUT / "_snapshots", "sofa_*.json", [stable, previous_src])
    return rel

def main():
    t0_clock = time.time()
    now = datetime.now(timezone.utc); t0 = now - timedelta(hours=6); t1 = now + timedelta(days=DAYS_AHEAD)
    seen, fixtures = set(), []
    for utid, label in TOURNAMENTS:
        try: raw = fetch_tournament(utid, label)
        except Exception as ex:
            print(f"[sofa] {label} erro: {ex}"); continue
        for e in raw:
            eid = e.get("id")
            if not eid or eid in seen: continue
            rec = normalize_event(e, label)
            if not rec: continue
            st = datetime.fromtimestamp(rec["start_ts"], tz=timezone.utc)
            if st < t0 or st > t1: continue
            seen.add(eid); fixtures.append(rec)
        time.sleep(0.2)
    fixtures.sort(key=lambda x: x["start_ts"])

    prev_meta, _, prev_n, prev_valid, _ = pointer_info()
    base_min = max(1, int(os.environ.get("SOFA_MIN_FIXTURES", "10")))
    ratio = float(os.environ.get("SOFA_MIN_PREV_RATIO", "0.25"))
    min_required = max(base_min, math.ceil(prev_n * ratio) if prev_valid else 0)
    n = len(fixtures)
    now_brt = datetime.now(BRT)
    payload = {
        "gerado": now_brt.strftime("%Y-%m-%d %H:%M"),
        "at": now_brt.isoformat(timespec="seconds"),
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fonte": "sofascore", "n": n, "janela_dias": DAYS_AHEAD, "fixtures": fixtures,
    }
    promoted = n >= min_required
    if promoted:
        stable_file = promote_fixture_snapshot(payload)
        write_status(True, n, min_required, True, t0=t0_clock)
        print(f"[sofa] {n} fixtures em {DAYS_AHEAD}d → {stable_file} (promovido atomicamente)")
    else:
        err = f"snapshot não saudável: n={n}, mínimo={min_required}; fallback preservado n={prev_n}"
        write_status(False, n, min_required, False, error=err, t0=t0_clock)
        print(f"[sofa] ⚠ {err}")
    for f in fixtures[:8]: print(f"  {f['inicio']} · {f['home']} - {f['away']} · {f['league']}")
    return promoted


if __name__ == "__main__":
    started = time.time()
    try: sys.exit(0 if main() else 2)
    except BaseException as exc:
        if isinstance(exc, SystemExit): raise
        print("[sofa] FAIL", exc)
        write_status(False, 0, int(os.environ.get("SOFA_MIN_FIXTURES", "10")), False,
                     error=exc, t0=started)
        sys.exit(1)
