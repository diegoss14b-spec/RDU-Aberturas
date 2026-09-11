# -*- coding: utf-8 -*-
"""fetch_odds_bet365.py — captura odds da BET365 via BetsAPI (api.b365api.com), pra
a Mesa de Aberturas. Até90 requests/420s por processo, incluindo inventário,
detalhes em lote, recuperação de FIs ausentes e retries; token compartilhado.

  lista : GET /v1/bet365/upcoming?sport_id=1&token=&page=   (50/página, ~700 eventos,
          horizonte de meses; POLUÍDO de Esoccer/SRL — filtrar por nome de liga)
  detalhe: GET /v3/bet365/prematch?token=&FI=a,b,c          (aceita ATÉ ~10 FIs por
          chamada — validado 21/07; sp.{mercado}.odds[] = {header:'Over'|'Under'|'1'|'2',
          name:'5.5'|'Over 5.5', odds:'2.000', handicap?})
  ⚠ A resposta tem seções-DICIONÁRIO (main/corners/cards_fouls/other/asian_lines/shots)
  E a lista `others` (~79 blocos, cada um com seu `sp`). MUITO mercado de partida só
  existe na LISTA (match_shots_on_target, asiáticos
  de escanteios/cartões, team_shots…). O parser varre as duas fontes.

Mercados capturados (só O/U de linha; faixas/race/exatos/3-vias ficam FORA):
  Cartões    : number_of_cards_in_match + asian_total_cards (+ team_cards por time)
  Escanteios : corners_2_way + asian_corners + asian_total_corners
               (+ team_corners por time)  — corners.corners é 3-VIAS (Over/Exactly/Under), NÃO entra
  Finalizações / Chutes no gol: match_shots / match_shots_on_target (+ team_*)
  Impedimentos / Desarmes: há aliases históricos no parser, mas sua existência
      não comprova oferta atual. O detector abaixo registra nomes desconhecidos;
      qualquer ampliação exige evidência de período, escopo e liquidação.
  Faltas de JOGO / laterais / tiros de meta: não confirmados nos payloads auditados
      em11/09. Isso NÃO prova indisponibilidade permanente na API. Mercados do
      Criar Aposta não são convertidos automaticamente em totais convencionais.
DETECTOR (rede de segurança, 21/07): parse_prematch flagra QUALQUER mercado com cara de
  O/U-de-total (Over+Under+linha .5) que não seja mapeado, nem player, nem ruído conhecido
  (gols/faixa/meio-tempo/handicap/timing) e LOGA em _status/bet365_unknown_markets.jsonl
  (dedup 1x/dia por key). NÃO adivinha canon — só observabilidade, pra nunca mais perder
  mercado por nomenclatura nova em silêncio. NADA de jogador entra (denylist _is_player_market).
⚠ Mercados enchem ao longo do dia do jogo (team_cards/match_shots vazios de madrugada):
  gravamos o que houver a cada captura; o modelo abertura→close da Mesa lida com isso.

SEGREDO: token via env BETSAPI_TOKEN (GitHub Actions secret — o repo é público, o
token JAMAIS vai em código/commit) com fallback betsapi_config.json local (gitignored).
Saída: data/odds/bet365_{stamp}.jsonl + bet365_latest.json (formato normalizado do board).

POLÍTICA DE CONSUMO (11/09 — token COMPARTILHADO):
  - FULL: intervalo mínimo de 1h, comprovado pelo ponteiro full validado.
    Até 120 eventos, 20 páginas upcoming e 90 requests por processo/420s.
  - CLOSE: até 40 jogos futuros do cache, com pelo menos 3min até o kickoff.
  - Falha parcial preserva o full anterior; orçamento esgotado não dispara
    uma segunda captura imediata. Métricas registram o consumo real.

11/09: Finalizações e Chutes no gol de jogo estão disponíveis na lista `others`.
O contrato segue v3: v4 foi comparado, mas não mostrou cobertura adicional na
amostra. Seleção exata de ligas, rotação, recuperação parcial e orçamento limitado.
Taxa de capturas bem-sucedidas NÃO representa cobertura do catálogo da casa."""
import sys, os, json, re, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import requests
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from capture_common import odds_window, in_window
except Exception:
    def odds_window(): return None
    def in_window(_s, _w): return True
