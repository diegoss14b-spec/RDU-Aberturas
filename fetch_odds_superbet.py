# -*- coding: utf-8 -*-
"""fetch_odds_superbet.py — captura odds da Superbet (bet.br) dos mercados de estatística
de JOGO INTEIRO, pra a Mesa de Aberturas. API pública offer (Fastly):
  by-date: /v2/pt-BR/events/by-date?currentStatus=active&offerState=prematch&sportId=5&startDate&endDate
  detalhe: /v2/pt-BR/events/{eventId}  -> campo 'odds' [{marketName,name,price,...}]
  struct : /v2/pt-BR/struct            -> nomes de torneio/categoria
⚠️ o CDN manda header 'content-encoding: gzip' às vezes mentiroso → ler bytes CRUS e
decodificar (gzip senão plain). marketName limpo tipo 'Total de Cartões'; outcome
'Mais de X.5'/'Menos de X.5'. Saída: data/odds/superbet_{stamp}.jsonl + superbet_latest.json,
mesmo formato normalizado do board. pythonw-safe, pacing educado."""
import sys, os, json, gzip, re, time, random, math, threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
try:
    import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
except Exception: pass
import requests
from capture_common import odds_window, _start_to_utc, _atomic_write_text

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BASE = "https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
     "Accept": "application/json", "Origin": "https://superbet.bet.br", "Referer": "https://superbet.bet.br/"}
BRT = timezone(timedelta(hours=-3))
DAYS = 4          # janela de captura (hoje + N dias)
# A lista by-date inclui apenas odds principais, não o inventário de estatísticas.
# Todos os eventos no horizonte precisam de detalhe: cortar os primeiros N jogos
# truncava a rodada às 13h em 12/09, apesar dos mercados abertos mais tarde.
HORIZON_H = float(__import__("os").environ.get("SUPERBET_HORIZON_H", "30"))
WORKERS = max(1, int(__import__("os").environ.get("SUPERBET_WORKERS", "3")))
CAPTURE_BUDGET_SECONDS = 840  # termina honestamente antes do timeout externo de 900s
MIN_EVENTS = 10   # mínimo pro finish() (abaixo = exit 2)
MIN_EFF = MIN_EVENTS  # modo close (ODDS_WINDOW_H) reduz — ver main()

def dec(raw):
    try:
        d = gzip.decompress(raw)
        if d[:1] in b"{[": return d.decode("utf-8", "replace")
    except Exception: pass
    return raw.decode("utf-8", "replace")

# 22/08 — DIAGNÓSTICO: o full da CI salvou 134 jogos (kickoff até 15:00) enquanto a
# mesma captura rodada no Mac (IP BR, direto) salvou 230 (39 com Faltas, noite incluída).
# O get() engolia 404/erro em silêncio e ninguém sabia ONDE a lista morria. Conta por
# status e grava em _status/superbet_diag.json (lido no ops/auditoria).
_DIAG = {"http_200": 0, "http_404": 0, "http_other": 0, "exc": 0, "detail_requests": 0,
         "first_fail": None, "last_fail": None}
_DIAG_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()
_SESSIONS = []
NOT_FOUND = object()  # Não confundir HTTP 404 com HTTP 200 cujo corpo é null.


class CaptureIncomplete(RuntimeError):
    """Inventário ou detalhes incompletos: não publicar como full saudável."""


class CaptureBudgetExceeded(CaptureIncomplete):
    """Orçamento encerrado; repetir o full inteiro não resolve a cobertura."""


def _session():
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(H)
        _THREAD_LOCAL.session = session
        with _DIAG_LOCK:
            _SESSIONS.append(session)
    return session


def _close_sessions():
    with _DIAG_LOCK:
        sessions, _SESSIONS[:] = list(_SESSIONS), []
    for session in sessions:
        session.close()
    if hasattr(_THREAD_LOCAL, "session"):
        del _THREAD_LOCAL.session


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaptureBudgetExceeded("captura parcial: orçamento Superbet de 840s esgotado")
    return remaining


def _diag_http(key, failure=None):
    with _DIAG_LOCK:
        _DIAG[key] += 1
        if failure:
            _DIAG["first_fail"] = _DIAG["first_fail"] or failure
            _DIAG["last_fail"] = failure


