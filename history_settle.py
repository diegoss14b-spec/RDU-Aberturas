# -*- coding: utf-8 -*-
"""Liquida o histórico e mantém backlog retryable/observável.

Resultados ausentes ou uma estatística ainda não publicada nunca viram um
estado terminal: ficam em ``pending_result`` e são tentados novamente em toda
execução.

``unavailable`` tem dois usos, e só um é terminal:
- ``unsupported_market`` — mercado sem mapeamento (``settlement_retryable`` False);
- ``no_result_source`` — jogo velho de liga fora da cobertura do feed. Sai do
  ``pending_result`` para o painel não mentir sobre o backlog acionável, mas
  CONTINUA retryable (cadência semanal): se a fonte um dia cobrir, liquida sozinho.
"""
from __future__ import annotations

import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from rapidfuzz import fuzz

    def ratio(a, b):
        return fuzz.token_set_ratio(a, b)
except Exception:
    import difflib

    def ratio(a, b):
        return 100 * difflib.SequenceMatcher(None, a, b).ratio()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canonical import flags_compatible, norm_team, parse_history_key  # noqa: E402
from history_merge import atomic_write_text, merge_records  # noqa: E402
from history_quality import parse_iso_flex  # noqa: E402  (parser único §10)
from jsonl_shard import append_jsonl_month  # noqa: E402  (GH001: clv mensal fatiado)

HIST = ROOT / "data" / "odds_history"
RES_AUTO = HIST / "results" / "results_auto.json"
RES_MANUAL = HIST / "results" / "manual_results.csv"
RES_STATUS = HIST / "results" / "settlement_status.json"
BRT = timezone(timedelta(hours=-3))

FIELD = {
    "Cartões": "cards",
    "Faltas": "fouls",
    "Finalizações": "shots",
    "Impedimentos": "offsides",
    "Laterais": "throw_ins",
    "Tiros de meta": "goal_kicks",
    "Escanteios": "corners",
    "Chutes no gol": "shots_on_goal",
    "Desarmes": "tackles",
}
RETRYABLE_STATUSES = {"closed", "pending_result"}
PENDING_STATUS = "pending_result"
RETRY_AUDIT_INTERVAL = timedelta(hours=6)
# Jogo VELHO cuja data o feed já publicou em volume não está esperando publicação: é
# liga fora do universo do RDU (Bulgária, Coreia, Finlândia, Islândia, reservas/sub-20)
# — 34.660 apostas de 2.206 jogos em 28/07, 64% do backlog, que nunca vão liquidar e
# inflam o "pendente" para sempre. Sai de ``pending_result`` para o painel mostrar o
# backlog acionável de verdade, mas continua RETRYABLE: se o feed um dia cobrir a liga,
# liquida sozinho. Só a cadência cai para semanal, porque cada auditoria reescreve (e
# commita) um JSON de 83 MB. Nada é apagado — odd, CLV e histórico ficam intactos.
SEM_FONTE_STATUS = "unavailable"
SEM_FONTE_REASON = "no_result_source"
SEM_FONTE_DIAS = 14        # folga enorme sobre o atraso real do feed (~1 ciclo, horas)
SEM_FONTE_MIN_JOGOS = 20   # jogos do feed na janela = prova de que aquele dia saiu
SEM_FONTE_INTERVAL = timedelta(days=7)
# Tolerância de dia no casamento aposta×resultado. A aposta guarda
# ``game_date = kickoff[:10]`` em BRT e o feed guarda a data do ``matches.json``,
# que é a data UTC da Sofa: jogo noturno nas Américas cai no dia seguinte em UTC.
# Medido em 28/07: 84 jogos / 11.994 apostas (22% do backlog) travados por 1 dia,
# TODOS com deslocamento +1. Ver ``find_result`` para as travas de identidade.
JANELA_DIAS = 1


def nrm(value):
    return norm_team(value)


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_results():
    """Carrega resultados automáticos e complementos manuais."""
    out = []
    if RES_AUTO.exists():
        try:
            data = json.loads(RES_AUTO.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict):
                        rec = dict(row)
                        rec["_source"] = "auto"
                        out.append(rec)
        except (OSError, ValueError):
            pass
    if RES_MANUAL.exists():
        try:
            with RES_MANUAL.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    rec = {
                        "date": row.get("date"),
                        "home": row.get("home"),
                        "away": row.get("away"),
                        "_source": "manual",
                    }
                    for field in set(FIELD.values()):
                        rec[field] = _number((row.get(field) or "").strip())
                    out.append(rec)
        except (OSError, ValueError):
            pass
    for row in out:
        row["_h"] = nrm(row.get("home"))
        row["_a"] = nrm(row.get("away"))
    return out