OUTDIR = ROOT / "data" / "odds"; OUTDIR.mkdir(parents=True, exist_ok=True)
STATUS_DIR = OUTDIR / "_status"
GATE_F = STATUS_DIR / "bet365_gate.json"   # timestamp do último full (persistido no repo)
FIS_F = STATUS_DIR / "bet365_fis.json"     # cache FI→jogo do último full (pro close barato)
UNKNOWN_MK_F = STATUS_DIR / "bet365_unknown_markets.jsonl"  # rede de segurança: totais O/U desconhecidos
BRT = timezone(timedelta(hours=-3))
BASE = "https://api.b365api.com"
DAYS_AHEAD = 5           # janela da Mesa
MAX_EVENTS = 120         # bounded: at most 12 normal prematch batches per full
MAX_CLOSE_EVENTS = 40    # four batches; useful games first, with exploration
MAX_PAGES = 20           # upcoming pagina de 50 em 50 (~700 eventos = 14 páginas)
FULL_EVERY_H = 1.0       # aligned with hourly full and 2h stale safety threshold
FIS_MAX_AGE_H = 12.0     # cache de FIs mais velho que isso não vale (fallback upcoming)
FI_BATCH = 10            # o /v3/prematch aceita FI=a,b,c — 10 jogos por request (21/07)
SLEEP = 0.12             # ≤10 req/s — bem abaixo do limite de 30 req/s do plano
MIN_EVENTS = 5
MIN_EFF = MIN_EVENTS     # modo close (ODDS_WINDOW_H) e skip do gate reduzem — ver main()
N_REQ = 0                # contador de requests da captura (auditoria de consumo)
REQUEST_LIMIT = 90       # shared token: includes discovery, failures and retries
DEADLINE = None          # set by main, below the orchestrator's 480s timeout
FULL_SKIPPED = False
CAPTURE_INCOMPLETE = False
HTTP_SLOTS = BoundedSemaphore(2)  # inventory, batches and single-FI recovery
REQUEST_LOCK = Lock()
from bet365_capture_plan import eligible_events, league_priority, merge_inventory, retained_quotes
from capture_discovery import DiscoveryQueue

# ligas falsas (bots/simulação) — NUNCA entram
EXCL_LEAGUE = re.compile(r"esoccer|e-?soccer|srl\b|\(srl\)|virtual|simulat", re.I)
class CaptureBudgetExceeded(RuntimeError):
    pass


def _token():
    tok = (os.environ.get("BETSAPI_TOKEN") or "").strip()
    if tok:
        return tok
    for p in (ROOT / "betsapi_config.json", ROOT.parent / "betsapi_config.json"):
        try:
            if p.exists():
                tok = (json.loads(p.read_text(encoding="utf-8")).get("token") or "").strip()
                if tok:
                    return tok
        except Exception:
            pass
    raise RuntimeError("BETSAPI_TOKEN ausente (env ou betsapi_config.json)")


@contextmanager
def _request_slot():
    """Reserve one of two HTTP slots, then charge the shared request budget."""
    global N_REQ
    remaining = DEADLINE - time.monotonic() if DEADLINE is not None else 30
    if remaining < 2 or not HTTP_SLOTS.acquire(timeout=max(0, remaining)):
        raise CaptureBudgetExceeded("orçamento de requests/tempo esgotado")
    try:
        with REQUEST_LOCK:
            remaining = DEADLINE - time.monotonic() if DEADLINE is not None else 30
            if N_REQ >= REQUEST_LIMIT or remaining < 2:
                raise CaptureBudgetExceeded("orçamento de requests/tempo esgotado")
            N_REQ += 1
            request_no = N_REQ
        yield request_no, min(30, remaining)
    finally:
        HTTP_SLOTS.release()


def get(path, params, token):
    """GET with two global slots; log only safe, low-cardinality diagnostics."""
    url = f"{BASE}{path}"
    q = dict(params); q["token"] = token
    fi_count = len(str(params["FI"]).split(",")) if params.get("FI") else 0
    # Keep a second batch try (observed to recover valid FIs), then recover
    # missing FIs individually instead of spending a third 30s on the same lot.
    attempt_limit = 2 if path == "/v3/bet365/prematch" and fi_count > 1 else 3
    local_attempts = 0
    try:
        for a in range(attempt_limit):
            delay = 1.0
            # Budget exceptions occur outside the retry handler and remain typed.
            with _request_slot() as (request_no, timeout):
                started, outcome = time.monotonic(), "unknown"
                try:
                    local_attempts += 1
                    r = requests.get(url, params=q, timeout=timeout)
                    if r.status_code == 200:
                        data = r.json()
                        outcome = "success" if data.get("success") == 1 else "success_0"
                        if data.get("success") == 1:
                            return data
                    else:
                        outcome = f"HTTP{r.status_code}"
                        if r.status_code == 429:
                            delay = 2.0 * (a + 1)
                except Exception as error:
                    # Never print exception text, URL/query, response or credentials.
                    outcome = type(error).__name__
                finally:
                    print(f"[bet365] request={request_no} path={path} FI_count={fi_count} "
                          f"attempt={a+1} result={outcome} "
                          f"elapsed_s={time.monotonic()-started:.2f}", flush=True)
            remaining = DEADLINE - time.monotonic() if DEADLINE is not None else delay
            if a + 1 < attempt_limit:
                time.sleep(min(delay, max(0, remaining)))
    except CaptureBudgetExceeded as error:
        # Local to this get() call: another worker can advance N_REQ at any time.
        error.request_started = bool(local_attempts)
        raise
    return None


# --- parse das odds -----------------------------------------------------------
_OU_NAME = re.compile(r"^(over|under)\s+([0-9.]+)$", re.I)


def _num(s):
    try:
        value = float(str(s).strip())
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError, OverflowError):
        return None