def get(url, tries=4, *, deadline=None, allow_not_found=False):
    deadline = deadline if deadline is not None else time.monotonic() + CAPTURE_BUDGET_SECONDS
    last_error = "sem resposta"
    for attempt in range(tries):
        remaining = _remaining(deadline)
        try:
            # Sessão não compartilhada entre fios; conexão/requisição/retry respeitam
            # o saldo. O timeout externo continua sendo a última barreira de 900s.
            timeout = min(25.0, remaining / 2)
            if allow_not_found:
                _diag_http("detail_requests")
            with _session().get(url, timeout=(timeout, timeout), stream=True) as r:
                body = dec(r.raw.read()).strip()
                if r.status_code == 200:
                    _diag_http("http_200")
                    return json.loads(body)
                if r.status_code == 404:
                    _diag_http("http_404")
                    if allow_not_found:
                        return NOT_FOUND  # retirado pela fonte, estado terminal explícito
                else:
                    _diag_http("http_other", f"{r.status_code} {url[-40:]}")
                last_error = f"HTTP {r.status_code}"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            _diag_http("exc", f"exc {type(e).__name__} {url[-40:]}")
        if attempt + 1 < tries:
            time.sleep(min(1.5, _remaining(deadline)))
    _remaining(deadline)
    raise CaptureIncomplete(f"falha consultando {url[-80:]}: {last_error}")

# Allowlist EXATA de marketName de JOGO INTEIRO.
# Totais por time ("Total de Finalizações América MG") vão em mercados_time
# (UI: coluna do mandante/visitante) — não misturam com a linha da partida.
_EXACT = {
    "total de cartões": "Cartões",
    "total de cartoes": "Cartões",
    "total de faltas": "Faltas",
    "total de escanteios": "Escanteios",
    "total de finalizações": "Finalizações",
    "total de finalizacoes": "Finalizações",
    "total de chutes": "Finalizações",
    "total de chutes no gol": "Chutes no gol",
    "total de impedimentos": "Impedimentos",
    "total de arremessos laterais": "Laterais",
    "total de laterais": "Laterais",
    "total de tiros de meta": "Tiros de meta",
    "total de desarmes": "Desarmes",
}
# Padrões de TOTAL POR TIME (UI Superbet: "Total de … da Equipe" com aba por time).
# A) "Total de Finalizações América MG"
# B) "América MG - Total de Faltas" / "América MG - Chutes no Gol" / "América MG - Desarmes"
_TEAM_STAT_SUFFIX = [  # prefixo "total de … " + time
    ("total de chutes no gol ", "Chutes no gol"),
    ("total de finalizações ", "Finalizações"),
    ("total de finalizacoes ", "Finalizações"),
    ("total de arremessos laterais ", "Laterais"),
    ("total de tiros de meta ", "Tiros de meta"),
    ("total de cartões ", "Cartões"),
    ("total de cartoes ", "Cartões"),
    ("total de faltas ", "Faltas"),
    ("total de escanteios ", "Escanteios"),
    ("total de chutes ", "Finalizações"),
    ("total de impedimentos ", "Impedimentos"),
    ("total de laterais ", "Laterais"),
    ("total de desarmes ", "Desarmes"),
]
# após "Time - …": trecho canônico (ordem: chutes no gol antes de chutes)
_TEAM_STAT_AFTER = [
    (re.compile(r"^total de chutes no gol$|^chutes no gol$|^chutes a gol$", re.I), "Chutes no gol"),
    (re.compile(r"^total de finaliza[cç][oõ]es$|^finaliza[cç][oõ]es$", re.I), "Finalizações"),
    (re.compile(r"^total de faltas$|^faltas$", re.I), "Faltas"),
    (re.compile(r"^total de cart[oõ]es$|^cart[oõ]es$", re.I), "Cartões"),
    (re.compile(r"^total de escanteios$|^escanteios$|^cantos$", re.I), "Escanteios"),
    (re.compile(r"^total de impedimentos$|^impedimentos$", re.I), "Impedimentos"),
    (re.compile(r"^total de (arremessos )?laterais$|^laterais$", re.I), "Laterais"),
    (re.compile(r"^total de tiros de meta$|^tiros de meta$", re.I), "Tiros de meta"),
    (re.compile(r"^total de desarmes$|^desarmes$", re.I), "Desarmes"),
    (re.compile(r"^total de chutes$|^chutes$", re.I), "Finalizações"),
]
_TEAM_REJECT = re.compile(r"vermelh|1[ºo°]\s*tempo|2[ºo°]\s*tempo|minuto|asi[aá]tic|impar|ímpar|jogador|goleiro", re.I)
# "Time - resto" (evita combos com ; e nomes de jogador "Sobrenome, Nome - …")
_TEAM_DASH = re.compile(r"^([^,;]{2,40}?)\s+[-–—]\s+(.+)$")