def _dia(value):
    """``'YYYY-MM-DD…'`` -> ``date``; ``None`` se não for data ISO."""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _janela_datas(date_str):
    """Datas aceitas no casamento: a exata mais ±``JANELA_DIAS``.

    Fail-closed: data que não parseia devolve só ela mesma (casamento exato, o
    comportamento antigo) — nunca uma janela maior do que a pedida.
    """
    base = _dia(date_str)
    if base is None:
        return {date_str}
    return {
        (base + timedelta(days=off)).isoformat()
        for off in range(-JANELA_DIAS, JANELA_DIAS + 1)
    }


def _offset_dias(row_date, game_date):
    """Dias entre o registro do feed e a data da aposta (0 = casou na data exata)."""
    feed, bet = _dia(row_date), _dia(game_date)
    if feed is None or bet is None:
        return None
    return (feed - bet).days


def find_result(results, date, home, away, field=None):
    """Melhor jogo na janela de ±1 dia; data exata vence, depois estatística
    disponível, e manual precede auto.

    PORQUÊ da janela (28/07): a data da aposta é BRT e a do feed é UTC (§ JANELA_DIAS)
    — exigir igualdade deixava 11.994 apostas de 84 jogos sem liquidar para sempre
    (Internacional×Cruzeiro, São Paulo×Athletico, MLS inteira).

    As travas de identidade continuam as MESMAS — par ORDENADO por mando e fuzzy ≥85
    nos DOIS times — mais o guard de marcador (sub-XX/feminino/time B) que a prova de
    28/07 mostrou faltar. Medido no feed inteiro (1.442 jogos): nenhum par de times — nem
    por igualdade, nem por fuzzy ≥85 — aparece em dias consecutivos, ou seja, o
    vizinho nunca é "outro jogo dos mesmos times". Das 2.412 janelas do backlog só 3
    ficam com mais de um candidato, e nas 3 é o MESMO jogo duplicado por variante de
    nome no feed ('Universidad Católica' × 'Universidad Católica (CHI)'), com as 9
    estatísticas idênticas. Ainda assim o desempate final é por ``(date, home, away)``
    do registro, para a escolha ser DETERMINÍSTICA e não depender da ordem do arquivo.
    """
    janela = _janela_datas(date)
    candidates = []
    for row in results:
        row_date = row.get("date")
        if row_date not in janela:
            continue
        # Guard de sub-XX/feminino/time B: o token_set_ratio dá 100 quando um nome é
        # SUPERCONJUNTO do outro, então 'cruz azul sub 21' × 'cruz azul' pontuava 100/100
        # e a aposta do sub-21 liquidava com a estatística do jogo profissional — 12
        # apostas em 5 confrontos na medição de 28/07 (Cruz Azul/Puebla, Toluca/Pumas,
        # Pachuca/Querétaro). É o mesmo guard que a Mesa e o bot de chasing usam.
        if not flags_compatible(home, away, row.get("_h") or "", row.get("_a") or ""):
            continue
        score = min(ratio(home, row.get("_h") or ""), ratio(away, row.get("_a") or ""))
        if score >= 85:
            exact_day = row_date == date
            has_field = row.get(field) is not None if field else False
            is_manual = row.get("_source") == "manual"
            candidates.append((exact_day, has_field, is_manual, score, row))
    if not candidates:
        return None
    # Ordem crescente com as prioridades negadas: data exata > tem a stat > manual >
    # score. O rabo (date, home, away) é só desempate determinístico.
    candidates.sort(
        key=lambda item: (
            -int(item[0]),
            -int(item[1]),
            -int(item[2]),
            -item[3],
            str(item[4].get("date") or ""),
            str(item[4].get("home") or ""),
            str(item[4].get("away") or ""),
        )
    )
    return candidates[0][4]


