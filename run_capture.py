# -*- coding: utf-8 -*-
"""Orquestra a captura sem reaproveitar status/pointers quebrados de rodada anterior."""
import json, os, sys, subprocess, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "data" / "odds" / "_status"
BRT = timezone(timedelta(hours=-3))
from capture_common import _atomic_write_text

FETCHERS = [
    ("betano",     "fetch_odds_betano.py",    13 * 60),
    ("superbet",   "fetch_odds_superbet.py",   8 * 60),
    ("estrelabet", "fetch_odds_estrelabet.py", 10 * 60),
    ("7k",         "fetch_odds_7k.py",        12 * 60),
    ("pinnacle",   "fetch_odds_pinnacle.py",   5 * 60),
]
FIXTURE_FETCH = ("sofa", "fetch_fixtures_sofascore.py", 4 * 60)


def _now_fields():
    now = datetime.now(timezone.utc)
    return now, now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.astimezone(BRT).strftime("%Y-%m-%d %H:%M")


def _write_pending(casa, t0):
    now, utc, brt = _now_fields()
    _atomic_write_text(STATUS / f"{casa}.json", json.dumps({
        "casa": casa, "ok": False, "ts_utc": utc, "ts_brt": brt,
        "n_events": 0, "n_markets": 0, "market_counts": {},
        "pointer_valid": False, "duration_sec": 0,
        "error": "captura em andamento", "error_class": "Pending",
        "mode": "close" if os.environ.get("ODDS_WINDOW_H") else "full",
        "run_started_epoch": t0,
    }, ensure_ascii=False, indent=1))


def run_one(casa, script, tmo):
    """Roda um fetcher isolado; status velho nunca pode fazê-lo parecer saudável."""
    t0 = time.time()
    STATUS.mkdir(parents=True, exist_ok=True)
    _write_pending(casa, t0)
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / script)],
                           cwd=str(ROOT), timeout=tmo)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = 124
        _, utc, brt = _now_fields()
        _atomic_write_text(STATUS / f"{casa}.json", json.dumps({
            "casa": casa, "ok": False, "ts_utc": utc, "ts_brt": brt,
            "n_events": 0, "n_markets": 0, "market_counts": {},
            "pointer_valid": False, "duration_sec": round(time.time() - t0, 1),
            "error": f"TIMEOUT após {tmo}s", "error_class": "Timeout",
            "mode": "close" if os.environ.get("ODDS_WINDOW_H") else "full",
        }, ensure_ascii=False, indent=1))
    print(f"[{casa}] exit={rc} ({time.time()-t0:.0f}s)", flush=True)
    return rc


def load_status(casa):
    try:
        return json.loads((STATUS / f"{casa}.json").read_text(encoding="utf-8"))
    except Exception:
        return {"casa": casa, "ok": False, "n_events": 0, "n_markets": 0,
                "pointer_valid": False, "error": "sem status válido"}


def status_ok(st):
    if not st.get("ok"):
        return False
    n = int(st.get("n_events") or 0)
    if st.get("mode") == "full" and n > 0:
        if st.get("pointer_valid") is not True:
            return False
        if int(st.get("n_markets") or 0) <= 0:
            return False
    return True


def casa_ok(casa):
    return status_ok(load_status(casa))


def main():
    STATUS.mkdir(parents=True, exist_ok=True)
    results = {}
    from concurrent.futures import ThreadPoolExecutor
    print("===== captura paralela das casas + sofa fixtures =====", flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {c: ex.submit(run_one, c, script, tmo) for c, script, tmo in FETCHERS}
        fx_casa, fx_script, fx_tmo = FIXTURE_FETCH
        futs[fx_casa] = ex.submit(run_one, fx_casa, fx_script, fx_tmo)
        for casa, fut in futs.items():
            results[casa] = fut.result()

    for casa, script, tmo in FETCHERS:
        if not casa_ok(casa):
            print(f"\n===== RETRY {casa} =====", flush=True)
            results[casa] = run_one(casa, script, tmo)

    casas_ok, casas_fail, total_events = [], [], 0
    per_casa, per_market = {}, {}
    for casa, _, _ in FETCHERS:
        st = load_status(casa)
        valid = status_ok(st)
        entry = {
            "ok": valid,
            "n_events": int(st.get("n_events") or 0),
            "n_markets": int(st.get("n_markets") or 0),
            "market_counts": st.get("market_counts") or {},
            "pointer_valid": st.get("pointer_valid"),
            "pointer_file": st.get("pointer_file"),
            "error": st.get("error"),
        }
        per_casa[casa] = entry
        if valid:
            casas_ok.append(casa)
            total_events += entry["n_events"]
            for market, count in entry["market_counts"].items():
                per_market[market] = per_market.get(market, 0) + int(count or 0)
        else:
            detail = st.get("error") or f"exit={results.get(casa)}"
            if st.get("ok") and not valid:
                detail = "status inconsistente (pointer/n_markets)"
            casas_fail.append({"casa": casa, "error": detail})

    min_events_deploy = int(os.environ.get("MIN_EVENTS_DEPLOY", "2"))
    deploy_allowed = len(casas_ok) >= 2 and total_events >= min_events_deploy
    reason = "ok" if deploy_allowed else f"captura insuficiente: {len(casas_ok)} casas ok, {total_events} eventos"
    _, utc, brt = _now_fields()
    sofa = load_status("sofa")
    summary = {
        "ts_utc": utc, "ts_brt": brt,
        "casas_ok": casas_ok, "casas_fail": casas_fail,
        "n_ok": len(casas_ok), "n_fail": len(casas_fail),
        "total_events": total_events,
        "per_casa": per_casa, "market_counts": dict(sorted(per_market.items())),
        "fixtures": sofa,
        "deploy_allowed": deploy_allowed, "reason": reason,
        "mode": "close" if os.environ.get("ODDS_WINDOW_H") else "full",
    }
    _atomic_write_text(STATUS / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1))

    if not os.environ.get("ODDS_WINDOW_H"):
        hist_casas = {c: {"ok": v["ok"], "n": v["n_events"],
                          "n_markets": v["n_markets"]} for c, v in per_casa.items()}
        hist_line = {"ts": brt, "casas": hist_casas, "total": total_events,
                     "market_counts": summary["market_counts"],
                     "sofa": {"ok": bool(sofa.get("ok")),
                               "n": sofa.get("n_fixtures") or sofa.get("n_events") or 0,
                               "pointer_valid": sofa.get("pointer_valid")}}
        with (STATUS / "history.jsonl").open("a", encoding="utf-8") as hf:
            hf.write(json.dumps(hist_line, ensure_ascii=False) + "\n")

    print(f"\n===== RESUMO: {len(casas_ok)}/{len(FETCHERS)} casas ok · {total_events} eventos · deploy_allowed={deploy_allowed} ({reason})")
    for cf in casas_fail:
        print(f"  ✗ {cf['casa']}: {cf['error']}")
    sys.exit(0 if deploy_allowed else 3)


if __name__ == "__main__":
    main()