def canon(mn):
    """Só aceita mercado de total de jogo inteiro com nome exato (sem sufixo de time)."""
    if not mn: return None
    m = mn.strip()
    if ";" in m or "&" in m: return None
    return _EXACT.get(m.lower())

def canon_team(mn):
    """Total por time → (canon, nome_time).
    Aceita 'Total de Finalizações América MG' e 'América MG - Total de Faltas'."""
    if not mn: return None
    m = mn.strip()
    if ";" in m or "&" in m: return None
    ml = m.lower()
    if ml in _EXACT: return None
    # A) Total de STAT + time
    for pref, c in _TEAM_STAT_SUFFIX:
        if ml.startswith(pref):
            team = m[len(pref):].strip()
            if not team or _TEAM_REJECT.search(team): return None
            return c, team
    # B) Time - STAT
    mo = _TEAM_DASH.match(m)
    if mo:
        team, rest = mo.group(1).strip(), mo.group(2).strip()
        if not team or _TEAM_REJECT.search(team) or _TEAM_REJECT.search(rest):
            return None
        # rejeita se "time" parece jogador (muito curto com iniciais raras ok; vírgula já barrada)
        for rx, c in _TEAM_STAT_AFTER:
            if rx.match(rest.strip()):
                return c, team
    return None

OUTC = re.compile(r"(mais|menos) de\s+([\d.]+)", re.I)

def is_full_game(mn):
    """Compat: True se marketName está na allowlist de jogo inteiro."""
    return canon(mn) is not None

def select_events(events, now, horizon_h, close_h=None):
    """Inventário inteiro, deduplicado e ordenado, sem teto de quantidade."""
    if not math.isfinite(horizon_h) or horizon_h <= 0:
        raise CaptureIncomplete("horizonte Superbet deve ser positivo e finito")
    if close_h is not None and (not math.isfinite(close_h) or close_h <= 0):
        raise CaptureIncomplete("janela close Superbet deve ser positiva e finita")
    if not isinstance(events, list) or any(not isinstance(e, dict) or not e.get("eventId") for e in events):
        raise CaptureIncomplete("inventário Superbet inválido")
    start = now.astimezone(timezone.utc)
    end = start + timedelta(hours=min(horizon_h, close_h) if close_h is not None else horizon_h)
    unique = {}
    for event in events:
        kickoff = _kickoff(event)
        # Timestamp desconhecido continua elegível: não esconder jogo por erro de data.
        if kickoff is not None and not start <= kickoff <= end:
            continue
        unique.setdefault(str(event["eventId"]), event)
    return sorted(unique.values(), key=lambda e: (_kickoff(e) or datetime.max.replace(tzinfo=timezone.utc), str(e["eventId"])))


def _kickoff(event):
    for key in ("unixDateMillis", "utcDate", "matchDate"):
        kickoff = _start_to_utc(event.get(key))
        if kickoff is not None:
            return kickoff
    return None