def consolidate_key_documents(documents):
    """Mescla chaves repetidas entre meses e escolhe o arquivo mais recente."""
    merged, owners = {}, {}
    duplicates = 0
    for path, keys in documents:
        for key, record in keys.items():
            if key.startswith("__") or not isinstance(record, dict):
                continue
            if key in merged:
                merged[key] = merge_records(merged[key], record)
                duplicates += 1
            else:
                merged[key] = dict(record)
            # O mês mais recente evita recriar a duplicata a cada novo ingest.
            owners[key] = path
    return merged, owners, duplicates


def persist_consolidated_documents(documents, merged, owners):
    """Remove cópias antigas e grava o estado global por rename atômico."""
    changed_files = 0
    for path, original in documents:
        updated = {
            key: value
            for key, value in original.items()
            if key.startswith("__") or not isinstance(value, dict)
        }
        for key, owner in owners.items():
            if owner == path:
                updated[key] = merged[key]
        if updated != original:
            atomic_write_text(path, json.dumps(updated, ensure_ascii=False))
            changed_files += 1
    return changed_files


def _parse_dt(value):
    """Parser único §10 — aceita Z / -03:00 / -0300 igual no py3.9 e py3.12.
    Stamp ingênuo recebe BRT explicitamente (não é chute mudo: BRT é o fuso do banco)."""
    parsed = parse_iso_flex(value, default_tz=BRT)
    return parsed.astimezone(BRT) if parsed is not None else None


def cobertura_por_dia(results):
    """Quantos jogos o feed publicou em cada data — prova de que o dia saiu."""
    por_dia = Counter()
    for row in results:
        dia = row.get("date")
        if dia:
            por_dia[dia] += 1
    return por_dia


def _sem_fonte(game_date, cobertura, now):
    """Jogo velho + dia bem coberto pelo feed = liga fora do universo do RDU.

    Fail-closed nos dois lados: data ilegível ou dia pouco coberto continua em
    ``pending_result`` (o caro é rotular de "sem fonte" um jogo que só atrasou).
    """
    dia = _dia(game_date)
    if dia is None or (now.date() - dia).days < SEM_FONTE_DIAS:
        return False
    return sum(cobertura.get(d, 0) for d in _janela_datas(game_date)) >= SEM_FONTE_MIN_JOGOS


def _mark_pending(record, reason, now, status=PENDING_STATUS, interval=RETRY_AUDIT_INTERVAL):
    changed = (
        record.get("status") != status
        or record.get("settlement_reason") != reason
        or record.get("settlement_retryable") is not True
    )
    last_attempt = _parse_dt(record.get("settlement_last_attempt"))
    audit_due = last_attempt is None or now - last_attempt >= interval
    if changed or audit_due:
        record["status"] = status
        record["result"] = None
        record["won"] = None
        record["settlement_reason"] = reason
        record["settlement_retryable"] = True
        record["settlement_last_attempt"] = now.isoformat()
        record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
        return True
    return False


