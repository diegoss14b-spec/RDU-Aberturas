# -*- coding: utf-8 -*-
"""history_ingest.py — BANCO DE ODDS. A cada captura, lê {casa}_latest e registra:
  - TICK (append) quando odd mudou ≥0.01, 1ª obs, ou main line mudou
  - KEY upsert open/last/min/max + sofa_id + capture_quality

Chave canônica:
  com Sofa:  casa|sofa:{id}|mercado|linha|lado
  sem Sofa:  casa|data|home_norm|away_norm|mercado|linha|lado

P1: não atualiza last_odd depois do kickoff; quality full_prematch|late_open|…
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import resolve_fixture, history_key, load_sofa_fixtures, parse_start, norm_team, gscore
from history_quality import (
    compute_capture_quality, is_pre_kickoff, pick_main_line, parse_ts, ensure_aware, BRT,
)
from history_merge import atomic_write_text
from history_shard import load_month, save_month
from migrate_history_keys import migrate_keys_dict, migrate_tick_file, unify_keys_dict
# uma régua só pro lado do time: a MESMA que o board usa pra montar times[mercado][home|away].
# Duas implementações do "de quem é esta linha" divergiriam e o banco discordaria da tela.
from build_board import _assign_side, _betano_team

ODDS = ROOT / "data" / "odds"
HIST = ROOT / "data" / "odds_history"
HOUSE_MAP = HIST / "house_event_map.json"
# ⚠️ 03/08 — "sportingbet" entrou aqui. Ela é capturada todo ciclo e o `build_board`
# a lê (load_normalized("Sportingbet", ...)), mas esta lista nunca foi atualizada:
# a casa aparecia na Mesa e 100% das linhas dela eram descartadas antes do banco —
# sem histórico, sem abertura/fechamento, sem CLV. Nenhum commit jamais tirou o nome
# daqui, então não foi decisão, foi esquecimento na hora de plugar a casa.
CASAS = ["betano", "superbet", "estrelabet", "7k", "pinnacle", "bet365", "betfast",
         "sportingbet"]

# métodos fortes o bastante pra FIXAR a identidade no mapa por casa (event_id).
# one_side/slot_unique re-resolvem a cada rodada — pin errado não pode ficar grudado.
PIN_METHODS = {"pair"}
HOUSE_MAP_TTL_DAYS = 30

BETANO_MK = {
    "Total de Cartões": "Cartões", "Total de Faltas": "Faltas", "Total de chutes": "Finalizações",
    "Total de Impedimentos": "Impedimentos", "Total de laterais": "Laterais",
    "Total de tiros de meta": "Tiros de meta", "Escanteios": "Escanteios",
    "Chutes no gol": "Chutes no gol",
}


def load_events(casa):
    ptr = ODDS / f"{casa}_latest.json"
    if not ptr.exists():
        return []
    try:
        fn = json.loads(ptr.read_text(encoding="utf-8")).get("file")
        src = ODDS / fn if fn else None
        if not src or not src.exists():
            return []
        evs = []
        for ln in src.read_text(encoding="utf-8").strip().split("\n"):
            if not ln.strip():
                continue
            e = json.loads(ln)
            merc_t = {}
            if casa == "betano":
                mk, mk_t = {}, {}
                for aba in ("cartoes", "estatisticas", "principais_ou", "escanteios"):
                    for m in (e.get("markets", {}).get(aba) or []):
                        if not (m.get("over") and m.get("under") and m.get("line") is not None):
                            continue
                        row = {"linha": m["line"], "over": m["over"], "under": m["under"]}
                        canon = BETANO_MK.get(m.get("market"))
                        if canon:
                            mk.setdefault(canon, {})[m["line"]] = row
                            continue
                        # a Betano não tem campo `mercados_time`: o time vem DENTRO do
                        # nome ('Athletico-PR Total de Cartões'). Mesmo parser do board.
                        par = _betano_team(m.get("market") or "")
                        if par and par[0]:
                            c, team = par
                            mk_t.setdefault(c, {}).setdefault(team, {})[m["line"]] = row
                merc = {c: list(v.values()) for c, v in mk.items()}
                merc_t = {c: {t: list(l.values()) for t, l in times.items() if l}
                          for c, times in mk_t.items()}
            else:
                merc = e.get("mercados") or {}
                merc_t = e.get("mercados_time") or {}
            if not merc and not merc_t:
                continue
            name = e.get("name") or ""
            parts = [p.strip() for p in name.replace(" vs. ", " - ").replace(" vs ", " - ").split(" - ")]
            home_raw = parts[0] if parts else name
            away_raw = parts[1] if len(parts) > 1 else ""
            evs.append({
                "name": name, "start": e.get("start"), "league": e.get("league") or "",
                "mercados": merc, "mercados_time": merc_t,
                "home_raw": home_raw, "away_raw": away_raw,
                "event_id": e.get("event_id"),
            })
        return evs
    except Exception as ex:
        print(f"[ingest] {casa}: erro ({type(ex).__name__}: {ex})")
        return []


def _gid(idt, djogo, h, a):
    if idt.get("sofa_id"):
        return f"sofa:{idt['sofa_id']}"
    return f"{djogo}|{h}|{a}"


# ---------------------------------------------------------------------------
# Mapa persistente de event_id POR CASA (brief 22/07 §6 req.1 e §7): uma vez que
# um evento da casa foi associado com método forte, a identidade fica estável
# entre rodadas (não fragmenta na virada de meia-noite nem "migra" por fuzzy).
# ---------------------------------------------------------------------------
def load_house_map():
    try:
        d = json.loads(HOUSE_MAP.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def prune_house_map(hmap, now):
    cut = (now - timedelta(days=HOUSE_MAP_TTL_DAYS)).strftime("%Y-%m-%dT%H:%M:%S%z")
    return {k: v for k, v in hmap.items()
            if isinstance(v, dict) and str(v.get("last_seen") or "") >= cut}


def save_house_map(hmap):
    atomic_write_text(HOUSE_MAP, json.dumps(hmap, ensure_ascii=False))


def resolve_identity(casa, ev, fixtures, hmap, now_iso):
    """Identidade do evento: mapa por casa (pin) > resolve_fixture.

    - pin sofa: reutiliza a identidade fixada, se os nomes crus ainda baterem;
    - pin legado: mantém o gid estável (dia/nomes), mas PROMOVE pra sofa se o
      resolve atual achar match forte (fixture pode ter aparecido depois);
    - sem pin: resolve e, se o método for forte (PIN_METHODS), fixa no mapa.
    """
    ev_id = ev.get("event_id")
    map_key = f"{casa}:{ev_id}" if ev_id not in (None, "") else None
    pinned = hmap.get(map_key) if map_key else None
    hn_now, an_now = norm_team(ev.get("home_raw")), norm_team(ev.get("away_raw"))

    if pinned:
        same_names = gscore(hn_now, an_now, pinned.get("hn") or "", pinned.get("an") or "") >= 80
        if not same_names:
            pinned = None  # a casa reaproveitou o event_id pra outro jogo — descarta pin
            hmap.pop(map_key, None)

    idt = resolve_fixture(
        ev["home_raw"], ev["away_raw"], ev["start"],
        league=ev.get("league") or "", fixtures=fixtures,
    )

    if pinned:
        pinned["last_seen"] = now_iso
        if pinned.get("sofa_id"):
            return {
                "home": pinned.get("home") or ev["home_raw"],
                "away": pinned.get("away") or ev["away_raw"],
                "hn": pinned["hn"], "an": pinned["an"],
                "day": pinned["day"], "sofa_id": pinned["sofa_id"],
                "match_method": "house_map",
                "kickoff_iso": pinned.get("kickoff_iso") or idt.get("kickoff_iso"),
                "match_confidence": pinned.get("confidence") or 90,
                "match_evidence": {"method": "house_map", "pinned_by": pinned.get("method")},
                "league": ev.get("league") or "",
            }
        if idt.get("sofa_id") and idt.get("match_method") in PIN_METHODS:
            hmap[map_key] = _pin_from_idt(idt, now_iso)   # promove legado → sofa
            return idt
        # pin legado: preserva o gid (dia civil da 1ª captura) contra flutuação de fuso
        idt = dict(idt)
        idt.update({"hn": pinned["hn"], "an": pinned["an"], "day": pinned["day"],
                    "sofa_id": None, "match_method": "house_map_legacy",
                    "match_confidence": pinned.get("confidence") or idt.get("match_confidence")})
        return idt

    if map_key and idt.get("day") and idt["day"] != "?":
        if idt.get("sofa_id") and idt.get("match_method") in PIN_METHODS:
            hmap[map_key] = _pin_from_idt(idt, now_iso)
        elif not idt.get("sofa_id"):
            hmap[map_key] = {
                "sofa_id": None, "hn": idt["hn"], "an": idt["an"], "day": idt["day"],
                "home": idt.get("home"), "away": idt.get("away"),
                "kickoff_iso": idt.get("kickoff_iso"),
                "method": idt.get("match_method"), "confidence": idt.get("match_confidence"),
                "first_seen": now_iso, "last_seen": now_iso,
            }
    return idt


def _pin_from_idt(idt, now_iso):
    return {
        "sofa_id": idt["sofa_id"], "hn": idt["hn"], "an": idt["an"], "day": idt["day"],
        "home": idt.get("home"), "away": idt.get("away"),
        "kickoff_iso": idt.get("kickoff_iso"),
        "method": idt.get("match_method"), "confidence": idt.get("match_confidence"),
        "first_seen": now_iso, "last_seen": now_iso,
    }


def main():
    now = datetime.now(BRT)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    (HIST / "ticks").mkdir(parents=True, exist_ok=True)
    (HIST / "keys").mkdir(parents=True, exist_ok=True)
    month = now.strftime("%Y-%m")
    # O documento do mês é repartido por CASA desde 31/07 (ver history_shard): o
    # arquivo único bateu nos 100 MB do GitHub e a Mesa parou de persistir. Em
    # memória nada muda — carrega a UNIÃO na MESMA ordem, porque `unify_keys_dict`
    # dedupa confronto e depende da ordem em que as chaves chegam.
    fixtures = load_sofa_fixtures()
    keys = load_month(HIST / "keys", month)
    keys, merge_stats = migrate_keys_dict(keys, fixtures)
    # dedup de confrontos (mesmo jogo grafado diferente por casas / dia ±1)
    keys, _gid_alias, _ustats = unify_keys_dict(keys)
    tick_path = HIST / "ticks" / f"{now.strftime('%Y-%m-%d')}.jsonl"
    if tick_path.exists():
        migrate_tick_file(tick_path, fixtures)
    tick_f = tick_path.open("a", encoding="utf-8")

    # main line state (persistido no próprio keys file sob __main_lines__)
    main_store = keys.get("__main_lines__") or {}
    if not isinstance(main_store, dict):
        main_store = {}

    # batch: (casa,gid,mercado) -> list of {linha,over,under}
    batch_ou = defaultdict(list)
    # (casa,gid,mercado) -> chaves daquele mercado nesta rodada. O movimento de LINHA
    # é um fato do MERCADO (a linha principal andou), não de uma chave — que é sempre
    # uma linha fixa. Sem este índice o `n_line_moves` da chave não tinha quem o
    # incrementasse e ficava 0 em 100% das chaves (38.879 medidas em 03/08), enquanto
    # os ticks registravam os movimentos. Contador que mente é pior que contador que
    # falta: quem lê a chave concluía "nenhuma linha se mexeu".
    mkt_keys = defaultdict(set)

    hmap = prune_house_map(load_house_map(), now)

    n_ticks = n_new = n_obs = n_sofa = n_skip_post = n_line_moves = 0
    n_time_ok = n_time_sem_lado = 0
    for casa in CASAS:
        for ev in load_events(casa):
            idt = resolve_identity(casa, ev, fixtures, hmap, now_iso)
            if not idt["day"] or idt["day"] == "?":
                continue
            if idt.get("sofa_id"):
                n_sofa += 1
            djogo = idt["day"]
            h, a = idt["hn"], idt["an"]
            gid = _gid(idt, djogo, h, a)
            kick_iso = idt.get("kickoff_iso") or ""

            # MERCADOS POR TIME (03/08). Eram capturados pelas 8 casas, exibidos pelo
            # board e IGNORADOS aqui — 4.304 linhas por rodada sem histórico, sem CLV,
            # sem movimento de linha, sem backtest. O dado já vinha de graça.
            # ⚠️ O time NÃO entra na chave pelo nome: cada casa grafa diferente
            # ('Djurgarden IF' x 'Djurgardens IF') e a chave fragmentaria. Resolve-se
            # o LADO (home/away) com o mesmo `_assign_side` que o board usa, e o
            # mercado vira 'Escanteios@home'. Fail-closed: lado ambíguo é DESCARTADO,
            # porque gravar no lado errado é pior que não gravar.
            mercados_do_evento = dict(ev["mercados"] or {})
            for canon, por_time in (ev.get("mercados_time") or {}).items():
                if not isinstance(por_time, dict):
                    continue
                for time_nome, linhas_t in por_time.items():
                    lado_t = _assign_side(time_nome, h, a)
                    if lado_t not in ("home", "away") or not linhas_t:
                        n_time_sem_lado += 1
                        continue
                    mercados_do_evento[f"{canon}@{lado_t}"] = linhas_t
                    n_time_ok += 1

            for mercado, linhas in mercados_do_evento.items():
                # coleta O/U da partida p/ main line
                for l in linhas:
                    if l.get("over") and l.get("under") and l.get("linha") is not None:
                        batch_ou[(casa, gid, mercado)].append({
                            "linha": l["linha"], "over": l["over"], "under": l["under"],
                        })

                for l in linhas:
                    linha = l.get("linha")
                    if linha is None:
                        continue
                    # over/under é o caso de sempre; casa/fora entrou em 29/07
                    # com o handicap de cartões (2 vias com MANDO, não com
                    # Mais/Menos). Os dois pares nunca coexistem na mesma linha
                    # — o fetcher emite um OU o outro — então listar os 4 lados
                    # aqui é seguro e evita um `if mercado ==` que apodrece.
                    for lado, odd in (("over", l.get("over")), ("under", l.get("under")),
                                      ("casa", l.get("casa")), ("fora", l.get("fora"))):
                        if not odd or odd <= 1.01 or odd > 50:
                            continue
                        n_obs += 1
                        key = history_key(
                            casa, djogo, h, a, mercado, linha, lado,
                            sofa_id=idt.get("sofa_id"),
                        )
                        mkt_keys[(casa, gid, mercado)].add(key)
                        k = keys.get(key)
                        pre_ko = is_pre_kickoff(now, kick_iso) if kick_iso else True
                        is_new = k is None
                        # price_move só se já existia e odd mudou ≥0.01 (1ª obs: n_moves=0)
                        price_moved = (not is_new) and (
                            abs((k.get("last_odd") or 0) - odd) >= 0.01
                        )

                        if is_new:
                            # open só “vale” se 1ª vista pré-kickoff; senão marca post
                            keys[key] = k = {
                                "open_odd": odd, "open_ts": now_iso, "open_is_first_seen": True,
                                "close_odd": None, "close_ts": None,
                                "last_odd": odd, "last_ts": now_iso,
                                "n_obs": 0, "n_moves": 0,  # 1ª obs → n_moves permanece 0
                                "n_price_moves": 0, "n_line_moves": 0,
                                "max_odd": odd, "min_odd": odd,
                                "kickoff": kick_iso,
                                "home_raw": ev["home_raw"], "away_raw": ev["away_raw"],
                                "home_norm": h, "away_norm": a,
                                # 10/08 (auditoria D2): liga vira campo PRÓPRIO na
                                # criação — antes pegava carona no carimbo do modelo
                                # (m_comp) e 62% das chaves de julho ficaram sem liga.
                                # É o rótulo CRU da casa; análise normaliza depois.
                                "league_raw": ev.get("league") or None,
                                "sofa_id": idt.get("sofa_id"),
                                "match_method": idt.get("match_method"),
                                "match_confidence": idt.get("match_confidence"),
                                "match_evidence": idt.get("match_evidence"),
                                "result": None, "won": None, "clv_pct": None, "status": "open",
                            }
                            n_new += 1
                        else:
                            if idt.get("sofa_id") and not k.get("sofa_id"):
                                k["sofa_id"] = idt["sofa_id"]
                                k["match_method"] = idt.get("match_method")
                                k["match_confidence"] = idt.get("match_confidence")
                                if idt.get("match_evidence"):
                                    k["match_evidence"] = idt["match_evidence"]
                            if kick_iso and not k.get("kickoff"):
                                k["kickoff"] = kick_iso

                        k["n_obs"] = (k.get("n_obs") or 0) + 1

                        # P1: não poluir last com odd pós-kickoff (preserva close real)
                        if k.get("status") == "open":
                            if pre_ko:
                                if price_moved:
                                    k["n_moves"] = (k.get("n_moves") or 0) + 1
                                    k["n_price_moves"] = (k.get("n_price_moves") or 0) + 1
                                k["last_odd"] = odd
                                k["last_ts"] = now_iso
                                k["max_odd"] = max(k.get("max_odd") or odd, odd)
                                k["min_odd"] = min(k.get("min_odd") or odd, odd)
                            else:
                                n_skip_post += 1

                        k["capture_quality"] = compute_capture_quality(k, now)

                        # tick de preço: 1ª obs (open) ou movimento real
                        if pre_ko and (is_new or price_moved):
                            tick_f.write(json.dumps({
                                "ts": now_iso, "kind": "price" if price_moved else "open",
                                "casa": casa, "kickoff": k.get("kickoff"),
                                "home": h, "away": a, "mercado": mercado,
                                "linha": linha, "lado": lado, "odd": odd,
                                "sofa_id": k.get("sofa_id"),
                                "djogo": djogo, "gid": gid,
                            }, ensure_ascii=False) + "\n")
                            n_ticks += 1

    # main line moves por (casa, gid, mercado)
    n_line_open = 0
    for (casa, gid, mercado), ou_list in batch_ou.items():
        main = pick_main_line(ou_list)
        if main is None:
            continue
        mk = f"{casa}|{gid}|{mercado}"
        prev = main_store.get(mk) or {}
        prev_line = prev.get("line")
        sid = gid.replace("sofa:", "") if str(gid).startswith("sofa:") else None
        if prev_line is None:
            # 1ª vez que vemos a linha principal deste mercado. Sem este tick o
            # movimento só era registrado a partir do 2º valor, e a linha de
            # ABERTURA — que é metade da pergunta "abriu 25,5 e fechou 23,5" —
            # não existia em lugar nenhum quando a linha nunca mais se mexia.
            tick_f.write(json.dumps({
                "ts": now_iso, "kind": "line_open",
                "casa": casa, "mercado": mercado, "gid": gid,
                "linha_to": main, "sofa_id": sid,
            }, ensure_ascii=False) + "\n")
            n_line_open += 1
            n_ticks += 1
        elif abs(float(prev_line) - float(main)) >= 0.01:
            tick_f.write(json.dumps({
                "ts": now_iso, "kind": "line_move",
                "casa": casa, "mercado": mercado, "gid": gid,
                "linha_from": prev_line, "linha_to": main,
                "sofa_id": sid,
            }, ensure_ascii=False) + "\n")
            n_line_moves += 1
            n_ticks += 1
            # o mercado andou → todas as chaves DELE herdam a contagem. Fica
            # explícito na chave que a linha principal daquele mercado mudou N
            # vezes (a chave em si é de uma linha fixa e nunca "anda").
            for key in mkt_keys.get((casa, gid, mercado), ()):
                rec = keys.get(key)
                if isinstance(rec, dict):
                    rec["n_line_moves"] = (rec.get("n_line_moves") or 0) + 1
        main_store[mk] = {"line": main, "ts": now_iso}

    keys["__main_lines__"] = main_store
    tick_f.close()
    n_partes, n_rm, tam = save_month(HIST / "keys", month, keys)
    maior = f"maior {max(tam):.1f} MB" if tam else "vazio"
    print(f"[ingest] fatias: {len(tam)} ({n_partes} gravadas, {n_rm} removidas) · {maior}")
    save_house_map(hmap)
    print(
        f"[ingest] {n_obs:,} obs · {n_ticks:,} ticks "
        f"({n_line_moves} line_move, {n_line_open} line_open) · "
        f"{n_new:,} keys novas · sofa_match={n_sofa} · skip_post_ko={n_skip_post} · "
        f"por_time={n_time_ok} (lado indefinido: {n_time_sem_lado}) · "
        f"total keys mês={len(keys):,}"
    )


if __name__ == "__main__":
    main()