def normalize_event(e, ev, tnames, captured_at):
    """Preserva o parser dos totais de jogo/time e exige um par de lados válido."""
    if not isinstance(ev, dict) or str(ev.get("eventId")) != str(e["eventId"]):
        raise CaptureIncomplete("identidade divergente no detalhe Superbet")
    odds = ev.get("odds")
    if not isinstance(odds, list) or any(not isinstance(o, dict) for o in odds):
        raise CaptureIncomplete("odds inválidas no detalhe Superbet")
    mk, mk_t = {}, {}
    for o in odds:
        if o.get("status") != "active": continue
        mo = OUTC.search(o.get("name") or o.get("info") or "")
        if not mo: continue
        side = "over" if mo.group(1).lower() == "mais" else "under"
        try:
            line = float(mo.group(2))
            price = float(o.get("price"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(line) or not math.isfinite(price) or price <= 1: continue
        mn = o.get("marketName")
        c = canon(mn)
        if c:
            mk.setdefault(c, {}).setdefault(line, {})[side] = round(price, 2)
            continue
        ct = canon_team(mn)
        if ct:
            c, team = ct
            mk_t.setdefault(c, {}).setdefault(team, {}).setdefault(line, {})[side] = round(price, 2)
    merc = {}
    for c, lines in mk.items():
        arr = [{"linha": L, "over": v["over"], "under": v["under"]}
               for L, v in sorted(lines.items()) if "over" in v and "under" in v]
        if arr: merc[c] = arr
    merc_t = {}
    for c, teams in mk_t.items():
        by_team = {}
        for team, lines in teams.items():
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if arr: by_team[team] = arr
        if by_team: merc_t[c] = by_team
    if not merc and not merc_t: return None
    kickoff = _kickoff(ev) or _kickoff(e)
    rec = {"casa": "Superbet", "event_id": e["eventId"],
           "name": (ev.get("matchName") or e.get("matchName") or "").replace("·", " - "),
           "league": tnames.get(str(ev.get("tournamentId")), ""),
           "start": int(kickoff.timestamp() * 1000) if kickoff else None,
           "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
    if merc_t: rec["mercados_time"] = merc_t
    return rec


def _fetch_event(event, deadline):
    det = get(f"{BASE}/events/{event['eventId']}", deadline=deadline, allow_not_found=True)
    captured_at = datetime.now(BRT)  # horário desta resposta, não do início do full
    if det is NOT_FOUND:
        return None, captured_at
    if not isinstance(det, dict) or not isinstance(det.get("data"), list) or len(det["data"]) != 1:
        raise CaptureIncomplete("resposta de detalhe vazia ou inválida")
    ev = det["data"][0]
    if not isinstance(ev, dict) or str(ev.get("eventId")) != str(event["eventId"]):
        raise CaptureIncomplete("identidade divergente no detalhe Superbet")
    delay = min(random.uniform(0.2, 0.4), max(0, deadline - time.monotonic()))
    if delay: time.sleep(delay)
    return ev, captured_at


def main():
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    from capture_common import write_odds_latest
    global MIN_EFF
    MIN_EFF = MIN_EVENTS
    now = datetime.now(BRT)
    deadline = time.monotonic() + CAPTURE_BUDGET_SECONDS
    _wh = odds_window()
    progress = {"n_list": 0, "n_eligible": 0, "n_scheduled": 0, "n_det": 0,
                "n_ok": 0, "n_unavailable": 0, "n_failed": 0, "n_unattempted": 0,
                "n_out": 0, "complete": False, "mode": "close" if _wh is not None else "full",
                "at": now.isoformat(timespec="seconds"), "horizon_h": HORIZON_H,
                "budget_seconds": CAPTURE_BUDGET_SECONDS, "workers": WORKERS,
                "last_kickoff_saved": None, "last_kickoff_processed": None,
                "failed_events": [], "unavailable_events": [], "proxy_br": False}
    with _DIAG_LOCK:
        _DIAG.update(http_200=0, http_404=0, http_other=0, exc=0, detail_requests=0, first_fail=None, last_fail=None)

    def save_progress():
        with _DIAG_LOCK:
            diag = dict(_DIAG)
        diag.update(progress, updated_at=datetime.now(BRT).isoformat(timespec="seconds"))
        _atomic_write_text(OUTDIR / "_status" / "superbet_diag.json", json.dumps(diag, ensure_ascii=False, indent=1))

    save_progress()
    try:
        return _capture(now, deadline, _wh, progress, save_progress, ThreadPoolExecutor, wait, FIRST_COMPLETED, write_odds_latest)
    except BaseException as exc:
        progress["error"] = str(exc)
        save_progress()
        raise
    finally:
        _close_sessions()


def _capture(now, deadline, _wh, progress, save_progress, executor_type, wait, first_completed, write_latest):
    global MIN_EFF
    try:
        struct = get(f"{BASE}/struct", deadline=deadline) or {}
    except CaptureBudgetExceeded:
        raise
    except CaptureIncomplete as exc:
        # Nomes de ligas são auxiliares: a falha fica visível sem ocultar odds.
        progress["struct_error"] = str(exc)
        struct = {}
    tnames = {}
    def _nm(o):  # 12/07: Superbet usa localNames{pt-BR}, NÃO name → sem isto tnames ficava vazio (liga em branco)
        ln = o.get("localNames")
        if isinstance(ln, dict): return ln.get("pt-BR") or ln.get("en") or next(iter(ln.values()), None)
        return o.get("name")
    def walk(o):
        if isinstance(o, dict):
            nm = _nm(o)
            if o.get("id") and nm:
                tnames[str(o["id"])] = nm
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    try: walk(struct.get("data", struct))
    except Exception: pass

    d0 = now.strftime("%Y-%m-%d %H:%M:%S")
    d1 = (now + timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    lst = get(f"{BASE}/events/by-date?currentStatus=active&offerState=prematch&sportId=5&startDate={d0}&endDate={d1}", deadline=deadline)
    if not isinstance(lst, dict) or not isinstance(lst.get("data"), list):
        raise CaptureIncomplete("inventário Superbet ausente ou inválido")
    events = lst["data"]
    sel = select_events(events, now, HORIZON_H, _wh)
    if _wh is not None:
        MIN_EFF = 1 if sel else 0
    progress.update(n_list=len(events), n_eligible=len(sel), n_unattempted=len(sel),
                    inventory_complete=True, first_kickoff=(_kickoff(sel[0]).isoformat() if sel and _kickoff(sel[0]) else None),
                    last_kickoff=(_kickoff(sel[-1]).isoformat() if sel and _kickoff(sel[-1]) else None))
    save_progress()
    print(f"[superbet] detalhes: todos os {len(sel)} de {len(events)} eventos (horizonte {HORIZON_H:g}h, {WORKERS} fios)", flush=True)
    # Único por execução: um retry nunca trunca o arquivo apontado por latest.
    stamp = now.strftime("%Y-%m-%d_%H%M%S_%f") + f"_{os.getpid()}_{time.time_ns()}"
    out_path = OUTDIR / f"superbet_{stamp}.jsonl"
    rows, pending, next_index = {}, {}, 0
    budget_exhausted = False
    with executor_type(max_workers=WORKERS) as executor:
        while next_index < len(sel) or pending:
            while next_index < len(sel) and len(pending) < WORKERS and not budget_exhausted:
                if time.monotonic() >= deadline:
                    budget_exhausted = True
                    break
                event = sel[next_index]
                pending[executor.submit(_fetch_event, event, deadline)] = event
                next_index += 1
                progress["n_scheduled"] += 1
                progress["n_unattempted"] = len(sel) - next_index
            if not pending:
                break
            completed, _ = wait(pending, timeout=1, return_when=first_completed)
            for future in completed:
                event = pending.pop(future)
                progress["n_det"] += 1
                kickoff = _kickoff(event)
                if kickoff:
                    previous = progress["last_kickoff_processed"]
                    progress["last_kickoff_processed"] = max(previous or "", kickoff.isoformat())
                try:
                    ev, captured_at = future.result()
                    if ev is None:
                        progress["n_unavailable"] += 1
                        progress["unavailable_events"].append(event["eventId"])
                    else:
                        rec = normalize_event(event, ev, tnames, captured_at)
                        progress["n_ok"] += 1
                        if rec:
                            rows[str(event["eventId"])] = rec
                            progress["n_out"] = len(rows)
                            if kickoff:
                                progress["last_kickoff_saved"] = max(progress["last_kickoff_saved"] or "", kickoff.isoformat())
                except Exception as exc:
                    progress["n_failed"] += 1
                    progress["failed_events"].append({"event_id": event["eventId"], "cause": str(exc), "class": type(exc).__name__})
                    if isinstance(exc, CaptureBudgetExceeded):
                        budget_exhausted = True
            save_progress()  # também durante chamadas lentas: estado não fica congelado
            if completed and (progress["n_det"] % 50 == 0 or not pending):
                print(f"[superbet] progresso {progress['n_det']}/{len(sel)} · {len(rows)} observados · {progress['n_failed']} falhas", flush=True)
    progress["complete"] = progress["n_unattempted"] == 0 and progress["n_failed"] == 0
    progress["budget_exhausted"] = budget_exhausted
    # Sem publicar ponteiros intermediários. Mesmo um partial fica isolado, com
    # nome único, e o full anterior segue intacto até completar todo inventário.
    _atomic_write_text(out_path, "".join(json.dumps(rows[str(e["eventId"])], ensure_ascii=False) + "\n"
                                      for e in sel if str(e["eventId"]) in rows))
    progress["output_file"] = out_path.name
    save_progress()
    if not progress["complete"]:
        cls = CaptureBudgetExceeded if budget_exhausted else CaptureIncomplete
        raise cls(f"captura parcial Superbet: {progress['n_failed']} falhas e {progress['n_unattempted']} eventos não consultados de {len(sel)}")
    write_latest("superbet", out_path.name, len(rows), at=datetime.now(BRT).isoformat(timespec="seconds"),
                 promote_full=None, min_events=MIN_EFF)
    print(f"[superbet] cobertura completa: {progress['n_det']} detalhes · {len(rows)} jogos com estatísticas", flush=True)
    return len(rows)

if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish
    try:
        _n = main() or 0
        sys.exit(finish("superbet", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("superbet", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