def _supported_line(value, *, signed=False):
    """Only finite integer/half lines; quarter settlement is not supported."""
    value = _num(value)
    return value is not None and (signed or value >= 0) and (value * 2).is_integer()

def _has_exact_outcome(odds_list):
    """Three-way totals must not be converted to two-way Asian/push totals."""
    return any(
        isinstance(o, dict)
        and any(str(o.get(k) or "").strip().lower().split(" ", 1)[0]
                in ("exactly", "exact") for k in ("header", "name", "handicap"))
        for o in odds_list
    )

def _put_price(lines, line, side, price):
    """Conflicting duplicate legs invalidate that block/line, not other blocks."""
    slot = lines.setdefault(line, {})
    if side in slot and slot[side] != price:
        slot["_conflict"] = True
    slot.setdefault(side, price)

def _merge_complete(target, block, sides=("over", "under")):
    """First complete block wins. Never combine one leg from separate blocks."""
    for line, pair in block.items():
        if not pair.get("_conflict") and all(side in pair for side in sides):
            target.setdefault(line, {side: pair[side] for side in sides})

def _entry_side_line(o):
    """Parse an unambiguous total; never infer a quarter or conflicting side."""
    if not isinstance(o, dict):
        return None
    header = str(o.get("header") or "").strip().lower()
    texts = [str(o.get(k) if o.get(k) is not None else "").strip()
             for k in ("name", "handicap")]
    if any("," in txt for txt in texts):
        return None
    candidates = []
    for txt in texts:
        mo = _OU_NAME.match(txt)
        if mo:
            side, line = mo.group(1).lower(), _num(mo.group(2))
            if header in ("over", "under") and header != side:
                return None
            candidates.append((side, line))
        elif header in ("over", "under") and _num(txt) is not None:
            candidates.append((header, _num(txt)))
    if not candidates or any(item != candidates[0] for item in candidates[1:]):
        return None
    side, line = candidates[0]
    return (side, line) if _supported_line(line) else None


def _collect(lines, odds_list):
    if not isinstance(odds_list, list) or _has_exact_outcome(odds_list):
        return
    for o in odds_list:
        if not isinstance(o, dict):
            continue
        sl = _entry_side_line(o)
        price = _num(o.get("odds"))
        if sl is None or price is None or price <= 1:
            continue
        side, line = sl
        _put_price(lines, line, side, price)


# ------------------------------------------------- HANDICAP (2 vias, casa/fora)
# Pedido do Diego (29/07): acompanhar a movimentação do handicap ASIÁTICO de
# cartões da bet365. O mercado `asian_handicap_cards` existe em 100% dos jogos
# que têm cartões na casa (medido 29/07: 11 de 11) e abre 33-36h antes do
# kickoff — mais cedo que a Pinnacle, que só abre no dia (mediana 13,9h).
#
# ⚠ O formato NÃO é over/under: vem `header` '1' (mandante) / '2' (visitante) e
# cada lado traz o PRÓPRIO handicap, espelhado ('+0.5' e '-0.5'). Pra ter uma
# chave estável no banco de odds, a `linha` gravada é SEMPRE do ponto de vista
# do MANDANTE — o lado '2' entra com o sinal invertido. Sem isso o mesmo
# mercado viraria duas linhas diferentes e o gráfico mostraria duas séries
# soltas em vez de um par.
_HAND_NUM = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _hand_line(txt):
    value = str(txt if txt is not None else "").strip()
    if "," in value or not _HAND_NUM.match(value):
        return None
    line = _num(value)
    return line if _supported_line(line, signed=True) else None


def _collect_hand(lines, odds_list):
    if not isinstance(odds_list, list) or _has_exact_outcome(odds_list):
        return
    for o in odds_list:
        if not isinstance(o, dict):
            continue
        header = str(o.get("header") or "").strip()
        side = "casa" if header == "1" else "fora" if header == "2" else None
        raw_line = o.get("handicap")
        if raw_line is None or not str(raw_line).strip():
            raw_line = o.get("name")
        line = _hand_line(raw_line)
        price = _num(o.get("odds"))
        if side is None or line is None or price is None or price <= 1:
            continue
        if side == "fora":
            line = -line
        _put_price(lines, line + 0.0 if line else 0.0, side, price)


def _collect_team(per_team, odds_list, home, away):
    if not isinstance(odds_list, list) or _has_exact_outcome(odds_list):
        return
    for o in odds_list:
        if not isinstance(o, dict):
            continue
        header = str(o.get("header") or "").strip()
        team = home if header == "1" else away if header == "2" else None
        # The team's 1/2 header identifies ownership, not the O/U side.
        sl = _entry_side_line(dict(o, header=""))
        price = _num(o.get("odds"))
        if team is None or sl is None or price is None or price <= 1:
            continue
        side, line = sl
        _put_price(per_team.setdefault(team, {}), line, side, price)


