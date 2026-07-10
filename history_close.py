# -*- coding: utf-8 -*-
"""history_close.py — congela a ODD DE FECHAMENTO. Para cada key com status=open cujo
kickoff já passou (agora >= kickoff − 2min): close_odd = last_odd (última vista pré-jogo),
status=closed. NUNCA apaga o open. Com captura 4/4h o close é aproximado (última odd vista
até ~4h antes) — v1 aceita; granularidade maior no futuro."""
import json, sys, glob
from pathlib import Path
from datetime import datetime, timezone, timedelta
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ROOT = Path(__file__).resolve().parent
HIST = ROOT / "data" / "odds_history"
BRT = timezone(timedelta(hours=-3))

def main():
    now = datetime.now(BRT)
    n = 0
    for kf in sorted(glob.glob(str(HIST / "keys" / "*.json"))):
        p = Path(kf)
        keys = json.loads(p.read_text(encoding="utf-8"))
        changed = False
        for key, k in keys.items():
            if k.get("status") != "open": continue
            try:
                ko = datetime.fromisoformat(k["kickoff"])
            except Exception:
                continue
            if now >= ko - timedelta(minutes=2):
                k["close_odd"] = k.get("last_odd")
                k["close_ts"] = k.get("last_ts")
                k["status"] = "closed"
                n += 1; changed = True
        if changed:
            p.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    print(f"[close] {n:,} keys fechadas (close_odd congelada)")

if __name__ == "__main__":
    main()
