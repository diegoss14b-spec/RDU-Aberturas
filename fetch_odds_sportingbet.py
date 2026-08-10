# -*- coding: utf-8 -*-
"""fetch_odds_sportingbet.py — captura odds da Sportingbet (Entain/bwin, bet.br) dos mercados
de estatística de JOGO INTEIRO, pra a Mesa de Aberturas. API cds-api pública, sem login/cookie.
  fixtures: /cds-api/bettingoffer/fixtures?...&sportIds=4        -> ~945 jogos (id,name,startDate,liga,optionMarkets)
  detalhe : /cds-api/bettingoffer/fixture-offers?fixtureIds=<id> -> mercados aninhados (varre recursivo por 'options')
Mercado principal: "Total de Escanteios" (O/U de jogo inteiro) + "{Time} - Total de Escanteios" (por time).
Cartões/chutes abrem só PERTO do kickoff e vêm como prop binária/por-jogador (não O/U) — não forçamos em O/U.
Auth: só o querystring x-bwin-accessid (público) + fingerprint TLS do curl_cffi impersonate="chrome124".
Saída: data/odds/sportingbet_{stamp}.jsonl + sportingbet_latest.json (formato normalizado do board).

  ⚠️ _raw PRO CHASING (OPT-IN, ODDS_RAW=1): a resposta CRUA de fixture-offers de cada jogo vai pra
  data/odds/_raw/sportingbet/{eid}_{stamp}.json.gz — histórico COMPLETO de TODOS os mercados/odds ao
  longo do tempo (não só os que a Mesa usa), pro projeto de chasing de odds do Diego.
  DESLIGADO por default: na nuvem o runner é efêmero (o _raw morreria com o job) e commitar seriam
  ~5 MB/rodada. Ligar só na máquina local do chasing:  ODDS_RAW=1 python3 fetch_odds_sportingbet.py
  A pasta data/odds/_raw/ está no .gitignore."""
import sys, os, json, re, gzip, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
try:
    import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
except Exception: pass
from curl_cffi import requests as creq

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from capture_common import br_proxies, odds_window, in_window
    PROX = br_proxies("sportingbet")          # nuvem: Decodo BR via env; local: None (direto, já é BR)
except Exception:
    PROX = None
    def odds_window(): return None
    def in_window(_s, _w): return True
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
RAWDIR = OUTDIR / "_raw" / "sportingbet"
# _raw é OPT-IN via ODDS_RAW=1. Motivo (24/07): na nuvem o runner do Actions é EFÊMERO — o _raw
# seria escrito e apagado no fim do job, gastando tempo/disco sem servir pro chasing. E se fosse
# commitado, seriam ~5 MB por rodada × 12 rodadas/hora inflando o repo. Então: desligado por
# default (nuvem), ligado quando a captura roda na máquina local do chasing (ODDS_RAW=1).
RAW_ON = os.environ.get("ODDS_RAW") == "1"
if RAW_ON:
    RAWDIR.mkdir(parents=True, exist_ok=True)
BRT = timezone(timedelta(hours=-3))

# Access id público, embutido no JS da home (se um dia 403/48 bytes, refarejar em /cds-api pelo navegador).
AID = "YTRhMjczYjctNTBlNy00MWZlLTliMGMtMWNkOWQxMThmZTI2"
BASE = "https://www.sportingbet.bet.br/cds-api"
COMMON = f"x-bwin-accessid={AID}&lang=pt-br&country=BR&userCountry=BR"
HOURS = 96          # não é filtro da API (traz Latest); é só cosmético/limite lógico via take
TAKE = 400          # jogos por página do fixtures (paginamos até esgotar)
MAX_EVENTS = 200    # teto de detalhes por rodada (oferta rica primeiro)
WORKERS = 8         # detalhes em paralelo (BR direto ~0,6s/req; proxy nuvem ~3x)
MIN_EVENTS = 8      # mínimo pro finish() (abaixo = exit 2)
MIN_EFF = MIN_EVENTS  # modo close (ODDS_WINDOW_H) reduz — ver main()

# --- taxonomia canônica de mercado (mesma do board/estrelabet) ---
_STAT_MAP = {
    "escanteios": "Escanteios",
    "cartões": "Cartões", "cartoes": "Cartões",
    "faltas": "Faltas",
    "finalizações": "Finalizações", "finalizacoes": "Finalizações",
    "chutes": "Finalizações", "remates": "Finalizações",
    "chutes no gol": "Chutes no gol", "chutes a gol": "Chutes no gol",
    "impedimentos": "Impedimentos",
    "laterais": "Laterais", "arremessos laterais": "Laterais",
    "tiros de meta": "Tiros de meta",
    "desarmes": "Desarmes",
}
def _deacc(s):
    return (s.replace("ç", "c").replace("õ", "o").replace("ã", "a").replace("á", "a")
             .replace("â", "a").replace("é", "e").replace("ê", "e").replace("í", "i")
             .replace("ó", "o").replace("ô", "o").replace("ú", "u"))