# ⚠ ACHADO 21/07: além das seções-DICIONÁRIO (main, corners, cards_fouls, other,
# asian_lines, shots…), a resposta traz `others` = LISTA de ~79 blocos, cada um com
# seu próprio `sp`. O parser antigo lia só os dicionários e IGNORAVA a lista inteira —
# por isso "Finalizações/Chutes no gol × bet365" dava 0. Dentro de `others` moram:
#   match_shots_on_target (O/U 9.5 do JOGO), alternative_corners (63 odds!),
#   asian_total_corners/cards, team_shots, team_shots_on_target, etc.
# Agora varremos AMBAS as fontes, casando por NOME de mercado (a seção varia).
# mercado (nome BetsAPI) → canon da Mesa — total da PARTIDA, só O/U de linha
MATCH_MARKETS = {
    "number_of_cards_in_match": "Cartões",
    "asian_total_cards": "Cartões",
    "corners_2_way": "Escanteios",
    # alternative_corners is three-way Over/Exactly/Under: intentionally excluded.
    "asian_corners": "Escanteios",
    "asian_total_corners": "Escanteios",
    "match_shots": "Finalizações",              # populated in `others` (verified 11/09)
    "match_shots_on_target": "Chutes no gol",
    # IMPEDIMENTOS e DESARMES: a bet365 abre no "especiais/outros" e, QUANDO abrem, vêm
    # na API (lista `others`). Hoje (21/07) raramente estão abertos, então a nomenclatura
    # EXATA não dá pra observar ao vivo. Mapeamos TODAS as variantes plausíveis do padrão
    # BetsAPI já confirmado nos outros mercados (match_/total_/asian_total_/.._2_way/
    # number_of_.._in_match) — é barato e não colide com nada. Se a bet365 abrir com um
    # nome fora desta lista, o DETECTOR (parse_prematch) loga o nome oficial pra mapear.
    "match_offsides": "Impedimentos",
    "total_offsides": "Impedimentos",
    "offsides_2_way": "Impedimentos",
    "asian_total_offsides": "Impedimentos",
    "number_of_offsides_in_match": "Impedimentos",
    "match_tackles": "Desarmes",
    "total_tackles": "Desarmes",
    "tackles_2_way": "Desarmes",
    "asian_total_tackles": "Desarmes",
    "number_of_tackles_in_match": "Desarmes",
}
# mercado de HANDICAP (2 vias casa/fora) → canon próprio. Fica FORA do board de
# propósito: o board e o modelo de valor são over/under de ponta a ponta, e o
# handicap precisa de μ por TIME, que o modelo de cartões não publica (só o
# total do jogo). Este canon só alimenta o banco de odds e o gráfico de
# movimento — que é exatamente o que foi pedido.
HAND_MARKETS = {
    "asian_handicap_cards": "Handicap de Cartões",
}
# mercado → canon, por TIME (header '1'/'2' + handicap 'Over 11.5')
TEAM_MARKETS = {
    "team_cards": "Cartões",
    "team_corners": "Escanteios",
    "team_shots": "Finalizações",
    "team_shots_on_target": "Chutes no gol",
    "team_offsides": "Impedimentos",
    "team_tackles": "Desarmes",
}
# NADA de jogador entra na Mesa (decisão do Diego, 21/07). Denylist explícita por
# prefixo/nome, aplicada ANTES do mapeamento — nunca por acaso.
DENY_PREFIX = ("player_", "goalscorer", "multi_scorer", "either_to_", "team_goalscorer",
               "goal_method", "first_goal_method", "goalkeeper_")
DENY_EXACT = {"goalkeeper_saves", "player_tackles", "player_cards", "player_shots",
              "player_shots_on_target", "player_fouls_committed", "player_to_be_fouled",
              "player_to_score_or_assist", "goalscorers", "multi_scorers"}


def _is_player_market(mk):
    m = str(mk or "").lower()
    return m in DENY_EXACT or any(m.startswith(p) for p in DENY_PREFIX) or "player" in m


# --- DETECTOR de mercado-total-desconhecido (rede de segurança) ----------------
# Objetivo: NUNCA mais perder um mercado de estatística por nomenclatura nova em
# silêncio. Se a bet365 abrir impedimentos/desarmes (ou qualquer stat) com um nome
# que não mapeamos, o detector LOGA o nome oficial num jsonl — daí é 1 min pra
# mapear. Ele NÃO adivinha o canon (não contamina o board); é só observabilidade.
_HALF_LINE = re.compile(r"^\d+\.5$")   # linha O/U meio-inteira (2.5, 5.5, 20.5…)
# RUÍDO CONHECIDO de total O/U que a Mesa NÃO cobre — validado contra catálogo real
# (21/07): são exatamente os mercados que "têm cara de total" mas são gols/faixa/
# meio-tempo/timing/handicap/corrida. NENHUM contém offside/tackle/card/shot/foul —
# então uma variante nova DESSES stats sempre cai FORA daqui e é logada.
# ⚠ `handicap` SAIU desta lista em 29/07, e o motivo é um buraco real: a rede de
# segurança existe pra nunca perder mercado de estatística por nomenclatura nova,
# mas ela filtrava tudo que tivesse "handicap" no nome — então
# `asian_handicap_cards`, o mercado que o Diego pediu, era descartado do PRÓPRIO
# detector e nunca apareceu no jsonl de desconhecidos (o arquivo nem existia).
# Tirar a palavra não gera ruído: `_looks_ou_total` exige headers Over E Under, e
# handicap vem com header '1'/'2' — os de gols continuam não sendo logados.
KNOWN_TOTAL_EXCL = re.compile(
    r"goal|corner|1st_half|2nd_half|half_time|_half\b|_minutes|_brackets|"
    r"race|both_teams|_range$|range_|exact|odd_even|winning_margin|"
    r"correct_score|to_score|time_of", re.I)
