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
import hashlib, json, os, re, sys, time
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
FULL_SNAPSHOT_DIR = ODDS_DIR / "_snapshots"


def _atomic_write_text(path, text):
    """Escreve ``path`` por rename atômico: leitores nunca veem arquivo parcial."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _parse_pointer_at(value):
    """Timestamp de ponteiro (ISO ou legado YYYY-MM-DD_HHMM) -> UTC aware."""
    if not value:
        return None
    s = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}", s):
            return datetime.strptime(s, "%Y-%m-%d_%H%M").replace(tzinfo=BRT).astimezone(timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BRT)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def pointer_age_hours(meta, now=None):
    dt = _parse_pointer_at((meta or {}).get("at") or (meta or {}).get("ts"))
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now.astimezone(timezone.utc) - dt).total_seconds() / 3600.0)


def _jsonl_count(path):
    """Valida o JSONL inteiro e devolve o número de objetos não vazios."""
    n = 0
    with Path(path).open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL linha {lineno} não é objeto")
            n += 1
    return n


def snapshot_market_counts(path, casa=None):
    """Conta eventos por família de mercado em snapshots normalizados ou da Betano."""
    aliases = {
        "cartoes": "Cartões", "faltas": "Faltas", "chutes": "Finalizações",
        "finalizacoes": "Finalizações", "chutes no gol": "Chutes no gol",
        "escanteios": "Escanteios", "impedimentos": "Impedimentos",
        "laterais": "Laterais", "tiros de meta": "Tiros de meta",
        "desarmes": "Desarmes",
    }

    def canon(raw):
        s = str(raw or "").strip().lower()
        s = s.translate(str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc"))
        s = re.sub(r"^.*?total de\s+", "", s)
        for key, label in aliases.items():
            if s == key or s.endswith(" " + key):
                return label
        return None

    counts = {}
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            present = set()
            for key in (rec.get("mercados") or {}):
                present.add(canon(key) or key)
            for key in (rec.get("mercados_time") or {}):
                present.add(canon(key) or key)
            for rows in (rec.get("markets") or {}).values():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    c = canon((row or {}).get("market"))
                    if c:
                        present.add(c)
            for market in present:
                if market:
                    counts[market] = counts.get(market, 0) + 1
    return dict(sorted(counts.items()))


def is_close_mode():
    """True quando ODDS_WINDOW_H está setado (modo close / janela curta)."""
    return odds_window() is not None


def _market_promotion_reasons(new_counts, old_counts):
    """Detecta colapso por mercado antes de substituir o último full saudável."""
    ratio = float(os.environ.get("PROMOTE_MARKET_MIN_RATIO", "0.35"))
    base_min = int(os.environ.get("PROMOTE_MARKET_BASE_MIN", "8"))
    reasons = []
    for market, before in (old_counts or {}).items():
        before = int(before or 0)
        after = int((new_counts or {}).get(market) or 0)
        if before >= base_min and after < before * ratio:
            reasons.append(
                f"mercado {market} caiu >{(1-ratio)*100:.0f}%: {before} → {after} eventos"
            )
    return reasons


def _immutable_text_snapshot(directory, prefix, suffix, text):
    """Cria snapshot imutável por conteúdo e devolve seu Path."""
    directory = Path(directory)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
    target = directory / f"{prefix}_{digest}{suffix}"
    if target.exists():
        if target.read_text(encoding="utf-8") != text:
            raise ValueError(f"colisão de snapshot imutável: {target}")
    else:
        _atomic_write_text(target, text)
    return target


def _cleanup_snapshot_versions(directory, pattern, keep):
    """Mantém snapshot atual e anterior; falhas de limpeza nunca invalidam pointers."""
    keep = {Path(p).resolve() for p in keep if p}
    try:
        for path in Path(directory).glob(pattern):
            if path.resolve() not in keep:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def write_odds_latest(casa, file_name, n, at=None, *, promote_full=None, min_events=1):
    """Publica ponteiros somente depois de validar o snapshot completo.

    O inventário ``latest_full`` aponta para um arquivo imutável em ``_snapshots``.
    O novo arquivo é criado primeiro e o pointer é trocado por último; assim, uma
    interrupção nunca destrói o fallback anterior. Capturas com colapso relevante
    de um mercado também não substituem o último full saudável.
    """
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    if at is None:
        at = datetime.now(BRT).isoformat(timespec="seconds")
    n = int(n or 0)
    min_events = max(0, int(min_events or 0))
    mode = "close" if is_close_mode() else "full"
    payload = {"file": file_name, "n": n, "at": at, "mode": mode}
    src = ODDS_DIR / file_name

    # Capture o fallback antes de publicar o pointer da rodada atual.
    prev_meta, prev_src = resolve_odds_pointer(casa, prefer_full=True)
    if not prev_meta or prev_meta.get("_pointer") != f"{casa}_latest_full.json":
        prev_meta, prev_src = None, None

    # Chamadas intermediárias acontecem antes de o arquivo existir. Não publique
    # ponteiro sem alvo; o ponteiro anterior continua utilizável até a conclusão.
    if not src.exists():
        if n:
            raise FileNotFoundError(f"snapshot ausente: {src}")
        return payload

    actual_n = _jsonl_count(src)
    if actual_n != n:
        raise ValueError(f"snapshot {file_name}: pointer n={n}, JSONL n={actual_n}")
    new_market_counts = snapshot_market_counts(src, casa=casa)
    payload["market_counts"] = new_market_counts

    if promote_full is None:
        promote_full = mode == "full" and n >= min_events and n > 0
    # Defesa: nenhum chamador pode promover uma janela close como inventário full.
    promote_full = bool(promote_full) and mode == "full" and n >= min_events and n > 0
    promotion_reasons = []
    if promote_full and prev_src:
        old_market_counts = snapshot_market_counts(prev_src, casa=casa)
        promotion_reasons = _market_promotion_reasons(new_market_counts, old_market_counts)
        if promotion_reasons:
            promote_full = False
            payload["promotion_blocked"] = promotion_reasons

    _atomic_write_text(
        ODDS_DIR / f"{casa}_latest.json",
        json.dumps(payload, ensure_ascii=False),
    )

    if promote_full:
        snapshot_text = src.read_text(encoding="utf-8")
        stable = _immutable_text_snapshot(
            FULL_SNAPSHOT_DIR, f"{casa}_full", ".jsonl", snapshot_text
        )
        # Revalida o novo arquivo antes de trocar o pointer.
        if _jsonl_count(stable) != n:
            raise ValueError(f"snapshot imutável inválido: {stable}")
        rel = stable.relative_to(ODDS_DIR)
        full_payload = dict(payload)
        full_payload.update({
            "file": rel.as_posix(),
            "source_file": file_name,
            "promoted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        # Último passo crítico: o pointer antigo fica válido até este rename.
        _atomic_write_text(
            ODDS_DIR / f"{casa}_latest_full.json",
            json.dumps(full_payload, ensure_ascii=False),
        )
        # Preserve também a versão anterior para leitores concorrentes que já leram
        # o pointer antigo, limitando o inventário versionado a duas gerações.
        _cleanup_snapshot_versions(
            FULL_SNAPSHOT_DIR, f"{casa}_full_*.jsonl", [stable, prev_src]
        )
        payload = full_payload
    elif mode == "close" and n > 0:
        _atomic_write_text(
            ODDS_DIR / f"{casa}_latest_close.json",
            json.dumps(payload, ensure_ascii=False),
        )
    return payload

def resolve_odds_pointer(casa, prefer_full=True, max_age_h=None):
    """Resolve e valida ponteiro -> ``(meta, Path)`` ou ``(None, None)``.

    Ponteiro sem alvo, JSONL truncado, contagem divergente ou timestamp inválido
    para uma consulta com ``max_age_h`` é descartado, nunca tratado como fresco.
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
            fn = meta.get("file")
            if not fn:
                continue
            src = ODDS_DIR / fn
            if not src.is_file():
                continue
            actual_n = _jsonl_count(src)
            declared_n = int(meta.get("n") or 0)
            if actual_n != declared_n or actual_n <= 0:
                continue
        except Exception:
            continue
        age_h = pointer_age_hours(meta)
        if max_age_h is not None and (age_h is None or age_h > float(max_age_h)):
            continue
        meta = dict(meta)
        meta["_pointer"] = name
        meta["_target_valid"] = True
        meta["_actual_n"] = actual_n
        meta["_age_h"] = round(age_h, 3) if age_h is not None else None
        meta["_stale"] = bool(
            prefer_full and (name.endswith("_latest.json") or meta.get("mode") == "close" or (age_h is not None and age_h > 2.0))
        )
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
    """Grava status estruturado e retorna 0 (ok) ou 2 (soft-fail)."""
    n_events = int(n_events or 0)
    ok = (error is None) and (n_events >= min_events)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    err_s = (str(error)[:300] if error else None)

    market_counts = {}
    pointer_meta, pointer_src = (None, None)
    if error is None and n_events > 0:
        pointer_meta, pointer_src = resolve_odds_pointer(casa, prefer_full=False)
        # Não atribua a esta rodada um ponteiro antigo que sobreviveu a uma falha.
        if not pointer_meta or int(pointer_meta.get("n") or 0) != n_events:
            pointer_meta, pointer_src = None, None
        if pointer_src:
            try:
                market_counts = snapshot_market_counts(pointer_src, casa=casa)
            except Exception as exc:
                ok = False
                err_s = f"status market parse: {exc}"[:300]
        blocked = (pointer_meta or {}).get("promotion_blocked") or []
        if blocked and not is_close_mode():
            ok = False
            err_s = ("promoção full bloqueada: " + "; ".join(map(str, blocked)))[:300]
    if n_markets is None:
        n_markets = len(market_counts)

    st = {
        "casa": casa, "ok": ok,
        "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_brt": now.astimezone(BRT).strftime("%Y-%m-%d %H:%M"),
        "n_events": n_events, "n_markets": int(n_markets or 0),
        "market_counts": market_counts,
        "min_events": min_events,
        "duration_sec": round(time.time() - t0, 1) if t0 else None,
        "error": err_s,
        "error_class": classify_error(err_s) if err_s else None,
        "mode": "close" if is_close_mode() else "full",
        "sample_events": (sample or [])[:3],
        "proxy_br": bool(os.environ.get("DECODO_USER")),
        "pointer_valid": bool(pointer_src) if n_events > 0 else n_events == 0,
        "pointer_file": (pointer_meta or {}).get("file"),
        "pointer_at": (pointer_meta or {}).get("at"),
        "pointer_age_h": (pointer_meta or {}).get("_age_h"),
    }
    _atomic_write_text(
        STATUS_DIR / f"{casa}.json",
        json.dumps(st, ensure_ascii=False, indent=1),
    )
    try:
        print(f"[{casa}] status: ok={ok} n_events={n_events} (min {min_events})"
              + f" n_markets={st['n_markets']} mode={st['mode']}"
              + (f" · ERRO: {st['error']}" if st.get("error") else ""))
    except Exception:
        pass
    return 0 if ok else 2
