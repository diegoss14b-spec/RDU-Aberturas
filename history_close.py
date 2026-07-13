# -*- coding: utf-8 -*-
"""history_close.py — congela a ODD DE FECHAMENTO (só pré-kickoff).

Regras (P1):
  - fecha key open quando agora ≥ kickoff − 2min
  - close_odd = last_odd **somente** se last_ts < kickoff − ε (45s)
  - se last_ts é pós-kickoff → no_close (não inventa close com odd live)
  - grava capture_quality via history_quality
"""
import json, sys, glob
from pathlib import Path
from datetime import datetime, timezone, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from history_quality import (
    compute_capture_quality, should_close_key, is_pre_kickoff,
    parse_ts, ensure_aware, BRT, CLOSE_EPS,
)

HIST = ROOT / "data" / "odds_history"


def main():
    now = datetime.now(BRT)
    n_closed = n_no_close = 0
    for kf in sorted(glob.glob(str(HIST / "keys" / "*.json"))):
        p = Path(kf)
        keys = json.loads(p.read_text(encoding="utf-8"))
        changed = False
        for key, k in keys.items():
            if key.startswith("__"):
                continue
            if k.get("status") != "open":
                # reclassifica settled/closed antigas se faltar quality
                if not k.get("capture_quality"):
                    k["capture_quality"] = compute_capture_quality(k, now)
                    changed = True
                continue
            if not should_close_key(k, now):
                k["capture_quality"] = compute_capture_quality(k, now)
                continue

            last_ts = k.get("last_ts")
            last_odd = k.get("last_odd")
            ko = k.get("kickoff")
            if last_odd and is_pre_kickoff(last_ts, ko):
                k["close_odd"] = last_odd
                k["close_ts"] = last_ts
                k["status"] = "closed"
                n_closed += 1
            else:
                # não usa odd pós-kickoff como close
                k["close_odd"] = None
                k["close_ts"] = None
                k["status"] = "closed"
                n_no_close += 1
            k["capture_quality"] = compute_capture_quality(k, now)
            changed = True
        if changed:
            p.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    print(
        f"[close] {n_closed:,} com close pré-KO · {n_no_close:,} closed sem close válido (no_close)"
    )


if __name__ == "__main__":
    main()
