# -*- coding: utf-8 -*-
"""capture_common.py — utilidades compartilhadas dos fetchers de odds (P0 do brief).
1. br_proxies(): proxy Decodo com saída BRASIL, via env DECODO_USER/DECODO_PASS
   (secrets do GitHub Actions). Necessário porque betano.bet.br e 7k.bet.br
   GEO-BLOQUEIAM IP estrangeiro (testado 10/07: US/DE=403, BR=200) e os runners
   do GitHub são US/EU. Localmente (sem env) retorna None = conexão direta (já é BR).
2. finish(casa, ...): grava data/odds/_status/{casa}.json (schema do brief) e
   devolve o exit code honesto: 0 = ok (n>=min), 2 = soft-fail (0 ou poucos eventos).
   NUNCA mascarar falha com exit 0.
3. odds_window()/in_window(): "modo close" — com a env ODDS_WINDOW_H (float, horas) os
   fetchers pulam eventos fora de [agora, agora+janela] ANTES das chamadas de detalhe.
   Sem a env, comportamento idêntico ao normal (janela cheia)."""
import json, os, re, sys, time
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

def odds_window():
    """Modo close: lê ODDS_WINDOW_H (float, horas). None = env ausente/inválida = janela cheia."""
    v = os.environ.get("ODDS_WINDOW_H")
    if not v or not str(v).strip():
        return None
    try:
        w = float(str(v).strip().replace(",", "."))
    except Exception:
        return None
    return w if w > 0 else None

def _start_to_utc(v):
    """start de evento (ISO com/sem tz/'Z', epoch s/ms, '/Date(ms)/') -> datetime UTC aware, ou None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        ts = float(v)
    else:
        s = str(v).strip()
        if not s:
            return None
        m = re.match(r"^/Date\((\d+)", s)            # formato .NET '/Date(1783742400000)/'
        if m:
            s = m.group(1)
        if re.match(r"^\d{9,13}(\.\d+)?$", s):       # epoch em string (s ou ms)
            ts = float(s)
        else:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
            except Exception:
                return None
            if dt.tzinfo is None:                    # ISO sem tz: assume UTC
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    if ts > 1e11:                                    # epoch em milissegundos
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None

def in_window(start_anything, window_h):
    """True se o start cai entre agora e agora+window_h (tudo em UTC aware).
    Start que não parseia -> True (não pula o que não entende: capturar demais > de menos)."""
    dt = _start_to_utc(start_anything)
    if dt is None:
        return True
    now = datetime.now(timezone.utc)
    return now <= dt <= now + timedelta(hours=float(window_h))

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
