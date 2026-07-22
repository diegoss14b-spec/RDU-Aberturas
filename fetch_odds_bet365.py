# -*- coding: utf-8 -*-
"""fetch_odds_bet365.py — captura odds da BET365 via BetsAPI (api.b365api.com), pra
a Mesa de Aberturas. Plano do Diego: 30 req/s, 3.600 req/h — cada captura usa
~15 páginas de upcoming + ≤MAX_EVENTS prematch (~75 req, folga enorme).

  lista : GET /v1/bet365/upcoming?sport_id=1&token=&page=   (50/página, ~700 eventos,
          horizonte de meses; POLUÍDO de Esoccer/SRL — filtrar por nome de liga)
  detalhe: GET /v3/bet365/prematch?token=&FI=a,b,c          (aceita ATÉ ~10 FIs por
          chamada — validado 21/07; sp.{mercado}.odds[] = {header:'Over'|'Under'|'1'|'2',
          name:'5.5'|'Over 5.5', odds:'2.000', handicap?})
  ⚠ A resposta tem seções-DICIONÁRIO (main/corners/cards_fouls/other/asian_lines/shots)
  E a lista `others` (~79 blocos, cada um com seu `sp`). MUITO mercado de partida só
  existe na LISTA (match_shots_on_target, alternative_corners com 63 odds, asiáticos
  de escanteios/cartões, team_shots…). O parser varre as duas fontes.

Mercados capturados (só O/U de linha; faixas/race/exatos/3-vias ficam FORA):
  Cartões    : number_of_cards_in_match + asian_total_cards (+ team_cards por time)
  Escanteios : corners_2_way + alternative_corners + asian_corners + asian_total_corners
               (+ team_corners por time)  — corners.corners é 3-VIAS (Over/Exactly/Under), NÃO entra
  Finalizações / Chutes no gol: match_shots / match_shots_on_target (+ team_*)
  Impedimentos / Desarmes: a bet365 abre só em "especiais/outros" e raramente — QUANDO
      abrem vêm na lista `others`. Mapeamos as variantes plausíveis do padrão BetsAPI
      (match_/total_/asian_total_/.._2_way/number_of_.._in_match + team_*); se abrirem
      com nome fora da lista, o DETECTOR (abaixo) loga o nome oficial pra mapear.
  Faltas de JOGO / laterais / tiros de meta: a bet365 só abre no "Criar Aposta" (bet
      builder), NÃO na API — não captáveis, casa entra sem eles.
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

POLÍTICA DE CONSUMO "abertura + fechamento" (21/07 — o token é COMPARTILHADO):
  - FULL: só a cada ~3h (gate por timestamp em _status/bet365_gate.json; fulls
    intermediários pulam SEM chamada nenhuma, reaproveitando o pointer atual —
    vira stale-keep honesto no board). Pega a abertura + pontos intermediários.
  - CLOSE: SEMPRE roda, mas SÓ os jogos iminentes do CACHE de FIs gravado no
    último full (_status/bet365_fis.json) — tipicamente 2-8 req; upcoming só
    como fallback (2 páginas) se o cache não servir.
  Conta (com lotes de 10 FIs): ~8 fulls/dia × ~20 req + ~72 closes × ~2 req ≈
  300-350 req/dia (era ~2.250), com limite de 3.600/h — folga enorme pro outro usuário.

TOTAL DE CHUTES DO JOGO (Finalizações): a BetsAPI NÃO entrega. `other.sp.match_shots`
existe no catálogo mas vem sempre com odds:[] — 0 ocorrências em 28 jogos testados
(21/07), incluindo o jogo mais rico (Atlético-MG×Bahia, 79 blocos) e jogos a 9 min do
apito. A bet365 mostra o mercado na tela, o provedor não expõe. O que dá pra ter:
match_shots_on_target (total do jogo, raro) e team_shots/team_shots_on_target (por time)."""
import sys, os, json, re, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
if sys.stdout is None or not hasattr(sys.stdout, "write"): sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"): sys.stderr = open(os.devnull, "w")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import requests

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
MAX_EVENTS = 60          # cap de chamadas de prematch por FULL (~75 req no total)
MAX_CLOSE_EVENTS = 12    # cap do close (iminentes; tipicamente 2-8)
MAX_PAGES = 20           # upcoming pagina de 50 em 50 (~700 eventos = 14 páginas)
FULL_EVERY_H = 2.5       # gate: full de verdade só a cada ~3h (2h30 de guarda, cron atrasa)
FIS_MAX_AGE_H = 12.0     # cache de FIs mais velho que isso não vale (fallback upcoming)
FI_BATCH = 10            # o /v3/prematch aceita FI=a,b,c — 10 jogos por request (21/07)
SLEEP = 0.12             # ≤10 req/s — bem abaixo do limite de 30 req/s do plano
MIN_EVENTS = 5
MIN_EFF = MIN_EVENTS     # modo close (ODDS_WINDOW_H) e skip do gate reduzem — ver main()
N_REQ = 0                # contador de requests da captura (auditoria de consumo)