_unknown_seen = None   # set 'YYYY-MM-DD|market_key' já logados (dedup: 1x por key por dia)


def _looks_ou_total(odds):
    """True se a lista de odds parece um par O/U de total: tem ao menos um Over E um
    Under (header) e ao menos uma linha meio-inteira (name/handicap ^\\d+\\.5$)."""
    n_over = n_under = 0
    has_half = False
    for o in odds:
        if not isinstance(o, dict):
            continue
        h = str(o.get("header") or "").strip().lower()
        if h == "over":
            n_over += 1
        elif h == "under":
            n_under += 1
        nm = str(o.get("name") or "").strip()
        hc = str(o.get("handicap") or "").strip()
        if _HALF_LINE.match(nm) or _HALF_LINE.match(hc):
            has_half = True
    return n_over >= 1 and n_under >= 1 and has_half


def _detect_unknown_total(mk, mv, gid, jogo):
    """Loga um mercado com cara de O/U-de-total que NÃO é mapeado, NÃO é player e NÃO
    é ruído conhecido. Dedup por (dia, market_key). Barato, não muda o board."""
    global _unknown_seen
    if mk in MATCH_MARKETS or mk in TEAM_MARKETS or mk in HAND_MARKETS:
        return
    if _is_player_market(mk):
        return
    if KNOWN_TOTAL_EXCL.search(str(mk or "")):
        return
    if not isinstance(mv, dict) or not isinstance(mv.get("odds"), list):
        return
    odds = [o for o in mv["odds"] if isinstance(o, dict)]
    if len(odds) < 2 or not _looks_ou_total(odds):
        return
    today = datetime.now(BRT).strftime("%Y-%m-%d")
    key = f"{today}|{mk}"
    if _unknown_seen is None:
        _unknown_seen = set()
        try:
            for ln in UNKNOWN_MK_F.read_text(encoding="utf-8").splitlines():
                try:
                    j = json.loads(ln)
                    _unknown_seen.add(f"{str(j.get('ts', ''))[:10]}|{j.get('market_key')}")
                except Exception:
                    pass
        except Exception:
            pass
    if key in _unknown_seen:
        return
    _unknown_seen.add(key)
    rec = {"ts": datetime.now(BRT).isoformat(timespec="seconds"),
           "gid": gid, "jogo": jogo, "market_key": mk,
           "name_oficial": (mv or {}).get("name"), "n_odds": len(odds),
           "amostra_de_odds": [{k: o.get(k) for k in ("header", "name", "handicap", "odds")}
                               for o in odds[:4]]}
    try:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        with open(UNKNOWN_MK_F, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[bet365] ⚑ mercado-total DESCONHECIDO: {mk} "
              f"(name={rec['name_oficial']}, {len(odds)} odds) — logado pra mapear")
    except Exception as ex:
        print(f"[bet365] aviso: não gravei unknown_markets: {type(ex).__name__}")


def _iter_sp(res):
    """Keep established scopes/order; tolerate malformed optional sections."""
    if not isinstance(res, dict):
        return
    blocks = [res.get(sec) for sec in (
        "main", "corners", "cards_fouls", "asian_lines", "other", "shots",
        "goals", "half", "player_stats")]
    others = res.get("others")
    if isinstance(others, list):
        blocks.extend(others)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        sp = block.get("sp")
        if isinstance(sp, dict):
            for key, market in sp.items():
                if isinstance(market, dict):
                    yield key, market


def parse_prematch(res, home, away, gid=None, jogo=None):
    """v3 prematch -> unchanged normalized schema; complete pairs per block."""
    merc, merc_t_raw, merc_h = {}, {}, {}
    for mk, mv in _iter_sp(res):
        if _is_player_market(mk):
            continue
        odds = mv.get("odds")
        if not isinstance(odds, list) or not odds:
            continue
        canon = MATCH_MARKETS.get(mk)
        if canon:
            block = {}
            _collect(block, odds)
            _merge_complete(merc.setdefault(canon, {}), block)
            continue
        canon_h = HAND_MARKETS.get(mk)
        if canon_h:
            block = {}
            _collect_hand(block, odds)
            _merge_complete(merc_h.setdefault(canon_h, {}), block, ("casa", "fora"))
            continue
        canon_t = TEAM_MARKETS.get(mk)
        if canon_t:
            block = {}
            _collect_team(block, odds, home, away)
            for team, lines in block.items():
                target = merc_t_raw.setdefault(canon_t, {}).setdefault(team, {})
                _merge_complete(target, lines)
            continue
        _detect_unknown_total(mk, mv, gid, jogo)
    out = {}
    for canon, lines in merc.items():
        if lines:
            out[canon] = [dict(linha=line, **pair) for line, pair in sorted(lines.items())]
    for canon, lines in merc_h.items():
        if lines:
            out[canon] = [dict(linha=line, **pair) for line, pair in sorted(lines.items())]
    merc_t = {}
    for canon, teams in merc_t_raw.items():
        for team, lines in teams.items():
            if lines:
                merc_t.setdefault(canon, {})[team] = [
                    dict(linha=line, **pair) for line, pair in sorted(lines.items())]
    return out, merc_t


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path, obj):
    try:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[bet365] aviso: não gravei {Path(path).name}: {type(e).__name__}")