def settle_one(key, record, results, now, cobertura=None):
    """Tenta liquidar uma key. Retorna ``(outcome, changed, clv_row)``.

    ``cobertura`` é o mapa data->nº de jogos do feed (``cobertura_por_dia``); vem
    pronto do ``main`` para não recontar 1.442 linhas em cada uma das 90 mil keys.
    """
    meta = parse_history_key(key)
    market = meta.get("mercado")
    field = FIELD.get(market)
    if not field:
        changed = record.get("status") != "unavailable"
        record["status"] = "unavailable"
        record["result"] = None
        record["won"] = None
        record["settlement_reason"] = "unsupported_market"
        record["settlement_retryable"] = False
        record["settlement_last_attempt"] = now.isoformat()
        record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
        return "unavailable", changed, None

    try:
        line = float(meta.get("linha"))
    except (TypeError, ValueError):
        changed = _mark_pending(record, "invalid_line", now)
        return "pending", changed, None

    game_date = (record.get("kickoff") or "")[:10] or meta.get("day") or ""
    home = record.get("home_norm") or nrm(record.get("home_raw") or meta.get("hn") or "")
    away = record.get("away_norm") or nrm(record.get("away_raw") or meta.get("an") or "")
    result_row = find_result(results, game_date, home, away, field=field)
    if result_row is None:
        if cobertura is None:
            cobertura = cobertura_por_dia(results)
        if _sem_fonte(game_date, cobertura, now):
            changed = _mark_pending(record, SEM_FONTE_REASON, now,
                                    status=SEM_FONTE_STATUS, interval=SEM_FONTE_INTERVAL)
            return "sem_fonte", changed, None
        changed = _mark_pending(record, "game_not_in_results", now)
        return "pending", changed, None

    result = _number(result_row.get(field))
    if result is None:
        changed = _mark_pending(record, f"stat_missing:{field}", now)
        return "pending", changed, None

    # Origem do casamento, para a auditoria separar o que veio da tolerância de dia.
    # Só grava quando HOUVE deslocamento — ausência do campo significa data exata.
    # (o keys/*.json tem 83 MB e é commitado ~19x/dia; não vale inflar 30 mil
    # registros para dizer "0"). O pop mantém idempotência se a key for re-liquidada
    # com o feed já corrigido.
    date_offset = _offset_dias(result_row.get("date"), game_date)
    if date_offset:
        record["settlement_date_offset"] = date_offset
    else:
        record.pop("settlement_date_offset", None)

    # ⚠ FAIL-CLOSED DO LADO (29/07). A linha era `side = meta.get("lado") or "over"`
    # e logo abaixo `won = over_won if side == "over" else not over_won` — ou seja,
    # QUALQUER lado que não fosse "over" era liquidado como se fosse "under". Com a
    # entrada do handicap de cartões (lados `casa`/`fora`, 29/07) isso liquidaria a
    # perna do MANDANTE como "menos cartões no jogo": errado em silêncio, e o erro
    # iria direto pro CLV. Hoje o handicap nem chega aqui (não está no FIELD e cai
    # em `unsupported_market` lá em cima), mas isto é a trava pra quando alguém
    # mapear o mercado sem lembrar que o desempate dele é OUTRO.
    side = meta.get("lado") or "over"
    if side not in ("over", "under"):
        changed = record.get("status") != "unavailable"
        record["status"] = "unavailable"
        record["result"] = None
        record["won"] = None
        record["settlement_reason"] = "unsupported_side:%s" % side
        record["settlement_retryable"] = False
        record["settlement_last_attempt"] = now.isoformat()
        record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
        return "unavailable", changed, None
    record["result"] = result
    # B2 (31/07): a Mesa liquida cartões como AMARELOS+VERMELHOS (o feed manda
    # `cards` = y+r) e o modelo do site prevê AMARELOS — 95 chaves (2,4%) trocavam
    # de vencedor conforme a definição no backtest de 30/07. `won` NÃO muda de
    # semântica; os dois números viajam JUNTOS para a análise honesta dos dois
    # lados (ledger/CLV). Lei do null: feed sem os campos → None, nunca 0.
    if field == "cards":
        record["result_yellows"] = _number(result_row.get("yellow_cards"))
        record["result_reds"] = _number(result_row.get("red_cards"))
        # 10/08 (auditoria D1): TODAS as casas da Mesa liquidam vermelho DIRETO=2
        # (Betano/bet365/Sportingbet/Superbet/EstrelaBet/7k/Betfast — red=1 é só
        # Betfair/Betnacional, que a Mesa não captura). O feed do site agora manda
        # cards_r2 = Y + 2·direto + 1·2º-amarelo (dos INCIDENTES) — quando existe,
        # a liquidação usa a régua REAL da casa; sem incidentes, segue o y+r antigo
        # com o carimbo r1_fallback pra análise filtrar. Os DOIS números viajam.
        _r2 = _number(result_row.get("cards_r2"))
        record["result_r1"] = result
        if _r2 is not None:
            record["result_r2"] = _r2
            record["settlement_rule"] = "red2_incidents"
            result = _r2
            record["result"] = result
        else:
            record["result_r2"] = None
            record["settlement_rule"] = "r1_fallback"
    if abs(result - line) < 1e-9:
        record["won"] = None
    else:
        over_won = result > line
        record["won"] = over_won if side == "over" else not over_won
    if record.get("open_odd") and record.get("close_odd"):
        record["clv_pct"] = round(
            (float(record["open_odd"]) / float(record["close_odd"]) - 1) * 100, 2
        )
        record["beat_close"] = record["clv_pct"] > 0
    record["status"] = "settled"
    record["settled_at"] = now.isoformat()
    record["settlement_last_attempt"] = now.isoformat()
    record["settlement_attempts"] = int(record.get("settlement_attempts") or 0) + 1
    record["settlement_reason"] = "settled"
    record["settlement_retryable"] = False
    record["settlement_source"] = result_row.get("_source") or "auto"
    clv_row = {
        "key": key,
        "casa": meta.get("casa") or key.split("|")[0],
        "mercado": market,
        "linha": line,
        "lado": side,
        "open_odd": record.get("open_odd"),
        "close_odd": record.get("close_odd"),
        "clv_pct": record.get("clv_pct"),
        "beat_close": record.get("beat_close"),
        "result": result,
        "won": record.get("won"),
        "kickoff": record.get("kickoff") or game_date,
        "sofa_id": record.get("sofa_id"),
        # 0 = casou na data exata; ±1 = veio da janela BRT×UTC (§ JANELA_DIAS)
        "date_offset": date_offset or 0,
    }
    if field == "cards":
        clv_row["result_yellows"] = record.get("result_yellows")
        clv_row["result_reds"] = record.get("result_reds")
    return "settled", True, clv_row


