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
import sys, os, json, gzip, re, time, random
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
from capture_common import odds_window, in_window

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BASE = "https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
     "Accept": "application/json", "Origin": "https://superbet.bet.br", "Referer": "https://superbet.bet.br/"}
BRT = timezone(timedelta(hours=-3))
DAYS = 4          # janela de captura (hoje + N dias)
MAX_EVENTS = 900  # teto de SEGURANÇA de detalhes por rodada (quem limita de verdade é o horizonte)
# 23/08 (pedido do Diego: "na aba de cartões da Superbet só mostra jogos até 17:30"): a
# lista by-date vem ORDENADA por kickoff e num domingo tem 573 jogos só de hoje — o teto
# antigo de 500 cortava a noite inteira (73 jogos de hoje além do 500º). Agora o recorte é
# por TEMPO (hoje + HORIZON_H) e os detalhes vão em WORKERS fios paralelos pra caber no
# timeout de 15min do run_capture (medido: ~0,8s/detalhe local, ~1,5s na nuvem).
HORIZON_H = float(__import__("os").environ.get("SUPERBET_HORIZON_H", "30"))
WORKERS = max(1, int(__import__("os").environ.get("SUPERBET_WORKERS", "3")))
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
_DIAG = {"http_200": 0, "http_404": 0, "http_other": 0, "exc": 0, "first_fail": None, "last_fail": None}


def get(url, tries=4):
    for _ in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=25, stream=True)
            body = dec(r.raw.read())
            if r.status_code == 200 and body[:1] in "{[":
                _DIAG["http_200"] += 1
                return json.loads(body)
            if r.status_code == 404:
                _DIAG["http_404"] += 1
                _DIAG["first_fail"] = _DIAG["first_fail"] or f"404 {url[-40:]}"
                _DIAG["last_fail"] = f"404 {url[-40:]}"
                return None
            _DIAG["http_other"] += 1
            _DIAG["first_fail"] = _DIAG["first_fail"] or f"{r.status_code} {url[-40:]}"
            _DIAG["last_fail"] = f"{r.status_code} {url[-40:]}"
        except Exception as e:
            _DIAG["exc"] += 1
            _DIAG["first_fail"] = _DIAG["first_fail"] or f"exc {type(e).__name__} {url[-40:]}"
            _DIAG["last_fail"] = f"exc {type(e).__name__} {url[-40:]}"
        time.sleep(1.5)
    return None

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

def main():
    struct = get(f"{BASE}/struct") or {}
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

    now = datetime.now(BRT)
    d0 = now.strftime("%Y-%m-%d %H:%M:%S")
    d1 = (now + timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    lst = get(f"{BASE}/events/by-date?currentStatus=active&offerState=prematch&sportId=5&startDate={d0}&endDate={d1}")
    events = (lst or {}).get("data") or []
    print(f"[superbet] by-date: {len(events)} eventos (janela {DAYS}d)")
    _wh = odds_window()
    if _wh is not None:   # modo close: kickoff = unixDateMillis (utcDate/matchDate = fallback; matchTimestamp NÃO é kickoff)
        global MIN_EFF
        _tot = len(events)
        events = [e for e in events
                  if in_window(e.get("unixDateMillis") or e.get("utcDate") or e.get("matchDate"), _wh)]
        MIN_EFF = (min(MIN_EVENTS, 1) if events else 0)   # janela curta: 1+ ok; lista vazia não é falha
        print(f"[superbet] modo close: janela {_wh:g}h -> {len(events)} de {_tot} eventos")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"superbet_{stamp}.jsonl"
    from capture_common import write_odds_latest
    def write_latest(n, promote=False):
        # intermediário: só latest (não promove full incompleto)
        write_odds_latest("superbet", out_path.name, n,
                          at=now.isoformat(timespec="seconds"),
                          promote_full=promote, min_events=MIN_EFF)
    f = open(out_path, "w", encoding="utf-8")
    n_out = n_det = 0
    # recorte por horizonte (lista já ordenada por kickoff) + teto de segurança
    _cut_ms = (now + timedelta(hours=HORIZON_H)).timestamp() * 1000
    sel = [e for e in events if e.get("eventId") and (e.get("unixDateMillis") or 0) <= _cut_ms][:MAX_EVENTS]
    print(f"[superbet] detalhes: {len(sel)} de {len(events)} (horizonte {HORIZON_H:g}h, teto {MAX_EVENTS}, {WORKERS} fios)")

    def _fetch(e):
        det = get(f"{BASE}/events/{e['eventId']}")
        time.sleep(random.uniform(0.2, 0.4))
        return e, det

    from concurrent.futures import ThreadPoolExecutor
    _ex = ThreadPoolExecutor(max_workers=WORKERS)
    for e, det in _ex.map(_fetch, sel):     # map preserva a ordem (kickoff)
        eid = e.get("eventId")
        n_det += 1
        if not det or not det.get("data"): continue
        ev = det["data"][0]
        odds = ev.get("odds") or []
        mk, mk_t = {}, {}  # jogo inteiro · por time
        for o in odds:
            if o.get("status") != "active": continue
            mo = OUTC.search(o.get("name") or o.get("info") or "")
            if not mo: continue
            side = "over" if mo.group(1).lower() == "mais" else "under"
            line = float(mo.group(2))
            price = o.get("price")
            if not price or price <= 1: continue
            mn = o.get("marketName")
            c = canon(mn)
            if c:
                mk.setdefault(c, {}).setdefault(line, {})[side] = round(price, 2)
                continue
            ct = canon_team(mn)
            if ct:
                c, team = ct
                mk_t.setdefault(c, {}).setdefault(team, {}).setdefault(line, {})[side] = round(price, 2)
        # só manter linhas com os 2 lados
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
        if not merc and not merc_t: continue
        name = (ev.get("matchName") or "").replace("·", " - ")
        league = tnames.get(str(ev.get("tournamentId")), "")
        ts = ev.get("unixDateMillis") or ev.get("matchTimestamp")
        rec = {"casa": "Superbet", "event_id": eid, "name": name, "league": league,
               "start": ts, "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"), "mercados": merc}
        if merc_t: rec["mercados_time"] = merc_t
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
    _ex.shutdown(wait=True)
    f.close()
    write_latest(n_out, promote=None)  # auto: full se n>0 e não-close
    print(f"[superbet] {n_det} detalhes buscados · {n_out} jogos com mercado de estatística salvos em {out_path.name}")
    try:
        import os as _os
        _diag = dict(_DIAG, n_list=len(events), n_det=n_det, n_out=n_out, mode=("close" if _wh is not None else "full"),
                     proxy_br=bool(_os.environ.get("DECODO_USER")) and "superbet" not in (_os.environ.get("PROXY_OFF") or "").lower(),
                     at=now.isoformat(timespec="seconds"), last_kickoff_saved=None)
        (OUTDIR / "_status").mkdir(parents=True, exist_ok=True)
        (OUTDIR / "_status" / "superbet_diag.json").write_text(json.dumps(_diag, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[superbet] diag: {_diag}")
    except Exception as _e:
        print(f"[superbet] diag não gravado: {_e!r}")
    return n_out

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