def _sweep_upcoming(token, now_utc, max_pages, start_page=1, *, return_cursor=False):
    """Varre o upcoming (barato: 50/página) → eventos reais na janela de DAYS_AHEAD."""
    events, total, page = [], None, start_page
    next_page = start_page
    while page < start_page + max_pages:
        d = get("/v1/bet365/upcoming", {"sport_id": 1, "page": page}, token)
        if not d:
            break
        total = (d.get("pager") or {}).get("total") or 0
        next_page = page + 1 if page * 50 < total else 11
        for r in d.get("results") or []:
            league = ((r.get("league") or {}).get("name")) or ""
            if EXCL_LEAGUE.search(league):
                continue
            try:
                t = int(r.get("time") or 0)
            except Exception:
                continue
            # Keep observed identities even when no longer eligible: the merge
            # must evict an old future kickoff corrected to past/cancelled time.
            home = ((r.get("home") or {}).get("name")) or ""
            away = ((r.get("away") or {}).get("name")) or ""
            if not home or not away:
                continue
            events.append({"fi": r.get("id"), "time": t, "league": league,
                           "league_id": (r.get("league") or {}).get("id"),
                           "home": home, "away": away})
        if page * 50 >= (total or 0):
            break
        page += 1
        time.sleep(SLEEP)
    print(f"[bet365] upcoming: {total} eventos brutos · {len(events)} reais ({start_page}–{page})")
    return (events, next_page) if return_cursor else events


def fetch_inventory(token, now_utc, cursor):
    """Two independent sweeps; only the deeper sweep owns the rotation cursor."""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="bet365-inventory") as pool:
        front_future = pool.submit(_sweep_upcoming, token, now_utc, 10, return_cursor=True)
        deep_future = pool.submit(_sweep_upcoming, token, now_utc, 10,
                                  start_page=cursor, return_cursor=True)
        front, _ = front_future.result()
        deeper, next_page = deep_future.result()
    return front + deeper, next_page


class ObservedRows(dict):
    """Rows plus source receipt clocks, independent of ordered parser execution."""
    def __init__(self):
        super().__init__()
        self.observed_at = {}


def _record_received(found, data, allowed_ids):
    values = (data or {}).get("results") or []
    observed_at = datetime.now(BRT).strftime("%Y-%m-%d %H:%M:%S")
    if not isinstance(values, list):
        return
    for row in values:
        if isinstance(row, dict) and str(row.get("FI")) in allowed_ids:
            ident = str(row["FI"])
            found[ident] = row
            found.observed_at[ident] = observed_at


def fetch_batch(lote, token):
    """Retry each missing FI, including partial (not only empty) responses."""
    ids = {str(e['fi']) for e in lote}
    data = get('/v3/bet365/prematch', {'FI': ','.join(str(e['fi']) for e in lote)}, token)
    found = ObservedRows()
    _record_received(found, data, ids)
    transport_missing = set(ids) if data is None else set()
    for event in lote:
        ident = str(event['fi'])
        if ident not in found and len(lote) > 1:
            try:
                data = get('/v3/bet365/prematch', {'FI': ident}, token)
            except CaptureBudgetExceeded:
                # Preserve valid rows already returned by the batch.
                return found, True, sorted(transport_missing | (ids - set(found)))
            if data is None:
                transport_missing.add(ident)
            else:
                transport_missing.discard(ident)
            _record_received(found, data, {ident})
            time.sleep(SLEEP)
    return found, False, sorted(transport_missing - set(found))


