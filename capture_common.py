# -*- coding: utf-8 -*-
"""capture_common.py — utilidades compartilhadas dos fetchers de odds (P0 do brief).
1. br_proxies(): proxy Decodo com saída BRASIL, via env DECODO_USER/DECODO_PASS
   (secrets do GitHub Actions). Necessário porque betano.bet.br e 7k.bet.br
   GEO-BLOQUEIAM IP estrangeiro (testado 10/07: US/DE=403, BR=200) e os runners
   do GitHub são US/EU. Localmente (sem env) retorna None = conexão direta (já é BR).
2. finish(casa, ...): grava data/odds/_status/{casa}.json (schema do brief) e
   devolve o exit code honesto: 0 = ok (n>=min), 2 = soft-fail (0 ou poucos eventos).
   NUNCA mascarar falha com exit 0."""
import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
STATUS_DIR = ROOT / "data" / "odds" / "_status"
BRT = timezone(timedelta(hours=-3))

def br_proxies():
    """Proxy residencial BR (Decodo) p/ furar geo-block na nuvem. None se sem env (= local/direto)."""
    user = os.environ.get("DECODO_USER"); pw = os.environ.get("DECODO_PASS")
    if not user or not pw:
        return None
    ep = os.environ.get("DECODO_ENDPOINT", "gate.decodo.com:7000")
    u = f"user-{user}-country-br"
    url = f"http://{u}:{pw}@{ep}"
    return {"http": url, "https": url}

def playwright_proxy():
    """Config de proxy pro Playwright (7k). None se sem env."""
    user = os.environ.get("DECODO_USER"); pw = os.environ.get("DECODO_PASS")
    if not user or not pw:
        return None
    ep = os.environ.get("DECODO_ENDPOINT", "gate.decodo.com:7000")
    return {"server": f"http://{ep}", "username": f"user-{user}-country-br", "password": pw}

def finish(casa, n_events, min_events, n_markets=None, error=None, t0=None, sample=None):
    """Grava o status estruturado e retorna o exit code (0 ok / 2 soft-fail)."""
    ok = (error is None) and (n_events >= min_events)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    st = {
        "casa": casa, "ok": ok,
        "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_brt": now.astimezone(BRT).strftime("%Y-%m-%d %H:%M"),
        "n_events": n_events, "n_markets": n_markets,
        "min_events": min_events,
        "duration_sec": round(time.time() - t0, 1) if t0 else None,
        "error": (str(error)[:300] if error else None),
        "error_class": (type(error).__name__ if isinstance(error, BaseException) else ("str" if error else None)),
        "sample_events": (sample or [])[:3],
        "proxy_br": bool(os.environ.get("DECODO_USER")),
    }
    (STATUS_DIR / f"{casa}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        print(f"[{casa}] status: ok={ok} n_events={n_events} (min {min_events})" + (f" · ERRO: {st['error']}" if error else ""))
    except Exception:
        pass
    return 0 if ok else 2
