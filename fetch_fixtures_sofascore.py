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
    pointer_age_hours, classify_error, STATUS_DIR, br_proxies,
)

H = {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0", "Accept": "*/*",
     "Origin": "https://www.sofascore.com", "Referer": "https://www.sofascore.com/",
     "x-requested-with": "XMLHttpRequest"}
TOURNAMENTS = [
    (325, "BR-A"), (390, "BR-B"), (373, "BR-CdB"), (16, "WC"),
    (7, "UCL"), (679, "UEL"), (17, "EPL"), (8, "LaLiga"),
    (23, "SerieA"), (35, "Bundesliga"), (34, "Ligue1"), (242, "MLS"),
    (155, "Argentina"), (649, "CSL"), (40, "Allsvenskan"), (278, "Uruguay"),
    (240, "Ecuador"), (203, "Russia"), (20, "Eliteserien"),
]
DAYS_AHEAD = 7
MAX_PAGES = 3
REQUEST_TIMEOUT = 20
STABLE_FILE = "sofa_latest_data.json"
PROX = br_proxies()

try:
    from curl_cffi import requests as _HTTP_CLIENT
    _HTTP_IMPERSONATE = True
except ImportError:
    import requests as _HTTP_CLIENT
    _HTTP_IMPERSONATE = False

_GET_DIAG = {}


def _reset_get_diag():
    _GET_DIAG.clear()
    _GET_DIAG.update({
        "requests": 0, "proxy_attempts": 0, "direct_attempts": 0,
        "statuses": {}, "last_error": None, "consecutive_failures": 0,
        "circuit_open": False, "failed_tournaments": [], "inactive_tournaments": [],
    })


def _diag_snapshot():
    return {
        "requests": int(_GET_DIAG.get("requests") or 0),
        "proxy_attempts": int(_GET_DIAG.get("proxy_attempts") or 0),
        "direct_attempts": int(_GET_DIAG.get("direct_attempts") or 0),
        "statuses": dict(_GET_DIAG.get("statuses") or {}),
        "last_error": _GET_DIAG.get("last_error"),
        "consecutive_failures": int(_GET_DIAG.get("consecutive_failures") or 0),
        "circuit_open": bool(_GET_DIAG.get("circuit_open")),
        "failed_tournaments": list(_GET_DIAG.get("failed_tournaments") or []),
        "inactive_tournaments": list(_GET_DIAG.get("inactive_tournaments") or []),
    }


def _diag_text():
    d = _diag_snapshot()
    statuses = ",".join(f"{k}:{v}" for k, v in sorted(d["statuses"].items())) or "nenhum"
    return (
        f"req={d['requests']} proxy={d['proxy_attempts']} direto={d['direct_attempts']} "
        f"http={statuses} ultimo={d['last_error'] or '-'} "
        f"circuito={int(d['circuit_open'])} falhas={','.join(d['failed_tournaments']) or '-'} "
        f"inativos={','.join(d.get('inactive_tournaments') or []) or '-'}"
    )


def _transport_get(url, proxies):
    kwargs = {"headers": H, "timeout": REQUEST_TIMEOUT}
    if proxies:
        kwargs["proxies"] = proxies
    if _HTTP_IMPERSONATE:
        kwargs["impersonate"] = "chrome124"
    return _HTTP_CLIENT.get(url, **kwargs)


_reset_get_diag()


def get(url, tries=2):
    """Usa proxy residencial primeiro na nuvem e conexao direta localmente."""
    if _GET_DIAG.get("circuit_open"):
        return None
    routes = [("proxy", PROX)] if PROX else [("direct", None)]
    for mode, proxies in routes:
        for attempt in range(tries):
            _GET_DIAG["requests"] += 1
            _GET_DIAG[f"{mode}_attempts"] += 1
            try:
                r = _transport_get(url, proxies)
                status = int(getattr(r, "status_code", 0) or 0)
                statuses = _GET_DIAG["statuses"]
                statuses[str(status)] = int(statuses.get(str(status)) or 0) + 1
                body = (getattr(r, "text", "") or "").lstrip()
                if status == 200 and body[:1] in "{[":
                    data = r.json()
                    _GET_DIAG["last_error"] = None
                    _GET_DIAG["consecutive_failures"] = 0
                    return data
                if status in (401, 403, 407):
                    _GET_DIAG["last_error"] = f"HTTP {status} via {mode}"
                    _GET_DIAG["circuit_open"] = True
                    return None
                if status == 404:
                    _GET_DIAG["last_error"] = f"HTTP 404 via {mode}"
                    return None
                _GET_DIAG["last_error"] = f"HTTP {status or '?'} via {mode}"
            except Exception as exc:
                _GET_DIAG["last_error"] = f"{type(exc).__name__} via {mode}"
            if attempt + 1 < tries:
                time.sleep(0.8 * (attempt + 1))
    _GET_DIAG["consecutive_failures"] += 1
    if _GET_DIAG["consecutive_failures"] >= 2:
        _GET_DIAG["circuit_open"] = True
        _GET_DIAG["last_error"] = f"{_GET_DIAG.get('last_error') or 'transport failure'}; circuit open"
    return None


def season_id(utid):
    """Retorna (sid, api_respondeu). sid=None com api_respondeu=True = torneio SEM temporada
    ativa (encerrado/sazonal, ex: Copa do Mundo pós-final) — é INATIVO, não falha."""
    d = get(f"https://api.sofascore.com/api/v1/unique-tournament/{utid}/seasons")
    if not isinstance(d, dict):
        return None, False
    seas = d.get("seasons") or []
    return (seas[0].get("id") if seas else None), True


def fetch_tournament(utid, label, max_ts=None):
    sid, api_ok = season_id(utid)
    if not sid:
        if api_ok:
            # Torneio sem temporada ativa → INATIVO (warning), NÃO falha do source.
            # Antes: entrava em failed_tournaments → source_healthy=False → o ponteiro
            # envelhecia → o gate bloqueava o board inteiro após 12h. Foi exatamente o que
            # travou a Mesa em 20/07/2026, quando a Copa do Mundo (16, "WC") acabou.
            if label not in _GET_DIAG["inactive_tournaments"]:
                _GET_DIAG["inactive_tournaments"].append(label)
            print(f"[sofa] {label} utid={utid}: sem temporada ativa → INATIVO (não bloqueia)")
        else:
            if label not in _GET_DIAG["failed_tournaments"]:
                _GET_DIAG["failed_tournaments"].append(label)
            print(f"[sofa] {label} utid={utid}: falha de transporte ({_diag_text()})")
        return []
    out = []
    for page in range(MAX_PAGES):
        d = get(f"https://api.sofascore.com/api/v1/unique-tournament/{utid}/season/{sid}/events/next/{page}")
        if not isinstance(d, dict):
            if label not in _GET_DIAG["failed_tournaments"]:
                _GET_DIAG["failed_tournaments"].append(label)
            break
        evs = d.get("events") or []
        if not evs: break
        out.extend(evs)
        stamps = [int(e["startTimestamp"]) for e in evs if e.get("startTimestamp")]
        time.sleep(0.25)
        if d.get("hasNextPage") is False or len(evs) < 10: break
        if max_ts and stamps and max(stamps) >= max_ts: break
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
        "transport": _diag_snapshot(),
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
    _reset_get_diag()
    now = datetime.now(timezone.utc); t0 = now - timedelta(hours=6); t1 = now + timedelta(days=DAYS_AHEAD)
    seen, fixtures = set(), []
    for utid, label in TOURNAMENTS:
        try: raw = fetch_tournament(utid, label, int(t1.timestamp()))
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
    source_healthy = not _GET_DIAG["circuit_open"] and not _GET_DIAG["failed_tournaments"]
    promoted = n >= min_required and source_healthy
    if promoted:
        stable_file = promote_fixture_snapshot(payload)
        write_status(True, n, min_required, True, t0=t0_clock)
        print(f"[sofa] {n} fixtures em {DAYS_AHEAD}d → {stable_file} (promovido atomicamente)")
    else:
        err = f"snapshot não saudável: n={n}, mínimo={min_required}; fallback preservado n={prev_n}"
        err += f"; transporte {_diag_text()}"
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