# ─────────────────────────────────────────────────────────────────────────────
# B1 (31/07) — backfill de identidade Sofa via feed de resultados.
#
# 463 jogos liquidados estavam SEM sofa_id no backtest de 30/07 (11.775 chaves
# fora de qualquer análise modelo×odds). O recuperador da pesquisa
# (fase1b_recuperacao.py, 193 aceitos / 1 reprovado) vira aqui parte do PIPELINE:
# roda em todo settle, senão o buraco reabre todo dia.
#
# Identidade NÃO se decide por nome (gotcha 25 — homônimo/sub-20/reservas). O
# nome só PROPÕE (o MESMO find_result do settle: fuzzy ≥85 nos dois lados +
# flags_compatible + janela ±1d); quem ACEITA é a corroboração pelo RESULTADO:
# o `result` com que a key foi liquidada tem que bater com a stat da row em
# TODOS os mercados liquidados do jogo (cartões: == cards OU == yellow_cards),
# com 0 divergências toleradas.
#
# O aceite grava `sofa_id_settle` + `settle_match_method` — campo SEPARADO de
# `sofa_id` DE PROPÓSITO: não polui a identidade forte do ingest nem o gate de
# pureza (canonical.sofa_purity lê v["sofa_id"]). Consumidores (ledger/backtest)
# usam coalesce(sofa_id, sofa_id_settle) com proveniência em `id_origem`;
# `settle_match_n` guarda quantos mercados corroboraram (dá pra exigir ≥2).
# ─────────────────────────────────────────────────────────────────────────────
MKT_MODELO = {"Cartões", "Faltas", "Finalizações", "Escanteios"}


def backfill_sofa_from_feed(merged, results):
    """Preenche ``sofa_id_settle`` nas keys liquidadas órfãs. Devolve contagens."""
    groups = defaultdict(list)
    for key, record in merged.items():
        if key.startswith("__") or not isinstance(record, dict):
            continue
        meta = parse_history_key(key)
        if meta.get("mercado") not in MKT_MODELO:
            continue
        if record.get("status") != "settled":
            continue
        if record.get("sofa_id") or meta.get("sofa_id") or record.get("sofa_id_settle"):
            continue
        game_date = (record.get("kickoff") or "")[:10] or meta.get("day") or ""
        home = record.get("home_norm") or nrm(record.get("home_raw") or meta.get("hn") or "")
        away = record.get("away_norm") or nrm(record.get("away_raw") or meta.get("an") or "")
        if not game_date or not home or not away:
            continue
        groups[(game_date, home, away)].append((key, record, meta))

    n = Counter()
    for (game_date, home, away), items in groups.items():
        # uma busca por MERCADO distinto (não por key): a row é a mesma pro jogo
        fields = sorted({FIELD[meta["mercado"]] for _, _, meta in items})
        rows = []
        for field in fields:
            row = find_result(results, game_date, home, away, field=field)
            if row is not None and not any(r is row for r in rows):
                rows.append(row)
        if not rows:
            n["sem_candidato_no_feed"] += 1
            continue
        # todas as buscas têm que apontar pro MESMO jogo, com sofa_id declarado
        idents = {(r.get("date"), r.get("_h"), r.get("_a")) for r in rows}
        sids = {str(r.get("sofa_id")) for r in rows if r.get("sofa_id") not in (None, "")}
        if not sids:
            n["row_sem_sofa_id"] += 1
            continue
        if len(idents) > 1 or len(sids) > 1:
            n["rows_conflitantes"] += 1
            continue
        row = rows[0]
        # Critério da pesquisa (fase1b): o result bate em TODOS os mercados
        # liquidados. Mercado cujo result NÃO PODE ser conferido (a row aceita
        # não tem a stat) não é neutro: a row que o liquidou TINHA a stat, logo
        # provavelmente foi OUTRA row — grupo inteiro fica de fora (ataque
        # 31/07: sem isso, uma key não-conferível herdava o sofa_id na carona
        # da corroboração de outro mercado).
        corroboradas, divergiu, inconferivel = 0, False, False
        for key, record, meta in items:
            field = FIELD[meta["mercado"]]
            got = record.get("result")
            if got is None:
                continue
            esperado = _number(row.get(field))
            candidatos = [esperado]
            if field == "cards":
                candidatos.append(_number(row.get("yellow_cards")))
            candidatos = [c for c in candidatos if c is not None]
            if not candidatos:
                inconferivel = True
                continue
            if any(abs(float(got) - c) < 1e-9 for c in candidatos):
                corroboradas += 1
            else:
                divergiu = True
        if divergiu:
            n["reprovado_pelo_resultado"] += 1
            continue
        if inconferivel or corroboradas < 1:
            n["sem_stat_para_corroborar"] += 1
            continue
        sid_raw = next(iter(sids))
        try:
            sid = int(sid_raw)
        except ValueError:
            n["sofa_id_ilegivel"] += 1
            continue
        for key, record, _meta in items:
            record["sofa_id_settle"] = sid
            record["settle_match_method"] = "feed_name_corroborated"
            record["settle_match_n"] = corroboradas
        n["jogos_recuperados"] += 1
        n["chaves_recuperadas"] += len(items)
    return n


