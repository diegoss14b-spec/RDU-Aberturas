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

ODDS_DIR = ROOT / "data" / "odds"


def is_close_mode():
    """True quando ODDS_WINDOW_H está setado (modo close / janela curta)."""
    return odds_window() is not None


def write_odds_latest(casa, file_name, n, at=None, *, promote_full=None):
    """Atualiza ponteiros de odds da casa.

    - sempre: {casa}_latest.json  (última captura qualquer — history_ingest)
    - full + n>0: também {casa}_latest_full.json  (inventário da mesa)
    - close + n>0: também {casa}_latest_close.json (só acompanhamento)

    promote_full=None → auto (full se não for close e n>0).
    Em writes intermediários (arquivo em construção), passe promote_full=False
    para não publicar inventário incompleto no board.
    """
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    if at is None:
        at = datetime.now(BRT).isoformat(timespec="seconds")
    payload = {"file": file_name, "n": int(n or 0), "at": at,
               "mode": "close" if is_close_mode() else "full"}
    blob = json.dumps(payload, ensure_ascii=False)
    (ODDS_DIR / f"{casa}_latest.json").write_text(blob, encoding="utf-8")

    if promote_full is None:
        promote_full = (not is_close_mode()) and (int(n or 0) > 0)
    if promote_full:
        (ODDS_DIR / f"{casa}_latest_full.json").write_text(blob, encoding="utf-8")
    elif is_close_mode() and int(n or 0) > 0:
        (ODDS_DIR / f"{casa}_latest_close.json").write_text(blob, encoding="utf-8")
    return payload


def resolve_odds_pointer(casa, prefer_full=True, max_age_h=None):
    """Resolve ponteiro → (meta dict, Path jsonl | None).

    prefer_full=True: board usa latest_full (fallback latest).
    prefer_full=False: history usa latest (qualquer modo).
    max_age_h: se setado, descarta ponteiro mais velho que isso (horas).
    """
    names = []
    if prefer_full:
        names.append(f"{casa}_latest_full.json")
    names.append(f"{casa}_latest.json")
    for name in names:
        ptr = ODDS_DIR / name
        if not ptr.exists():
            continue
        try:
            meta = json.loads(ptr.read_text(encoding="utf-8"))
        except Exception:
            continue
        fn = meta.get("file")
        if not fn:
            continue
        src = ODDS_DIR / fn
        if not src.exists():
            continue
        if max_age_h is not None:
            at = meta.get("at") or ""
            try:
                # aceita ISO com/sem tz ou 'YYYY-MM-DD_HHMM'
                s = str(at).replace("_", "T", 1) if "_" in str(at) and "T" not in str(at) else str(at)
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=BRT)
                age_h = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600
                if age_h > float(max_age_h):
                    continue
            except Exception:
                pass
        meta = dict(meta)
        meta["_pointer"] = name
        meta["_stale"] = prefer_full and name.endswith("_latest.json") and not (ODDS_DIR / f"{casa}_latest_full.json").exists()
        # se estamos lendo full mas o modo do pointer era close, marcar
        if prefer_full and meta.get("mode") == "close":
            meta["_stale"] = True
        return meta, src
    return None, None


def classify_error(error):
    """Classifica falha para painel/ops: Timeout | HTTP429 | Geo | Parse | Auth | Other."""
    if error is None:
        return None
    if isinstance(error, BaseException):
        name = type(error).__name__
        msg = str(error)
    else:
        name, msg = "str", str(error)
    low = (msg or "").lower()
    if "timeout" in low or name in ("Timeout", "TimeoutError", "ReadTimeout", "ConnectTimeout"):
        return "Timeout"
    if "429" in low or "rate" in low:
        return "HTTP429"
    if "403" in low or "401" in low or "geo" in low or "blocked" in low or "forbidden" in low:
        return "Geo"
    if "auth" in low or "token" in low or "jwt" in low:
        return "Auth"
    if "parse" in low or "json" in low or "empty" in low:
        return "Parse"
    if name and name not in ("str", "Exception"):
        return name
    return "Other"


def finish(casa, n_events, min_events, n_markets=None, error=None, t0=None, sample=None):
    """Grava o status estruturado e retorna o exit code (0 ok / 2 soft-fail)."""
    ok = (error is None) and (n_events >= min_events)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    err_s = (str(error)[:300] if error else None)
    st = {
        "casa": casa, "ok": ok,
        "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_brt": now.astimezone(BRT).strftime("%Y-%m-%d %H:%M"),
        "n_events": n_events, "n_markets": n_markets,
        "min_events": min_events,
        "duration_sec": round(time.time() - t0, 1) if t0 else None,
        "error": err_s,
        "error_class": classify_error(error) if error else None,
        "mode": "close" if is_close_mode() else "full",
        "sample_events": (sample or [])[:3],
        "proxy_br": bool(os.environ.get("DECODO_USER")),
    }
    (STATUS_DIR / f"{casa}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        print(f"[{casa}] status: ok={ok} n_events={n_events} (min {min_events})"
              + (f" mode={st['mode']}" if st.get("mode") else "")
              + (f" · ERRO: {st['error']}" if error else ""))
    except Exception:
        pass
    return 0 if ok else 2