def _norm_stat(stat):
    s = re.sub(r"\s+", " ", (stat or "").strip().lower())
    if s in _STAT_MAP: return _STAT_MAP[s]
    su = _deacc(s)
    for k, v in _STAT_MAP.items():
        if su == _deacc(k): return v
    return None

# "Total de {stat}"  (jogo inteiro). Exclui recortes de tempo/gol/handicap.
_MATCH_RE = re.compile(r"^total\s+(?:de\s+)?(.+)$", re.I)
# "{Time} - Total de {stat}"  (por time).
_TEAM_RE = re.compile(r"^(.+?)\s+-\s+total\s+(?:de\s+)?(.+)$", re.I)
_EXCL = ("gol", "tempo", "1º", "2º", "1°", "2°", "handicap", "par", "ímpar", "impar",
         "exato", "faixa", "múltipl", "multipl", "75:", "fim de jogo", "primeir", "últim", "ultim")

def canon_match(nm):
    """'Total de Escanteios' (sem time) -> stat canônico, ou None."""
    if not nm: return None
    l = nm.strip().lower()
    if " - " in l: return None
    if any(b in l for b in _EXCL): return None
    m = _MATCH_RE.match(l)
    if not m: return None
    return _norm_stat(m.group(1))

def canon_team(nm):
    """'Time - Total de Escanteios' -> (stat, nome_time), ou None."""
    if not nm: return None
    m = nm.strip()
    ml = m.lower()
    if any(b in ml for b in _EXCL): return None
    mo = _TEAM_RE.match(m)
    if not mo: return None
    team, stat = mo.group(1).strip(), mo.group(2)
    c = _norm_stat(stat)
    if c and len(team) >= 2: return c, team
    return None

_LINE_RE = re.compile(r"([\d]+(?:[.,]\d+)?)")

def get(url, tries=3):
    for a in range(tries):
        try:
            r = creq.get(url, impersonate="chrome124", timeout=30, proxies=PROX)
            if r.status_code == 200 and r.content[:1] in b"[{":
                return r.json()
            if r.status_code in (403, 429):
                time.sleep(3.0 * (a + 1)); continue
        except Exception:
            pass
        time.sleep(1.2)
    return None

def iter_markets(obj):
    """Varre recursivo a resposta fixture-offers e rende cada mercado (dict com 'options' + name.value)."""
    if isinstance(obj, dict):
        opts = obj.get("options")
        nm = obj.get("name")
        if isinstance(opts, list) and isinstance(nm, dict) and nm.get("value"):
            yield obj
        for v in obj.values():
            yield from iter_markets(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_markets(v)

def ou_lines(market):
    """Extrai linhas O/U de um mercado -> {linha: {over, under}} (só onde price.odds>1)."""
    lines = {}
    for o in (market.get("options") or []):
        price = ((o.get("price") or {}).get("odds"))
        if not price or price <= 1: continue
        types = ((o.get("parameters") or {}).get("optionTypes") or [])
        low = [str(t).lower() for t in types]
        oname = ((o.get("name") or {}).get("value") or "")
        if "over" in low or oname.lower().startswith("mais"):
            side = "over"
        elif "under" in low or oname.lower().startswith("menos"):
            side = "under"
        else:
            continue
        lm = _LINE_RE.search(oname)
        if not lm: continue
        try: L = float(lm.group(1).replace(",", "."))
        except Exception: continue
        lines.setdefault(L, {})[side] = round(float(price), 2)
    return lines

def save_raw(eid, stamp, detail):
    """_raw pro chasing: fixture-offers CRU (gzip). Enxuto: só se tem conteúdo.
    No-op quando ODDS_RAW != 1 (default na nuvem — ver comentário do RAWDIR)."""
    if not RAW_ON:
        return 0
    try:
        safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(eid))
        path = RAWDIR / f"{safe}_{stamp}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(detail, fh, ensure_ascii=False)
        return path.stat().st_size
    except Exception:
        return 0

def fetch_fixtures():
    """Pagina o fixtures até esgotar (ou teto de segurança)."""
    out, skip = [], 0
    for _ in range(6):  # ~945 jogos → 3 páginas de 400; teto folgado
        url = (f"{BASE}/bettingoffer/fixtures?{COMMON}"
               f"&fixtureTypes=Standard&state=Latest&offerMapping=Filtered"
               f"&offerCategories=Gridable&sportIds=4&skip={skip}&take={TAKE}&sortBy=StartDate")
        d = get(url)
        fx = (d or {}).get("fixtures") or []
        if not fx: break
        out += fx
        if len(fx) < TAKE: break
        skip += TAKE
    return out

def league_of(f):
    for k in ("competition", "tournament", "league", "region"):
        v = f.get(k)
        if isinstance(v, dict):
            nm = (v.get("name") or {}).get("value")
            if nm: return nm
    return ""

