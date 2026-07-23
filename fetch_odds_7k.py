# -*- coding: utf-8 -*-
"""fetch_odds_7k.py — captura odds do 7k (bet.br, plataforma FSSB) dos mercados de
estatística de JOGO INTEIRO, pra a Mesa de Aberturas.

Fluxo (FSSB "pulse"):
 1. host FSSB via 7k.bet.br/api/sports/anonymous-launch
 2. JWTs anônimos (authorization+session) direto do HTML da launch page do FSSB
    ('internalToken'/'sessionToken' inline no <script> do SPA — SEM browser; expiram em
    ~1 dia → frescos a cada run; Playwright virou só fallback se o HTTP puro falhar)
 3. /api/pulse/snapshot/events?lang=BR-PT -> eventos (filtra futebol+prematch+muitos mercados)
 4. por evento: markets/all?markets=<eid>:ALL descobre os MarketType._id dos mercados de
    estatística; depois markets/all?markets=<eid>:<codes> traz Selections COM preço
    (Points=linha, Name=Mais/Menos, DisplayOdds.Decimal=odd).
Saída: data/odds/7k_{stamp}.jsonl + 7k_latest.json (formato normalizado do board)."""
import sys, os, json, re, time, random
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
from capture_common import br_proxies, playwright_proxy, odds_window, in_window
PROX = br_proxies()   # nuvem: proxy BR (geo-block bet.br); local: None

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"
MIN_MARKETS = 60     # só jogos com muitos mercados têm os de estatística
# ^ diagnóstico 11/07/2026: NÃO baixar achando que rende mais jogos — no snapshot do dia
#   havia 290 candidatos ≥60 mkts (457 ≥40, 767 ≥20), mas mercados de estatística
#   (cartões/faltas) só existiam em jogos com 141+ mercados totais. 7-14 jogos/run é o
#   tamanho real da oferta do 7k; threshold menor = só mais probes à toa.
MAX_EVENTS = 120
MIN_EVENTS = 8    # mínimo pro finish() (abaixo = exit 2)
MIN_EFF = MIN_EVENTS  # modo close (ODDS_WINDOW_H) reduz — ver main()

# Jogo inteiro (sem nome de time no market)
def canon(nm):
    m = (nm or "").lower()
    if "tempo" in m or "jogador" in m or "primeiro" in m or "antes" in m: return None
    # "equipe" no nome de JOGO (ex. "Equipe com Mais Cartões") não é O/U de total
    if m.startswith("equipe ") or " equipe com " in m: return None
    # time no nome: "América MG: Total de …" → vai pra canon_team
    if ":" in (nm or ""): return None
    if "cart" in m and "total" in m: return "Cartões"
    if "falta" in m and ("total" in m or "partida" in m): return "Faltas"
    if ("chute" in m or "finaliza" in m or "remate" in m) and "total" in m and "gol" not in m: return "Finalizações"
    if "chute" in m and "gol" in m and "total" in m: return "Chutes no gol"
    if "impedi" in m and "total" in m: return "Impedimentos"
    if ("lateral" in m or "arremesso" in m) and "total" in m: return "Laterais"
    if "tiro de meta" in m and "total" in m: return "Tiros de meta"
    if "escanteio" in m or "canto" in m:
        if "total" in m or "mais/menos" in m or "mais" in m: return "Escanteios"
    return None

# Time: "América MG: Total de Faltas da Equipe Mais/Menos" / "Londrina: Chutes no gol"
_TEAM_PREFIX = re.compile(r"^([^:]{2,50})\s*:\s*(.+)$")
_TEAM_STAT_RX = [
    (re.compile(r"chutes?\s*no\s*gol|chutes?\s*a\s*gol", re.I), "Chutes no gol"),
    (re.compile(r"finaliza", re.I), "Finalizações"),
    (re.compile(r"total de chutes\b", re.I), "Finalizações"),
    (re.compile(r"(?<!\w)chutes?(?!\s*(no|a)\s*gol)\b", re.I), "Finalizações"),
    (re.compile(r"\bfaltas?\b", re.I), "Faltas"),
    (re.compile(r"\bcart[oõ]es?\b", re.I), "Cartões"),
    (re.compile(r"escanteio|cantos?", re.I), "Escanteios"),
    (re.compile(r"impedi", re.I), "Impedimentos"),
    (re.compile(r"lateral|arremesso", re.I), "Laterais"),
    (re.compile(r"tiro de meta", re.I), "Tiros de meta"),
    (re.compile(r"desarme", re.I), "Desarmes"),
]