# ligas falsas (bots/simulação) — NUNCA entram
EXCL_LEAGUE = re.compile(r"esoccer|e-?soccer|srl\b|\(srl\)|virtual|simulat", re.I)
# prioridade 0 = ligas com MODELO na Mesa (cartões/faltas/finalizações/escanteios)
PRIO_LEAGUE = re.compile(
    r"brazil serie [ab]|premier league|la liga|italy serie a|bundesliga|ligue 1|"
    r"eliteserien|bolivia|ecuador|china super league", re.I)
# prioridade 1 = competições que a Mesa acompanha de perto
SEC_LEAGUE = re.compile(
    r"brazil|libertadores|sudamericana|sul-americana|argentina|mexico|colombia|"
    r"uefa|champions|europa|conference|championship|eredivisie|primeira liga|mls", re.I)


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


def get(path, params, token):
    """GET com retry; loga SEM o token (o token nunca pode vazar em log público)."""
    global N_REQ
    url = f"{BASE}{path}"
    q = dict(params); q["token"] = token
    for a in range(3):
        try:
            N_REQ += 1
            r = requests.get(url, params=q, timeout=30)
            if r.status_code == 200:
                d = r.json()
                if d.get("success") == 1:
                    return d
                # success:0 = token/quota — não insistir além do retry
                print(f"[bet365] {path} success=0 (tentativa {a+1}): {str(d)[:120]}")
            elif r.status_code == 429:
                time.sleep(2.0 * (a + 1)); continue
            else:
                print(f"[bet365] {path} HTTP {r.status_code} (tentativa {a+1})")
        except Exception as e:
            print(f"[bet365] {path} erro: {type(e).__name__} (tentativa {a+1})")
        time.sleep(1.0)
    return None


# --- parse das odds -----------------------------------------------------------
_OU_NAME = re.compile(r"^(over|under)\s+([0-9.]+)$", re.I)


def _num(s):
    try:
        v = float(str(s).strip())
        return v
    except Exception:
        return None


def _entry_side_line(o):
    """Uma odd da BetsAPI → (side, linha) ou None.
    Formatos: header Over/Under + name/handicap numérico  |  name/handicap 'Over 5.5'."""
    header = str(o.get("header") or "").strip().lower()
    name = str(o.get("name") or "").strip()
    hcap = str(o.get("handicap") or "").strip()
    if "," in name or "," in hcap:
        return None  # linha asiática quartada (5.5,6.0) — não representável no par O/U
    if header in ("over", "under"):
        L = _num(name) if _num(name) is not None else _num(hcap)
        if L is None:
            return None
        return header, L
    for txt in (name, hcap):
        mo = _OU_NAME.match(txt)
        if mo:
            return mo.group(1).lower(), float(mo.group(2))
    return None


def _collect(lines, odds_list):
    """Acumula pares O/U em lines[L] = {'over','under'} (primeiro valor vence)."""
    for o in odds_list or []:
        sl = _entry_side_line(o)
        price = _num(o.get("odds"))
        if not sl or not price or price <= 1:
            continue
        side, L = sl
        slot = lines.setdefault(L, {})
        slot.setdefault(side, round(price, 2))


def _collect_team(per_team, odds_list, home, away):
    """Mercados por time: header '1'(casa)/'2'(fora) + handicap 'Over 5.5'."""
    for o in odds_list or []:
        header = str(o.get("header") or "").strip()
        team = home if header == "1" else (away if header == "2" else None)
        if not team:
            continue
        sl = None
        for txt in (str(o.get("handicap") or ""), str(o.get("name") or "")):
            mo = _OU_NAME.match(txt.strip())
            if mo:
                sl = (mo.group(1).lower(), float(mo.group(2)))
                break
        price = _num(o.get("odds"))
        if not sl or not price or price <= 1:
            continue
        side, L = sl
        slot = per_team.setdefault(team, {}).setdefault(L, {})
        slot.setdefault(side, round(price, 2))


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
    "alternative_corners": "Escanteios",
    "asian_corners": "Escanteios",
    "asian_total_corners": "Escanteios",
    "match_shots": "Finalizações",              # existe no catálogo mas a API nunca popula (ver docstring)
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
KNOWN_TOTAL_EXCL = re.compile(
    r"goal|corner|1st_half|2nd_half|half_time|_half\b|_minutes|_brackets|"
    r"race|handicap|both_teams|_range$|range_|exact|odd_even|winning_margin|"
    r"correct_score|to_score|time_of", re.I)
