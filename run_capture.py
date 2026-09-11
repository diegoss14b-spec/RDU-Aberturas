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
from capture_health import state as capture_state

FETCHERS = [
    ("betano",     "fetch_odds_betano.py",    13 * 60),
    # superbet 8→15min (20/08): o full dela TIMEOUTAVA a 480s desde a noite de 19/08
    # (status: "TIMEOUT após 480s", n=0) — ponteiro full congelou em 19/08 19:42,
    # passou das 12h do BOARD_MAX_AGE_H e a casa SUMIU do board. Ela é a casa com
    # mais mercados e em dia de rodada europeia cheia (24 UECL + 12 UEL…) o full
    # não cabe em 8min. Captura é paralela (pool): o teto só vale pra ela.
    ("superbet",   "fetch_odds_superbet.py",  15 * 60),
    ("estrelabet", "fetch_odds_estrelabet.py", 10 * 60),
    ("7k",         "fetch_odds_7k.py",        12 * 60),
    ("pinnacle",   "fetch_odds_pinnacle.py",   5 * 60),
    ("bet365",     "fetch_odds_bet365.py",     8 * 60),
    # betfast DESLIGADA (21/08, decisão Diego via brief Cursor): 13 fails/24 runs
    # e ~5 jogos só-escanteios quando funciona — custo>valor. Religar = descomentar.
    # ("betfast",  "fetch_odds_betfast.py",   10 * 60),
    ("sportingbet","fetch_odds_sportingbet.py", 8 * 60),
]
# 08/09: 72 torneios sequenciais; captura saudável de 12:13 levou 237,3s
# (150 requests, 1103 fixtures). O teto de 240s deixou só 2,7s de folga e
# matou a rodada seguinte. 480s permite variação de latência sem alterar
# promoção atômica, rejeição de fonte parcial ou gate de frescor de 12h.
FIXTURE_FETCH = ("sofa", "fetch_fixtures_sofascore.py", 8 * 60)


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
    st = load_status(casa)
    st.update({"attempted": not bool(st.get("skipped_reason")), "attempt_started_epoch": t0,
               "attempt_finished_epoch": time.time()})
    st["source_state"] = capture_state(st, casa)
    _atomic_write_text(STATUS / f"{casa}.json", json.dumps(st, ensure_ascii=False, indent=1))
    return rc


def load_status(casa):
    try:
        return json.loads((STATUS / f"{casa}.json").read_text(encoding="utf-8"))
    except Exception:
        return {"casa": casa, "ok": False, "n_events": 0, "n_markets": 0,
                "pointer_valid": False, "error": "sem status válido"}


def status_ok(st):
    if capture_state(st, st.get("casa")) != "ok":
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


def should_retry(st, casa):
    # A preserved richer residential feed and policy/parse failures cannot be
    # improved by repeating the same datacenter capture immediately.
    if capture_state(st, casa) in ("protected_feed", "disabled") or status_ok(st):
        return False
    if st.get("attempted") is False:
        return False
    if casa == "bet365" and "captura parcial: rede/orçamento" in str(st.get("error") or ""):
        return False  # bounded collector already retries missing FIs internally
    return st.get("error_class") not in ("Auth", "Geo", "Parse", "CaptureBudgetExceeded")


def record_skip(casa, reason):
    st = load_status(casa)
    st.update({"attempted": False, "skipped_reason": reason,
               "last_decision_epoch": time.time()})
    st['source_state'] = capture_state(st, casa)
    # Do not refresh ts_utc/ts_brt or the pointer: no odds were fetched.
    _atomic_write_text(STATUS / f"{casa}.json", json.dumps(st, ensure_ascii=False, indent=1))


def main():
    STATUS.mkdir(parents=True, exist_ok=True)
    results = {}
    from concurrent.futures import ThreadPoolExecutor
    print("===== captura paralela das casas + sofa fixtures =====", flush=True)
    # FULL_STRIDE (env json, ex. {"estrelabet":2,"7k":2}): em modo FULL a casa só
    # captura quando hour %% stride == 0 — close (janela curta, barato) roda sempre.
    # Economia de Decodo (10/08): estrelabet ~6MB e 7k ~4MB por full — stride 2
    # corta metade. Casa pulada mantém o status/pointer anterior (stale-keep de 12h
    # do write_odds_latest cobre o buraco). Reverter = tirar da env.
    import datetime as _dt
    _stride = {}
    try:
        _stride = {k.lower(): max(1, int(v)) for k, v in
                   json.loads(os.environ.get("FULL_STRIDE") or "{}").items()}
    except Exception:
        print("[stride] FULL_STRIDE inválida — ignorando (todas capturam)")
    _is_full = not os.environ.get("ODDS_WINDOW_H")
    _hr = _dt.datetime.utcnow().hour
    _run = []
    for c, script, tmo in FETCHERS:
        st = _stride.get(c.lower(), 1)
        if _is_full and st > 1 and (_hr % st) != 0:
            print(f"[stride] {c}: full pulado (hora {_hr} %% {st} != 0) — pointer anterior segue valendo")
            results[c] = 0
            record_skip(c, "full_stride")
            continue
        _run.append((c, script, tmo))
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {c: ex.submit(run_one, c, script, tmo) for c, script, tmo in _run}
        fx_casa, fx_script, fx_tmo = FIXTURE_FETCH
        futs[fx_casa] = ex.submit(run_one, fx_casa, fx_script, fx_tmo)
        for casa, fut in futs.items():
            results[casa] = fut.result()

    # Bound the tail of a full: at most two simultaneous retries and 10 min
    # overall, instead of adding every house timeout sequentially.
    retry_deadline = time.monotonic() + 600
    def retry_one(casa, script, tmo):
        remaining = int(retry_deadline - time.monotonic())
        if remaining < 30:
            print(f"[{casa}] retry não iniciado: orçamento da rodada esgotado")
            return results.get(casa, 1)
        print(f"[retry] {casa} (até {min(tmo, remaining)}s)", flush=True)
        return run_one(casa, script, min(tmo, remaining))
    retries = [(c, script, tmo) for c, script, tmo in _run
               if should_retry(load_status(c), c)]
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {c: ex.submit(retry_one, c, script, tmo) for c, script, tmo in retries}
        for casa, fut in futs.items():
            results[casa] = fut.result()

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
            "source_state": st.get("source_state"),
            "error_class": st.get("error_class"),
            "attempted": st.get("attempted"),
            "skipped_reason": st.get("skipped_reason"),
            "mode": st.get("mode"),
            "ts_utc": st.get("ts_utc"),
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
                          "n_markets": v["n_markets"], "source_state":v.get("source_state"),
                          "error_class":v.get("error_class")} for c, v in per_casa.items()}
        for c, v in per_casa.items():
            hist_casas[c].update({"attempted": v.get("attempted"), "skipped_reason": v.get("skipped_reason")})
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
