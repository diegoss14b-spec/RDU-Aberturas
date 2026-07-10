# -*- coding: utf-8 -*-
"""run_capture.py — orquestrador da captura das 4 casas (P0 do brief: sem falha silenciosa).
Roda os 4 fetchers em sequência com timeout próprio, lê os data/odds/_status/{casa}.json
que cada um grava, e escreve data/odds/_status/summary.json com o veredito:
  deploy_allowed = (n_ok >= 2) E (total de eventos >= 8)
Exit: 0 se deploy_allowed, 3 se captura insuficiente (job fica vermelho — de propósito).
Falha de UMA casa NÃO derruba as outras (cada fetcher é um subprocesso isolado)."""
import json, sys, subprocess, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "data" / "odds" / "_status"
BRT = timezone(timedelta(hours=-3))

FETCHERS = [  # (casa, script, timeout_s)
    ("betano",     "fetch_odds_betano.py",    13 * 60),
    ("superbet",   "fetch_odds_superbet.py",   8 * 60),
    ("estrelabet", "fetch_odds_estrelabet.py", 6 * 60),
    ("7k",         "fetch_odds_7k.py",        12 * 60),
]

def main():
    STATUS.mkdir(parents=True, exist_ok=True)
    results = {}
    for casa, script, tmo in FETCHERS:
        print(f"\n===== {casa} (timeout {tmo//60}min) =====", flush=True)
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / script)],
                               cwd=str(ROOT), timeout=tmo)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = 124
            (STATUS / f"{casa}.json").write_text(json.dumps({
                "casa": casa, "ok": False, "n_events": 0,
                "error": f"TIMEOUT apos {tmo}s", "error_class": "Timeout",
                "ts_brt": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
                "duration_sec": round(time.time() - t0, 1)}, ensure_ascii=False), encoding="utf-8")
        results[casa] = rc
        print(f"[{casa}] exit={rc} ({time.time()-t0:.0f}s)", flush=True)

    # consolida
    casas_ok, casas_fail, total_events = [], [], 0
    for casa, _, _ in FETCHERS:
        f = STATUS / f"{casa}.json"
        st = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"ok": False, "error": "sem status"}
        if st.get("ok"):
            casas_ok.append(casa); total_events += st.get("n_events") or 0
        else:
            casas_fail.append({"casa": casa, "error": st.get("error") or f"exit={results.get(casa)}"})
    deploy_allowed = len(casas_ok) >= 2 and total_events >= 8
    reason = ("ok" if deploy_allowed else
              f"captura insuficiente: {len(casas_ok)} casas ok, {total_events} eventos")
    summary = {"ts_brt": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
               "casas_ok": casas_ok, "casas_fail": casas_fail,
               "n_ok": len(casas_ok), "n_fail": len(casas_fail),
               "total_events": total_events,
               "deploy_allowed": deploy_allowed, "reason": reason}
    (STATUS / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n===== RESUMO: {len(casas_ok)}/4 casas ok · {total_events} eventos · deploy_allowed={deploy_allowed} ({reason})")
    for cf in casas_fail:
        print(f"  ✗ {cf['casa']}: {cf['error']}")
    sys.exit(0 if deploy_allowed else 3)

if __name__ == "__main__":
    main()