_unknown_seen = None   # set 'YYYY-MM-DD|market_key' já logados (dedup: 1x por key por dia)


def _looks_ou_total(odds):
    """True se a lista de odds parece um par O/U de total: tem ao menos um Over E um
    Under (header) e ao menos uma linha meio-inteira (name/handicap ^\\d+\\.5$)."""
    n_over = n_under = 0
    has_half = False
    for o in odds:
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
    if mk in MATCH_MARKETS or mk in TEAM_MARKETS:
        return
    if _is_player_market(mk):
        return
    if KNOWN_TOTAL_EXCL.search(str(mk or "")):
        return
    odds = (mv or {}).get("odds") or []
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
    """Gera (mercado, mv) de TODAS as fontes: seções-dicionário + lista `others`."""
    for sec in ("main", "corners", "cards_fouls", "asian_lines", "other", "shots",
                "goals", "half", "player_stats"):
        sp = (res.get(sec) or {}).get("sp") or {}
        if isinstance(sp, dict):
            for mk, mv in sp.items():
                yield mk, mv
    for blk in (res.get("others") or []):
        sp = (blk or {}).get("sp") or {}
        if isinstance(sp, dict):
            for mk, mv in sp.items():
                yield mk, mv


def parse_prematch(res, home, away, gid=None, jogo=None):
    """results[0] do /v3/bet365/prematch → (mercados, mercados_time).
    Varre seções-dicionário E a lista `others`; jogador nunca entra. Mercado não
    mapeado com cara de O/U-de-total vai pro detector (rede de segurança)."""
    merc, merc_t_raw = {}, {}
    for mk, mv in _iter_sp(res):
        if _is_player_market(mk):
            continue
        odds = (mv or {}).get("odds") or []
        if not odds:
            continue
        canon = MATCH_MARKETS.get(mk)
        if canon:
            _collect(merc.setdefault(canon, {}), odds)
            continue
        canon_t = TEAM_MARKETS.get(mk)
        if canon_t:
            _collect_team(merc_t_raw.setdefault(canon_t, {}), odds, home, away)
            continue
        # não mapeado e não é player → rede de segurança (só loga, não entra no board)
        _detect_unknown_total(mk, mv, gid, jogo)
    out = {}
    for canon, lines in merc.items():
        arr = [{"linha": L, "over": v["over"], "under": v["under"]}
               for L, v in sorted(lines.items()) if "over" in v and "under" in v]
        if arr:
            out[canon] = arr
    merc_t = {}
    for canon, per_team in merc_t_raw.items():
        for team, lines in per_team.items():
            arr = [{"linha": L, "over": v["over"], "under": v["under"]}
                   for L, v in sorted(lines.items()) if "over" in v and "under" in v]
            if arr:
                merc_t.setdefault(canon, {})[team] = arr
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


def _sweep_upcoming(token, now_utc, max_pages):
    """Varre o upcoming (barato: 50/página) → eventos reais na janela de DAYS_AHEAD."""
    events, total, page = [], None, 1
    while page <= max_pages:
        d = get("/v1/bet365/upcoming", {"sport_id": 1, "page": page}, token)
        if not d:
            break
        total = (d.get("pager") or {}).get("total") or 0
        for r in d.get("results") or []:
            league = ((r.get("league") or {}).get("name")) or ""
            if EXCL_LEAGUE.search(league):
                continue
            try:
                t = int(r.get("time") or 0)
            except Exception:
                continue
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            if not (now_utc - timedelta(hours=3) <= dt <= now_utc + timedelta(days=DAYS_AHEAD)):
                continue
            home = ((r.get("home") or {}).get("name")) or ""
            away = ((r.get("away") or {}).get("name")) or ""
            if not home or not away:
                continue
            events.append({"fi": r.get("id"), "time": t, "league": league,
                           "home": home, "away": away})
        if page * 50 >= (total or 0):
            break
        page += 1
        time.sleep(SLEEP)
    print(f"[bet365] upcoming: {total} eventos brutos · {len(events)} reais na janela de {DAYS_AHEAD}d ({page} páginas)")
    return events