def canon_team(nm):
    """'{Time}: Total de Faltas da Equipe Mais/Menos' → ('Faltas', 'Time')."""
    if not nm: return None
    m = nm.strip()
    mo = _TEAM_PREFIX.match(m)
    if not mo: return None
    team, rest = mo.group(1).strip(), mo.group(2).strip()
    rl = rest.lower()
    if "tempo" in rl or "jogador" in rl or "1º" in rl or "2º" in rl or "primeiro" in rl:
        return None
    for rx, c in _TEAM_STAT_RX:
        if rx.search(rest):
            return c, team
    return None

def get_host():
    try:
        r = requests.get("https://7k.bet.br/api/sports/anonymous-launch", proxies=PROX,
                         headers={"User-Agent": UA}, timeout=20)
        m = re.search(r"https://([a-z0-9-]+\.fssb\.io)", r.json().get("url", ""))
        if m: return "https://" + m.group(1)
    except Exception: pass
    return "https://prod20350-kbet-152319626.fssb.io"

def get_jwts(host):
    """JWTs anônimos SEM browser (descoberta 11/07/2026): a launch page do FSSB — a mesma
    URL que o anonymous-launch devolve (ex.: {host}/br-pt/spbk?operatorToken=logout) —
    embute os 2 tokens no HTML inline do SPA: 'internalToken' = header authorization e
    'sessionToken' = header session (customerId=-1, expira ~1 dia). 1 GET + regex resolve.
    Validado 11/07: snapshot/events 200 (959 jogos futebol prematch) e markets/all 200 com
    os tokens (403 sem). Se o HTTP puro falhar, cai no fallback Playwright antigo."""
    launch_url = host + "/br-pt/spbk?operatorToken=logout"   # chute razoável se o launch falhar
    try:
        r = requests.get("https://7k.bet.br/api/sports/anonymous-launch", proxies=PROX,
                         headers={"User-Agent": UA, "Accept": "application/json",
                                  "Referer": "https://7k.bet.br/sports"}, timeout=20)
        u = (r.json() or {}).get("url") or ""
        if u.startswith("https://"): launch_url = u
    except Exception: pass
    hh = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
          "Accept-Language": "pt-BR,pt;q=0.9", "Referer": "https://7k.bet.br/"}
    html = ""
    try:
        from curl_cffi import requests as _cr
        html = _cr.get(launch_url, impersonate="chrome124", proxies=PROX, timeout=25, headers=hh).text
    except Exception:
        try: html = requests.get(launch_url, proxies=PROX, timeout=25, headers=hh).text
        except Exception: html = ""
    mi = re.search(r"[\'\"]internalToken[\'\"]\s*:\s*[\'\"](eyJ[^\'\"]+)[\'\"]", html)
    ms = re.search(r"[\'\"]sessionToken[\'\"]\s*:\s*[\'\"](eyJ[^\'\"]+)[\'\"]", html)
    if mi and ms:
        return {"authorization": mi.group(1), "session": ms.group(1), "time-area": "1"}
    print("[7k] tokens não vieram na launch page (HTTP) — tentando fallback Playwright")
    return _get_jwts_browser()