def _age_bucket(kickoff, now):
    parsed = _parse_dt(kickoff)
    if parsed is None:
        return "unknown", None
    age_days = (now - parsed).total_seconds() / 86400
    if age_days < 0:
        return "not_started", age_days
    if age_days < 1:
        return "0-24h", age_days
    if age_days < 3:
        return "1-3d", age_days
    if age_days < 7:
        return "3-7d", age_days
    if age_days < 30:
        return "7-30d", age_days
    return "30d+", age_days


def build_settlement_status(records, results, now):
    """Resumo operacional por mercado, status, motivo e idade do backlog."""
    total_status = Counter()
    by_market = defaultdict(
        lambda: {
            "total": 0,
            "status": Counter(),
            "pending_age": Counter(),
            "pending_reasons": Counter(),
        }
    )
    backlog_age, backlog_reasons = Counter(), Counter()
    backlog_samples = []
    # Quantas liquidações vieram da janela de ±1 dia (§ JANELA_DIAS) — se um dia a
    # origem do feed passar a emitir data BRT, isso cai para 0 sozinho e a auditoria vê.
    settled_offsets = Counter()

    for key, record in records:
        meta = parse_history_key(key)
        market = meta.get("mercado") or "unknown"
        status = record.get("status") or "unknown"
        total_status[status] += 1
        market_row = by_market[market]
        market_row["total"] += 1
        market_row["status"][status] += 1
        if status == "settled":
            settled_offsets[int(record.get("settlement_date_offset") or 0)] += 1
        if status == PENDING_STATUS:
            bucket, age_days = _age_bucket(record.get("kickoff"), now)
            reason = record.get("settlement_reason") or "unknown"
            market_row["pending_age"][bucket] += 1
            market_row["pending_reasons"][reason] += 1
            backlog_age[bucket] += 1
            backlog_reasons[reason] += 1
            backlog_samples.append(
                {
                    "key": key,
                    "market": market,
                    "kickoff": record.get("kickoff"),
                    "age_days": round(age_days, 2) if age_days is not None else None,
                    "reason": reason,
                    "attempts": record.get("settlement_attempts") or 0,
                }
            )

    coverage = {}
    for field in sorted(set(FIELD.values())):
        available = sum(1 for row in results if row.get(field) is not None)
        coverage[field] = {"available": available, "missing": len(results) - available}

    def plain_market(row):
        return {
            "total": row["total"],
            "status": dict(row["status"]),
            "pending_age": dict(row["pending_age"]),
            "pending_reasons": dict(row["pending_reasons"]),
        }

    backlog_samples.sort(
        key=lambda row: row.get("age_days") if row.get("age_days") is not None else -1,
        reverse=True,
    )
    return {
        "generated_at": now.isoformat(),
        "results_rows": len(results),
        "result_field_coverage": coverage,
        "totals_by_status": dict(total_status),
        "settled_date_offsets": {str(off): n for off, n in sorted(settled_offsets.items())},
        "by_market": {
            market: plain_market(row) for market, row in sorted(by_market.items())
        },
        "backlog": {
            "total": total_status.get(PENDING_STATUS, 0),
            "age": dict(backlog_age),
            # §10: pendências cuja data não parseou. Com o parser único deve ser ~0;
            # um valor alto = kickoff corrompido ou formato novo → alerta, não "recente".
            "age_unknown": backlog_age.get("unknown", 0),
            "reasons": dict(backlog_reasons),
            "oldest_samples": backlog_samples[:25],
        },
    }