def main():
    global MIN_EFF
    token = _token()
    now = datetime.now(BRT)
    now_utc = datetime.now(timezone.utc)
    _wh = odds_window()

    if _wh is None:
        # ===== FULL: só a cada FULL_EVERY_H (token compartilhado — ver docstring) =====
        gate = _load_json(GATE_F) or {}
        last = float(gate.get("last_full_epoch") or 0)
        age_h = (time.time() - last) / 3600.0 if last else 1e9
        from capture_common import resolve_odds_pointer
        if age_h < FULL_EVERY_H:
            meta, _srcp = resolve_odds_pointer("bet365", prefer_full=False)
            n_prev = int((meta or {}).get("_actual_n") or 0)
            if n_prev > 0:
                # pulo SEM chamada nenhuma; pointer atual segue valendo (stale-keep honesto)
                MIN_EFF = 1
                print(f"[bet365] gate: último full há {age_h:.1f}h (<{FULL_EVERY_H:g}h) — "
                      f"pulando captura (0 req; inventário atual: {n_prev} jogos)")
                return n_prev
            print(f"[bet365] gate: dentro da janela mas SEM pointer válido — full de recuperação")
        events = _sweep_upcoming(token, now_utc, MAX_PAGES)
        # cache de FIs pro modo close (todos da janela, ANTES do cap)
        _save_json(FIS_F, {"at": now.isoformat(timespec="seconds"),
                           "at_epoch": time.time(), "events": events})
        # prioridade: ligas com modelo > competições acompanhadas > resto; depois kickoff
        def prio(e):
            lg = e["league"]
            p = 0 if PRIO_LEAGUE.search(lg) else (1 if SEC_LEAGUE.search(lg) else 2)
            return (p, e["time"])
        events.sort(key=prio)
        events = events[:MAX_EVENTS]
    else:
        # ===== CLOSE: sempre roda, mas SÓ iminentes, via cache de FIs do último full =====
        cache = _load_json(FIS_F) or {}
        cache_age_h = (time.time() - float(cache.get("at_epoch") or 0)) / 3600.0 \
            if cache.get("at_epoch") else 1e9
        events = [e for e in (cache.get("events") or []) if in_window(e.get("time"), _wh)]
        if cache_age_h > FIS_MAX_AGE_H or (not events and not cache.get("events")):
            print(f"[bet365] close: cache de FIs {'velho' if cache else 'ausente'} "
                  f"({cache_age_h:.1f}h) — fallback upcoming (2 páginas)")
            swept = _sweep_upcoming(token, now_utc, 2)
            events = [e for e in swept if in_window(e.get("time"), _wh)]
        else:
            print(f"[bet365] close: cache de FIs ({cache_age_h:.1f}h) → "
                  f"{len(events)} jogos iminentes na janela {_wh:g}h")
        events.sort(key=lambda e: e.get("time") or 0)
        events = events[:MAX_CLOSE_EVENTS]
        MIN_EFF = (min(MIN_EVENTS, 1) if events else 0)

    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = OUTDIR / f"bet365_{stamp}.jsonl"
    from capture_common import write_odds_latest
    def write_latest(n, promote=None):
        write_odds_latest("bet365", out_path.name, n,
                          at=now.isoformat(timespec="seconds"), promote_full=promote,
                          min_events=MIN_EFF)

    # 2) prematch em LOTES de FI (o endpoint aceita FI=a,b,c — 21/07: 10 jogos por
    #    request, validado. Derruba o full de ~75 req pra ~20 e o close pra 1-2.)
    f = open(out_path, "w", encoding="utf-8")
    n_out = n_det = 0
    for i in range(0, len(events), FI_BATCH):
        lote = events[i:i + FI_BATCH]
        d = get("/v3/bet365/prematch", {"FI": ",".join(str(e["fi"]) for e in lote)}, token)
        time.sleep(SLEEP)
        rs = (d or {}).get("results") or []
        if not rs and len(lote) > 1:
            # lote falhou: tenta um a um (não perde a rodada inteira por 1 FI ruim)
            rs = []
            for e in lote:
                d1 = get("/v3/bet365/prematch", {"FI": e["fi"]}, token)
                time.sleep(SLEEP)
                rs += (d1 or {}).get("results") or []
        by_fi = {str(r.get("FI") or r.get("event_id")): r for r in rs}
        for e in lote:
            r = by_fi.get(str(e["fi"]))
            if not r:
                continue
            n_det += 1
            merc, merc_t = parse_prematch(r, e["home"], e["away"],
                                          gid=e["fi"], jogo=f"{e['home']} - {e['away']}")
            if not merc and not merc_t:
                continue
            rec = {"casa": "bet365", "event_id": e["fi"],
                   "name": f"{e['home']} - {e['away']}",
                   "league": e["league"], "start": e["time"],
                   "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                   "mercados": merc}
            if merc_t:
                rec["mercados_time"] = merc_t
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            n_out += 1
    f.close()
    write_latest(n_out, promote=None)
    if _wh is None and n_out > 0:
        # full concluído: arma o gate das próximas ~3h (auditoria: req da rodada junto)
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
        sys.exit(finish("bet365", _n, MIN_EFF, t0=_t0))
    except SystemExit:
        raise
    except BaseException as _e:
        finish("bet365", 0, MIN_EFF, error=_e, t0=_t0)
        sys.exit(1)