def _get_jwts_browser():
    """fallback antigo: navega o 7k via Playwright e captura authorization+session+time-area
    de qualquer request /api. Só roda se o HTTP puro falhar E o playwright estiver instalado."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {}
    grabbed = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, proxy=playwright_proxy()) if playwright_proxy() else pw.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, locale="pt-BR")
        page = ctx.new_page()
        def on_req(r):
            h = r.headers or {}
            if "fssb.io/api" in r.url and h.get("authorization") and h.get("session"):
                grabbed.setdefault("authorization", h["authorization"])
                grabbed.setdefault("session", h["session"])
                grabbed.setdefault("time-area", h.get("time-area", "1"))
        page.on("request", on_req)
        try:
            page.goto("https://7k.bet.br/sports?bt-path=%2Fsoccer-1", timeout=45000, wait_until="domcontentloaded")
            for _ in range(20):
                if "authorization" in grabbed: break
                page.wait_for_timeout(1000)
        except Exception: pass
        ctx.close(); b.close()
    return grabbed

# _pick_family fica no NÍVEL DO MÓDULO (era aninhada em main) pra o teste exercitar o
# código REAL: o teste antigo replicava a lógica numa cópia e por isso não teria pegado
# o bug de índice da tupla de score (15/07). Não usa nada do escopo de main.
def _pick_family(fams):
    """Escolhe uma família canônica por regra determinística.

    Ordem: 2 vias ANTES de 3 vias → mais linhas O/U válidas → nome com
    'total'/'mais/menos' → market_type_id lexicograficamente menor.

    O critério de 2-vias vem PRIMEIRO (achado 15/07): a 7k publica as duas
    famílias do mesmo mercado (ex. "Escanteios Mais/Menos (2-Vias)" com 73 linhas
    E "Escanteios 3- Vias Mais/Menos" com 42). O 3-vias tem o total EXATO como
    terceiro resultado (over e under perdem) → é inútil pro flag de valor, que o
    build_board exclui. Se ele fosse escolhido por ter mais linhas num jogo, a
    Mesa mostraria a escada pior E perderia o valor daquele jogo — foi o que
    aconteceu no Corinthians×Remo. Só sobra o 3-vias quando não há 2-vias.
    """
    if not fams:
        return None, []
    scored = []
    for fk, blob in fams.items():
        by = blob["by_line"]
        n = len(by)
        nm = (blob["meta"].get("market_type_name") or "").lower()
        flat = nm.replace(" ", "").replace("-", "")
        is3 = 1 if ("3vias" in flat or "3way" in flat or "tresvias" in flat
                    or "trêsvias" in flat) else 0
        prefer = 1 if ("total" in nm or "mais/menos" in nm or "mais menos" in nm) else 0
        # penaliza nomes ambíguos de período (já filtrados em canon, reforço)
        if any(x in nm for x in ("1º", "2º", "primeiro", "segundo", "tempo", "ht")):
            prefer -= 5
        scored.append((is3, -n, -prefer, str(fk), fk, blob))
    scored.sort()
    # tupla = (is3, -n, -prefer, str(fk), fk, blob) — índices 4 e 5
    best_fk, best = scored[0][4], scored[0][5]
    arr = [best["by_line"][L] for L in sorted(best["by_line"])]
    # anota meta na 1ª linha (board pode ignorar campos extras)
    if arr:
        arr = [{**row,
                "market_type_id": best["meta"].get("market_type_id"),
                "market_type_name": best["meta"].get("market_type_name"),
                "scope": best["meta"].get("scope"),
                } for row in arr]
    dropped = [{"family": s[4], "n_lines": -s[1],
                "name": (s[5]["meta"].get("market_type_name") or "")}
               for s in scored[1:]]
    return arr, dropped


def main():
    now = datetime.now(BRT)
    host = get_host()
    jwt = get_jwts(host)
    if "authorization" not in jwt:
        print("[7k] não consegui os JWTs (HTTP puro nem fallback Playwright) — abortando"); return
    hdr = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "pt-BR",
           "authorization": jwt["authorization"], "session": jwt["session"], "time-area": jwt.get("time-area", "1")}

    def gj(path):
        try:
            r = requests.get(host + path, headers=hdr, timeout=25, proxies=PROX)
            if r.status_code == 200 and r.text[:1] in "[{": return r.json()
        except Exception: pass
        return None

    evs = gj("/api/pulse/snapshot/events?lang=BR-PT") or []
    cand = [e for e in evs if str(e.get("SportId")) == "1" and not e.get("IsLive")
            and (e.get("TotalActiveMarketsCount") or 0) >= MIN_MARKETS]
    _wh = odds_window()
    if _wh is not None:   # modo close: filtra ANTES do sort/cap e das 2 chamadas markets/all por evento
        global MIN_EFF
        _tot = len(cand)
        cand = [e for e in cand
                if in_window(e.get("StartTimeUtc") or e.get("StartDate") or e.get("StartEventDate"), _wh)]
        MIN_EFF = (min(MIN_EVENTS, 1) if cand else 0)   # janela curta: 1+ ok; lista vazia não é falha
        print(f"[7k] modo close: janela {_wh:g}h -> {len(cand)} de {_tot} eventos")
    cand.sort(key=lambda e: -(e.get("TotalActiveMarketsCount") or 0))
    cand = cand[:MAX_EVENTS]
    print(f"[7k] snapshot {len(evs)} eventos · {len(cand)} candidatos (futebol+prematch+≥{MIN_MARKETS} mercados)")

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"7k_{stamp}.jsonl"
    from capture_common import write_odds_latest
    def write_latest(n, promote=False):
        write_odds_latest("7k", out_path.name, n,
                          at=now.isoformat(timespec="seconds"), promote_full=promote, min_events=MIN_EFF)
    f = open(out_path, "w", encoding="utf-8")
    n_out = 0
    for e in cand:
        eid = e["_id"]
        allm = gj(f"/api/eventlist/eu/markets/all?markets={eid}:ALL")
        time.sleep(random.uniform(0.15, 0.3))
        if not allm: continue
        # :ALL já traz Selections com preço — não precisa 2ª ida (e cobre nomes com time no MarketType)
        # Famílias por (canon, market_type_id) — NÃO mesclar MarketTypes diferentes
        # só porque canon(name) colidiu (brief §4 auditoria 2026-07-14).
        families = {}       # canon -> {family_key: {meta, lines_by_L}}
        families_t = {}     # canon -> team -> {family_key: ...}
        for m in allm:
            mt = m.get("MarketType") or {}
            mname = mt.get("Name") or m.get("Name") or ""
            mt_id = mt.get("_id") or mt.get("Id") or mt.get("id") or mname
            c = canon(mname)
            ct = None if c else canon_team(mname)
            if not c and not ct: continue
            lines = {}
            for s in (m.get("Selections") or []):
                pts = s.get("Points")
                od = ((s.get("DisplayOdds") or {}).get("Decimal")) or s.get("TrueOdds")
                if pts is None or not od: continue
                try: od = float(od)
                except Exception: continue
                if od <= 1: continue
                side = "over" if (s.get("Side") == 1 or "mais" in (s.get("Name") or "").lower()
                                  or "acima" in (s.get("Name") or "").lower()
                                  or (s.get("OutcomeType") or "").lower() in ("acima", "over")) else "under"
                lines.setdefault(float(pts), {})[side] = round(od, 2)
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if not arr: continue
            fam_key = str(mt_id)
            meta = {
                "market_type_id": mt_id,
                "market_type_name": mname,
                "period": mt.get("Period") or m.get("Period") or "full",
                "scope": "match" if c else "team",
            }
            if c:
                slot = families.setdefault(c, {})
                prev = slot.get(fam_key)
                if prev is None:
                    slot[fam_key] = {"meta": meta, "by_line": {row["linha"]: row for row in arr}}
                else:
                    # mesma família: mescla linhas (mesmo MarketType)
                    for row in arr:
                        prev["by_line"][row["linha"]] = row
            else:
                c2, team = ct
                slot = families_t.setdefault(c2, {}).setdefault(team, {})
                prev = slot.get(fam_key)
                if prev is None:
                    slot[fam_key] = {"meta": meta, "by_line": {row["linha"]: row for row in arr}}
                else:
                    for row in arr:
                        prev["by_line"][row["linha"]] = row

        merc, merc_t = {}, {}
        fam_log = []
        for c, fams in families.items():
            arr, dropped = _pick_family(fams)
            if arr:
                merc[c] = arr
            if dropped:
                fam_log.append({"canon": c, "kept": arr[0].get("market_type_name") if arr else None,
                                "dropped": dropped})
        for c2, by_team in families_t.items():
            for team, fams in by_team.items():
                arr, dropped = _pick_family(fams)
                if arr:
                    merc_t.setdefault(c2, {})[team] = arr
        if not merc and not merc_t: continue
        name = (e.get("EventName") or "").replace(" vs ", " - ")
        # a API da 7k às vezes devolve o visitante DUPLICADO: "Viborg - OB Odense - OB Odense"
        # → o board via 3 partes e não extraía home/away (jogo não fundia). Colapsa o último
        # segmento repetido (23/07). Só age quando há ≥3 segmentos e os 2 últimos são iguais.
        _segs = [s.strip() for s in name.split(" - ")]
        if len(_segs) >= 3 and _segs[-1] and _segs[-1] == _segs[-2]:
            name = " - ".join(_segs[:-1])
        rec = {"casa": "7k", "event_id": eid, "name": name, "league": e.get("LeagueName"),
               "start": e.get("StartEventDate"), "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
               "mercados": merc}
        if merc_t: rec["mercados_time"] = merc_t
        if fam_log: rec["_family_choices"] = fam_log
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
    f.close(); write_latest(n_out, promote=None)
    print(f"[7k] {n_out} jogos com mercado de estatística salvos em {out_path.name}")
    return n_out

if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish
    try:
        _n = main() or 0
        sys.exit(finish("7k", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("7k", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
