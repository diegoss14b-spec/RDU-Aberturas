# -*- coding: utf-8 -*-
"""gate_board.py — gate de qualidade ANTES do deploy (política "não piorar" do brief).
Baseline = o board AO VIVO no site (https://valor-rdu.netlify.app/data/board.js) — sem
cache/artifact: o site publicado É o estado anterior. Compara com o board recém-gerado:
BLOQUEIA (exit 3, site antigo fica no ar) se:
  - summary.deploy_allowed == false; ou
  - n_casas_now < n_casas_prev E n_casas_now < 3; ou
  - n_jogos_now < 50% de n_jogos_prev (quando prev >= 10).
Se bloquear: grava data/odds/_status/blocked_deploy.json com o motivo."""
import json, re, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "data" / "odds" / "_status"
BRT = timezone(timedelta(hours=-3))
LIVE_URL = "https://valor-rdu.netlify.app/data/board.js"

def parse_board(txt):
    m = re.search(r"BOARD\s*=\s*", txt)
    d, _ = json.JSONDecoder().raw_decode(txt, m.end())
    return d

def main():
    new = parse_board((ROOT / "valor" / "data" / "board.js").read_text(encoding="utf-8"))
    n_casas_now = len(new.get("casas") or [])
    n_jogos_now = len(new.get("jogos") or [])
    summary = {}
    sf = STATUS / "summary.json"
    if sf.exists():
        summary = json.loads(sf.read_text(encoding="utf-8"))

    prev = None
    try:
        from curl_cffi import requests as cr
        r = cr.get(LIVE_URL, timeout=20, impersonate="chrome124")
        if r.status_code == 200:
            prev = parse_board(r.text)
    except Exception as e:
        print(f"[gate] aviso: não li o board ao vivo ({type(e).__name__}) — sigo sem baseline")
    n_casas_prev = len((prev or {}).get("casas") or [])
    n_jogos_prev = len((prev or {}).get("jogos") or [])

    reasons = []
    if summary and not summary.get("deploy_allowed", True):
        reasons.append(f"summary: {summary.get('reason')}")
    if prev is not None:
        if n_casas_now < n_casas_prev and n_casas_now < 3:
            reasons.append(f"casas caíram: {n_casas_prev} → {n_casas_now}")
        if n_jogos_prev >= 10 and n_jogos_now < 0.5 * n_jogos_prev:
            reasons.append(f"jogos caíram >50%: {n_jogos_prev} → {n_jogos_now}")

    print(f"[gate] agora: {n_casas_now} casas / {n_jogos_now} jogos · ao vivo: {n_casas_prev} casas / {n_jogos_prev} jogos")
    if reasons:
        blocked = {"ts_brt": datetime.now(BRT).strftime("%Y-%m-%d %H:%M"),
                   "reasons": reasons,
                   "now": {"casas": n_casas_now, "jogos": n_jogos_now},
                   "prev": {"casas": n_casas_prev, "jogos": n_jogos_prev}}
        STATUS.mkdir(parents=True, exist_ok=True)
        (STATUS / "blocked_deploy.json").write_text(json.dumps(blocked, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[gate] ❌ DEPLOY BLOQUEADO — site antigo permanece no ar:")
        for r_ in reasons: print(f"   - {r_}")
        sys.exit(3)
    print("[gate] ✅ liberado pra deploy")
    sys.exit(0)

if __name__ == "__main__":
    main()