def iter_batches(events, token):
    """Refill on completion, emit in selection order; at most two in-flight.

    Finished rows wait in a bounded buffer (the caller selects at most 120 FIs),
    retaining their HTTP receipt clocks. One slow batch cannot idle the other
    lane. Budget errors stop refills but never discard the other in-flight
    worker or results already buffered. Only main parses and writes records.
    """
    chunks = iter(events[i:i + FI_BATCH] for i in range(0, len(events), FI_BATCH))
    pending, completed = {}, {}
    next_submit = next_yield = 0
    stopped = False
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="bet365-prematch") as pool:
        def submit_next():
            nonlocal next_submit
            for chunk in chunks:
                lote = [e for e in chunk if float(e["time"]) > time.time()]
                if not lote:
                    continue
                submitted_at = datetime.now(BRT).strftime("%Y-%m-%d %H:%M:%S")
                future = pool.submit(fetch_batch, lote, token)
                pending[future] = (next_submit, lote, submitted_at)
                next_submit += 1
                return True
            return False

        for _ in range(2):
            if not submit_next():
                break
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            done.update(future for future in pending if future.done())
            # Resolve every observed completion before refill: a simultaneous
            # budget failure must not be hidden by a successful lower index.
            for future in sorted(done, key=lambda item: pending[item][0]):
                index, lote, submitted_at = pending.pop(future)
                try:
                    found, exhausted, missing = future.result()
                except CaptureBudgetExceeded as error:
                    found, exhausted, missing = ObservedRows(), True, sorted(str(e["fi"]) for e in lote)
                    if getattr(error, "request_started", None) is False:
                        # No HTTP: preserve unattempted metrics and the full
                        # selected-ID tombstones used by retention in main.
                        lote = []
                if not isinstance(found, ObservedRows):
                    observed = ObservedRows()
                    observed.update(found)
                    observed.observed_at.update({ident: submitted_at for ident in found})
                    found = observed
                stopped = stopped or exhausted
                completed[index] = (lote, found, exhausted, missing)
            # Replenish transport before yielding: parsing can be slower than
            # an HTTP return and must not hold an otherwise available lane.
            while not stopped and len(pending) < 2:
                if not submit_next():
                    break
            while next_yield in completed:
                yield completed.pop(next_yield)
                next_yield += 1