def _append_clv(rows):
    """Registro imutável do estudo (append-only). GH001 (02/09): o arquivo do mês
    é FATIADO via jsonl_shard — o feed de resultados chega em cadência ~semanal e
    liquida dias de backlog de uma vez; o append da rajada estourava os 100 MB do
    GitHub no monólito mensal e derrubava o persist da Mesa em loop."""
    if not rows:
        return
    by_month = defaultdict(list)
    for row in rows:
        kickoff = str(row.get("kickoff") or "")
        month = kickoff[:7] if len(kickoff) >= 7 else datetime.now(BRT).strftime("%Y-%m")
        by_month[month].append(row)
    for month, month_rows in by_month.items():
        append_jsonl_month(HIST / "clv", month, month_rows)


def main():
    now = datetime.now(BRT)
    results = load_results()
    print(f"[settle] resultados disponíveis: {len(results):,}")
    outcomes = Counter()
    clv_rows = []
    paths = [Path(name) for name in sorted(glob.glob(str(HIST / "keys" / "*.json")))]
    documents = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    merged, owners, duplicates = consolidate_key_documents(documents)
    cobertura = cobertura_por_dia(results)

    for key, record in merged.items():
        current_status = record.get("status")
        retryable = current_status in RETRYABLE_STATUSES or (
            current_status == "unavailable" and record.get("settlement_retryable") is not False
        )
        if retryable:
            outcome, _record_changed, clv_row = settle_one(
                key, record, results, now, cobertura=cobertura
            )
            outcomes[outcome] += 1
            if clv_row:
                clv_rows.append(clv_row)

    # B1: identidade tardia por corroboração de resultado, ANTES do persist —
    # o sofa_id_settle entra no mesmo write atômico da liquidação desta rodada.
    bf = backfill_sofa_from_feed(merged, results)
    changed_files = persist_consolidated_documents(documents, merged, owners)
    all_records = list(merged.items())

    _append_clv(clv_rows)
    status = build_settlement_status(all_records, results, now)
    RES_STATUS.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(RES_STATUS, json.dumps(status, ensure_ascii=False, indent=2))
    print(
        f"[settle] {outcomes['settled']:,} liquidadas · "
        f"{outcomes['pending']:,} em retry · {outcomes['unavailable']:,} sem mapeamento · "
        f"{outcomes['sem_fonte']:,} fora da cobertura do feed (retry semanal)"
    )
    if bf:
        print(
            f"[settle] backfill sofa (B1): {bf.get('jogos_recuperados', 0):,} jogos / "
            f"{bf.get('chaves_recuperadas', 0):,} chaves com sofa_id_settle · "
            f"reprovados pelo resultado: {bf.get('reprovado_pelo_resultado', 0):,} · "
            f"rows sem sofa_id no feed: {bf.get('row_sem_sofa_id', 0):,}"
        )
    _age_unknown = (status.get("backlog") or {}).get("age_unknown", 0)
    if _age_unknown:
        print(f"[settle] ⚠ {_age_unknown:,} pendências com data NÃO parseável (age=unknown) — "
              f"verifique o formato do kickoff (§10)")
    if duplicates:
        print(f"[settle] {duplicates:,} duplicatas mensais consolidadas · {changed_files} arquivos atualizados")
    for market, row in status["by_market"].items():
        pending = row["status"].get(PENDING_STATUS, 0)
        if pending:
            ages = ", ".join(f"{name}={count}" for name, count in row["pending_age"].items())
            print(f"  [backlog] {market}: {pending} · {ages}")
    print(f"[settle] observabilidade -> {RES_STATUS}")


if __name__ == "__main__":
    main()