def main():
    now = datetime.now(BRT)
    stamp = now.strftime("%Y-%m-%d_%H%M")
    fixtures = fetch_fixtures()
    print(f"[sportingbet] fixtures: {len(fixtures)} jogos")
    if not fixtures:
        # lista vazia = TRANSPORTE morto (a lista não encolhe em modo close) —
        # sentinela None distingue de "rodou e capturou 0" e aciona o flip de rota
        return None

    _wh = odds_window()
    if _wh is not None:   # modo close: filtra ANTES do sort/cap
        global MIN_EFF
        _tot = len(fixtures)
        fixtures = [f for f in fixtures if in_window(f.get("startDate"), _wh)]
        MIN_EFF = (min(MIN_EVENTS, 1) if fixtures else 0)
        print(f"[sportingbet] modo close: janela {_wh:g}h -> {len(fixtures)} de {_tot} eventos")

    # oferta rica primeiro (mais optionMarkets no grid) e capa
    fixtures.sort(key=lambda f: len(f.get("optionMarkets") or []), reverse=True)
    fixtures = fixtures[:MAX_EVENTS]

    out_path = OUTDIR / f"sportingbet_{stamp}.jsonl"
    from capture_common import write_odds_latest

    # detalhes em PARALELO
    from concurrent.futures import ThreadPoolExecutor
    ids = [f.get("id") for f in fixtures if f.get("id")]
    def _detail(fid):
        url = (f"{BASE}/bettingoffer/fixture-offers?{COMMON}"
               f"&fixtureIds={fid}&offerMapping=All")
        return fid, get(url)
    details = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fid, d in ex.map(_detail, ids):
            if d: details[fid] = d
    n_det = len(details)

    raw_bytes = 0
    n_out = n_corners = n_cards = 0
    f = open(out_path, "w", encoding="utf-8")
    for fx in fixtures:
        eid = fx.get("id")
        d = details.get(eid)
        if not d: continue
        raw_bytes += save_raw(eid, stamp, d)

        merc, merc_t = {}, {}
        has_card_prop = False
        for m in iter_markets(d):
            mname = (m.get("name") or {}).get("value") or ""
            ml = mname.lower()
            if "cart" in ml:  # cartões: pode ser O/U ("Total de Cartões") OU prop binária perto do jogo
                has_card_prop = True
            cm = canon_match(mname)
            ct = None if cm else canon_team(mname)
            if not cm and not ct: continue
            lines = ou_lines(m)
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if not arr: continue
            if cm:
                if len(arr) > len(merc.get(cm, [])): merc[cm] = arr
            else:
                c2, team = ct
                prev = {x["linha"]: x for x in merc_t.get(c2, {}).get(team, [])}
                for row in arr: prev[row["linha"]] = row
                merc_t.setdefault(c2, {})[team] = [prev[L] for L in sorted(prev)]

        merc = {k: v for k, v in merc.items() if v}
        if not merc and not merc_t: continue
        if "Escanteios" in merc or "Escanteios" in merc_t: n_corners += 1
        if "Cartões" in merc or "Cartões" in merc_t or has_card_prop: n_cards += 1

        name = ((d if isinstance(d, dict) else {}) and (fx.get("name") or {}).get("value")) or ""
        # start em epoch ms (contrato do board)
        start_ms = None
        try:
            dt = datetime.fromisoformat((fx.get("startDate") or "").replace("Z", "+00:00"))
            start_ms = int(dt.timestamp() * 1000)
        except Exception:
            start_ms = fx.get("startDate")
        rec = {"casa": "Sportingbet", "event_id": eid, "name": name,
               "league": league_of(fx), "start": start_ms,
               "captured_at": now.isoformat(timespec="seconds"), "mercados": merc}
        if merc_t: rec["mercados_time"] = merc_t
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        n_out += 1
    f.close()

    promote = None  # deixa write_odds_latest decidir (full: promove se n>=MIN_EFF)
    write_odds_latest("sportingbet", out_path.name, n_out,
                      at=now.isoformat(timespec="seconds"), promote_full=promote, min_events=MIN_EFF)
    print(f"[sportingbet] {n_det} detalhes · {n_out} jogos com mercado salvos "
          f"({n_corners} c/ escanteios, {n_cards} c/ cartões) · _raw {raw_bytes/1024:.0f} KB · {out_path.name}")
    return n_out

CASA = "sportingbet"
if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish, br_proxies
    try:
        _n = main()
        if _n is None:
            # lista não veio pela rota primária → tenta a OUTRA rota no MESMO run
            # (simétrico: direto→proxy se PROXY_OFF lista a casa; proxy→direto se o
            # Decodo morrer — pane de 09/08: 407 derrubou 5 casas por 10h em silêncio)
            _rota2 = "proxy BR" if PROX is None else "DIRETO"
            print(f"[{CASA}] lista vazia na rota primária — tentando {_rota2} no mesmo run")
            globals()["PROX"] = br_proxies(CASA, force=True) if PROX is None else None
            _n = main()
        if _n is None:
            finish(CASA, 0, MIN_EFF, error="lista vazia nas DUAS rotas (direto e proxy)", t0=_t0)
            sys.exit(2)
        sys.exit(finish(CASA, _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish(CASA, 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