def main():
    global MIN_EFF, DEADLINE, FULL_SKIPPED, CAPTURE_INCOMPLETE, N_REQ
    DEADLINE, N_REQ = time.monotonic() + 420, 0
    FULL_SKIPPED = CAPTURE_INCOMPLETE = False
    now = datetime.now(BRT)
    now_utc = datetime.now(timezone.utc)
    _wh = odds_window()
    inventory_started = time.monotonic()

    if _wh is None:
        # ===== FULL: só a cada FULL_EVERY_H (token compartilhado — ver docstring) =====
        from capture_common import resolve_odds_pointer
        meta, _srcp = resolve_odds_pointer("bet365", prefer_full=True, max_age_h=FULL_EVERY_H)
        if meta and meta.get('_pointer') == 'bet365_latest_full.json':
            age_h = meta.get('_age_h')
            n_prev = int((meta or {}).get("_actual_n") or 0)
            try:
                current_contract = all(json.loads(line).get('parser_contract') == 2
                    for line in _srcp.read_text(encoding='utf-8').splitlines() if line.strip())
            except (OSError, ValueError, AttributeError):
                current_contract = False
            if n_prev > 0 and current_contract:
                # pulo SEM chamada nenhuma; pointer atual segue valendo (stale-keep honesto)
                MIN_EFF = 1
                FULL_SKIPPED = True
                print(f"[bet365] gate: último full há {age_h:.1f}h (<{FULL_EVERY_H:g}h) — "
                      f"pulando captura (0 req; inventário atual: {n_prev} jogos)")
                return n_prev
            print(f"[bet365] gate: fonte vazia/contrato antigo — full de recuperação")
        token = _token()
        cache = _load_json(FIS_F) or {}
        # Refresh the near horizon and rotate deeper pages, instead of repeatedly
        # visiting only the same first 1000 events. Keep older future identities.
        cursor = max(11, int(cache.get('next_page') or 11))
        swept, next_page = fetch_inventory(token, now_utc, cursor)
        events = merge_inventory(cache.get('events') or [], swept, now_utc.timestamp())
        _save_json(FIS_F, {"at": now.isoformat(timespec="seconds"),
                           "at_epoch": time.time(), "events": events,
                           "next_page": next_page, "partial_inventory": True})
    else:
        # ===== CLOSE: sempre roda, mas SÓ iminentes, via cache de FIs do último full =====
        token = _token()
        cache = _load_json(FIS_F) or {}
        cache_age_h = (time.time() - float(cache.get("at_epoch") or 0)) / 3600.0 \
            if cache.get("at_epoch") else 1e9
        events = eligible_events(cache.get('events') or [], now_utc.timestamp(), hours=_wh, min_lead=180)
        if cache_age_h > FIS_MAX_AGE_H or (not events and not cache.get("events")):
            print(f"[bet365] close: cache de FIs {'velho' if cache else 'ausente'} "
                  f"({cache_age_h:.1f}h) — fallback upcoming (2 páginas)")
            swept = _sweep_upcoming(token, now_utc, 2)
            events = eligible_events(swept, now_utc.timestamp(), hours=_wh, min_lead=180)
        else:
            print(f"[bet365] close: cache de FIs ({cache_age_h:.1f}h) → "
                  f"{len(events)} jogos iminentes na janela {_wh:g}h")
        MIN_EFF = (min(MIN_EVENTS, 1) if events else 0)

    queue = DiscoveryQueue(STATUS_DIR / 'bet365_discovery.json', now)
    events = eligible_events(events, now_utc.timestamp(), hours=_wh, min_lead=180 if _wh else 0)
    inventory = list(events)
    events = queue.select(events, MAX_CLOSE_EVENTS if _wh is not None else MAX_EVENTS,
                          id_field='fi', priority_key=league_priority)
    queue.metrics.update(mode='close' if _wh is not None else 'full', returned=0,
                         parsed=0, missing_fis=[], no_supported_markets=[], started_before_fetch=[],
                         inventory_elapsed_s=round(time.monotonic()-inventory_started, 2))

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"bet365_{stamp}.jsonl"
    from capture_common import write_odds_latest
    previous = []
    if _wh is None:
        old_meta, old_path = resolve_odds_pointer("bet365", prefer_full=True, max_age_h=12)
        if old_path and old_meta.get('_pointer') == 'bet365_latest_full.json':
            try:
                previous = [json.loads(line) for line in old_path.read_text(encoding='utf-8').splitlines() if line.strip()]
            except (OSError, ValueError):
                previous = []
    def write_latest(n, promote=None):
        return write_odds_latest("bet365", out_path.name, n,
                          at=now.isoformat(timespec="seconds"), promote_full=promote,
                          min_events=MIN_EFF)

    # 2) prematch em LOTES de FI (o endpoint aceita FI=a,b,c — 21/07: 10 jogos por
    #    request, validado. Derruba o full de ~75 req pra ~20 e o close pra 1-2.)
    f = open(out_path, "w", encoding="utf-8")
    n_out = n_det = 0
    prematch_started = time.monotonic()
    for lote, by_fi, budget_exhausted, transport_missing in iter_batches(events, token):
        if budget_exhausted or transport_missing:
            CAPTURE_INCOMPLETE = True
        time.sleep(SLEEP)
        for e in lote:
            r = by_fi.get(str(e["fi"]))
            if not r:
                queue.record(e['fi'], success=False)
                queue.metrics['missing_fis'].append(str(e['fi']))
                continue
            queue.metrics['returned'] += 1
            n_det += 1
            if float(e['time']) <= time.time():
                queue.metrics['started_before_fetch'].append(str(e['fi']))
                queue.record(e['fi'], success=True, useful=False)
                continue
            merc, merc_t = parse_prematch(r, e["home"], e["away"],
                                          gid=e["fi"], jogo=f"{e['home']} - {e['away']}")
            if not merc and not merc_t:
                queue.record(e['fi'], success=True, useful=False)
                queue.metrics['no_supported_markets'].append(str(e['fi']))
                continue
            useful = (set(merc) | set(merc_t)) - {'Escanteios', 'Handicap de Cartões'}
            queue.record(e['fi'], success=True, useful=bool(useful), markets=useful)
            queue.metrics['parsed'] += 1
            rec = {"casa": "bet365", "event_id": e["fi"],
                   "parser_contract": 2,
                   "name": f"{e['home']} - {e['away']}",
                   "league": e["league"], "start": e["time"],
                   "captured_at": by_fi.observed_at[str(e["fi"])],
                   "mercados": merc}
            if merc_t:
                rec["mercados_time"] = merc_t
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            n_out += 1
    fresh_count = n_out
    carried = retained_quotes(previous, inventory, {str(e['fi']) for e in events}, time.time()) if _wh is None else []
    for rec in carried:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n_out += len(carried)
    f.close()
    if _wh is None and fresh_count < MIN_EFF:
        CAPTURE_INCOMPLETE = True
    queue.metrics.update(requests=N_REQ, incomplete=CAPTURE_INCOMPLETE,
                         prematch_elapsed_s=round(time.monotonic()-prematch_started, 2),
                         fresh_events=fresh_count, retained_events=len(carried),
                         unattempted=max(0,len(events)-queue.metrics.get('attempted',0)))
    queue.save()
    result = write_latest(n_out, promote=False if CAPTURE_INCOMPLETE else None)
    if _wh is None and n_out >= MIN_EFF and not CAPTURE_INCOMPLETE and not result.get('promotion_blocked'):
        # Full concluído: recibo da rodada; o gate consulta o ponteiro validado.
        _save_json(GATE_F, {"last_full_epoch": time.time(),
                            "last_full_at": now.isoformat(timespec="seconds"),
                            "last_full_req": N_REQ, "last_full_n": n_out})
    print(f"[bet365] {n_det} prematch consultados · {n_out} jogos com mercado de estatística salvos em {out_path.name}")
    print(f"[bet365] req nesta captura: {N_REQ} (modo {'close' if _wh is not None else 'full'})")
    return n_out


if __name__ == "__main__":
    import time as _t; _t0 = _t.time()
    from capture_common import finish
    try:
        _n = main() or 0
        sys.exit(finish("bet365", _n, MIN_EFF, t0=_t0, reused=FULL_SKIPPED,
                        error="captura parcial: rede/orçamento; full anterior preservado" if CAPTURE_INCOMPLETE else None))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("bet365", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
