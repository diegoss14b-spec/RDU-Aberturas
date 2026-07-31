# -*- coding: utf-8 -*-
"""Ataque adversarial ao casador B1 (backfill_sofa_from_feed) — FASE 3, 31/07.

O casador NÃO decide identidade por nome (gotcha 25): o nome PROPÕE via o mesmo
find_result do settle e o RESULTADO corrobora. Estes testes provam o fail-closed
nos casos que já quebraram sinal em outros lugares do projeto:

- doubleheader / jogo adiado re-marcado (mesmo par ordenado em dias vizinhos);
- feed corrigido depois do settle (stat mudou → divergência REPROVA);
- mercados apontando para jogos DIFERENTES na janela (rows_conflitantes);
- sub-XX/feminino/time B (flags_compatible bloqueia o candidato);
- row sem a stat → sem corroboração → NÃO atribui (sem_stat_para_corroborar);
- cartões aceitam == cards OU == yellow_cards (B2), mas nada além disso.

⚠ LIMITE CONHECIDO (documentado de propósito): quando settle e backfill rodam
na MESMA rodada com o MESMO feed, a corroboração é circular (o result da key
veio da própria row) — a proteção real de identidade é o find_result (par
ordenado, fuzzy ≥85 nos dois lados, flags, ±1d, data exata primeiro) e a
corroboração só ganha dente cruzando TEMPO (key liquidada com feed antigo ×
feed atual corrigido) ou FONTE (manual × auto). Por isso o método fica gravado
em settle_match_method/settle_match_n: consumidor exigente filtra.
"""
from datetime import datetime

from history_settle import BRT, backfill_sofa_from_feed, find_result, settle_one

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=BRT)


def row(date, home, away, sofa_id=None, **stats):
    r = {"date": date, "home": home, "away": away, "_source": "auto"}
    if sofa_id is not None:
        r["sofa_id"] = sofa_id
    r.update(stats)
    from history_settle import nrm
    r["_h"], r["_a"] = nrm(home), nrm(away)
    return r


def settled_key(mercado, linha, result, day="2026-07-25", home="Time Alpha",
                away="Time Beta", **extra):
    rec = {"status": "settled", "result": result, "won": True,
           "kickoff": f"{day}T16:00:00-03:00",
           "home_raw": home, "away_raw": away}
    rec.update(extra)
    key = f"betano|{day}|{home.lower()}|{away.lower()}|{mercado}|{linha}|over"
    return key, rec


def test_doubleheader_data_exata_vence_e_replay_corrigido_reprova():
    """Par ordenado jogando em D e D+1 (adiado/re-marcado): a key do dia D casa
    com a row do dia D (data exata > janela). Uma key cujo result veio do OUTRO
    jogo (feed antigo) DIVERGE da row exata → reprovada, nada atribuído."""
    feed = [
        row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100, cards=4.0),
        row("2026-07-26", "Time Alpha", "Time Beta", sofa_id=200, cards=7.0),
    ]
    # caso são: result bate com a row exata → aceita com o sofa_id do dia D
    k1, r1 = settled_key("Cartões", "3.5", 4.0)
    n = backfill_sofa_from_feed({k1: r1}, feed)
    assert n["jogos_recuperados"] == 1 and r1["sofa_id_settle"] == 100
    assert r1["settle_match_method"] == "feed_name_corroborated"

    # caso adversarial: result 7.0 (veio do jogo re-marcado, feed antigo) não
    # bate com a row exata do dia D → REPROVA, sem sofa_id
    k2, r2 = settled_key("Cartões", "3.5", 7.0)
    n = backfill_sofa_from_feed({k2: r2}, feed)
    assert n["reprovado_pelo_resultado"] == 1
    assert "sofa_id_settle" not in r2 and "settle_match_method" not in r2


def test_mercados_apontando_para_jogos_diferentes_reprova():
    """Sem row na data exata e com os mercados resolvendo para VIZINHOS
    diferentes (D-1 tem fouls, D+1 tem cards): rows_conflitantes, nada
    atribuído."""
    feed = [
        row("2026-07-26", "Time Alpha", "Time Beta", sofa_id=200, cards=4.0),
        row("2026-07-24", "Time Alpha", "Time Beta", sofa_id=300, fouls=26.0),
    ]
    k1, r1 = settled_key("Cartões", "3.5", 4.0)
    k2, r2 = settled_key("Faltas", "25.5", 26.0)
    n = backfill_sofa_from_feed({k1: r1, k2: r2}, feed)
    assert n["rows_conflitantes"] == 1
    assert "sofa_id_settle" not in r1 and "sofa_id_settle" not in r2


def test_mercado_inconferivel_derruba_o_grupo_sem_carona():
    """Ataque 31/07: cards bate (circular) mas a key de faltas foi liquidada
    com 26.0 e a row aceita NÃO tem fouls — a key de faltas veio de OUTRA row
    (o settle exige a stat presente). Grupo INTEIRO fica de fora: nada de
    herdar sofa_id na carona da corroboração alheia."""
    feed = [
        row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100, cards=4.0),
        row("2026-07-26", "Time Alpha", "Time Beta", sofa_id=200, fouls=26.0),
    ]
    k1, r1 = settled_key("Cartões", "3.5", 4.0)
    k2, r2 = settled_key("Faltas", "25.5", 26.0)
    n = backfill_sofa_from_feed({k1: r1, k2: r2}, feed)
    assert n["sem_stat_para_corroborar"] == 1
    assert "sofa_id_settle" not in r1 and "sofa_id_settle" not in r2


def test_sub21_nao_casa_com_profissional():
    """Gotcha 25: token_set_ratio dá 100 em superconjunto; quem segura é o
    flags_compatible — a key do sub-21 NÃO pode herdar o sofa_id do jogo
    profissional (nem corroboração salva: o candidato nem entra)."""
    feed = [row("2026-07-25", "Cruz Azul", "Puebla", sofa_id=300, cards=5.0)]
    k, r = settled_key("Cartões", "4.5", 5.0,
                       home="Cruz Azul Sub 21", away="Puebla Sub 21")
    n = backfill_sofa_from_feed({k: r}, feed)
    assert n["sem_candidato_no_feed"] == 1 and "sofa_id_settle" not in r


def test_row_sem_stat_nao_corrobora_e_nao_atribui():
    """Feed atual perdeu a stat que liquidou a key: sem corroboração possível
    → fail-closed (não atribui por nome puro)."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100, cards=None,
                corners=9.0)]
    k, r = settled_key("Cartões", "3.5", 4.0)
    n = backfill_sofa_from_feed({k: r}, feed)
    assert n["sem_stat_para_corroborar"] == 1 and "sofa_id_settle" not in r


def test_row_sem_sofa_id_nao_atribui():
    """Feed ainda sem o patch do site (rows sem sofa_id) = no-op honesto."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", cards=4.0)]
    k, r = settled_key("Cartões", "3.5", 4.0)
    n = backfill_sofa_from_feed({k: r}, feed)
    assert n["row_sem_sofa_id"] == 1 and "sofa_id_settle" not in r


def test_cartoes_aceita_yellow_cards_mas_nada_alem():
    """B2: key liquidada como AMARELOS (feed antigo mandava só yellows) ainda
    corrobora contra a row nova (cards=y+r, yellow_cards=y). Um result que não
    é NEM cards NEM yellow_cards reprova."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100,
                cards=6.0, yellow_cards=5.0)]
    k1, r1 = settled_key("Cartões", "4.5", 5.0)      # == yellow_cards
    n = backfill_sofa_from_feed({k1: r1}, feed)
    assert n["jogos_recuperados"] == 1 and r1["sofa_id_settle"] == 100

    k2, r2 = settled_key("Cartões", "4.5", 4.0)      # nem 6 nem 5
    n = backfill_sofa_from_feed({k2: r2}, feed)
    assert n["reprovado_pelo_resultado"] == 1 and "sofa_id_settle" not in r2


def test_settle_match_n_conta_mercados_corroborados():
    """Grupo com 2 mercados liquidados e batendo → n=2 nas DUAS keys (o
    consumidor pode exigir ≥2 pra aceitar identidade por corroboração)."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100,
                cards=4.0, corners=9.0)]
    k1, r1 = settled_key("Cartões", "3.5", 4.0)
    k2, r2 = settled_key("Escanteios", "8.5", 9.0)
    n = backfill_sofa_from_feed({k1: r1, k2: r2}, feed)
    assert n["jogos_recuperados"] == 1 and n["chaves_recuperadas"] == 2
    assert r1["settle_match_n"] == 2 and r2["settle_match_n"] == 2
    assert r1["sofa_id_settle"] == r2["sofa_id_settle"] == 100


def test_um_mercado_divergente_derruba_o_jogo_inteiro():
    """0 divergências toleradas: corners bate mas cards diverge → NENHUMA key
    do jogo ganha sofa_id (nem a que bateu)."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100,
                cards=4.0, corners=9.0)]
    k1, r1 = settled_key("Cartões", "3.5", 3.0)      # diverge (feed diz 4)
    k2, r2 = settled_key("Escanteios", "8.5", 9.0)   # bate
    n = backfill_sofa_from_feed({k1: r1, k2: r2}, feed)
    assert n["reprovado_pelo_resultado"] == 1
    assert "sofa_id_settle" not in r1 and "sofa_id_settle" not in r2


def test_ja_com_identidade_nao_reprocessa():
    """Key com sofa_id forte (ou sofa_id_settle anterior) fica fora do grupo."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100, cards=4.0)]
    k1, r1 = settled_key("Cartões", "3.5", 4.0, sofa_id="999")
    k2, r2 = settled_key("Escanteios", "8.5", 9.0, sofa_id_settle=888)
    n = backfill_sofa_from_feed({k1: r1, k2: r2}, feed)
    assert n["jogos_recuperados"] == 0
    assert r1.get("sofa_id") == "999" and r2["sofa_id_settle"] == 888


def test_circularidade_mesmo_feed_e_aceite_automatico():
    """DOCUMENTA o limite: settle e backfill com o MESMO feed → o result veio
    da própria row, corroboração passa por construção. A identidade aqui está
    apoiada nos guards do find_result — por isso settle_match_method existe."""
    feed = [row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100, cards=4.0)]
    key, rec = settled_key("Cartões", "3.5", None)
    rec.update({"status": "closed", "result": None, "won": None,
                "open_odd": 1.9, "close_odd": 1.85})
    outcome, _ch, _clv = settle_one(key, rec, feed, NOW)
    assert outcome == "settled" and rec["result"] == 4.0
    n = backfill_sofa_from_feed({key: rec}, feed)
    assert n["jogos_recuperados"] == 1 and rec["sofa_id_settle"] == 100
    assert rec["settle_match_n"] == 1     # 1 mercado só — filtrável a jusante


def test_find_result_prioriza_data_exata_no_doubleheader():
    """A âncora do caso 1: com o par repetido em D e D+1, a busca do dia D
    devolve a row do dia D (nunca a vizinha)."""
    feed = [
        row("2026-07-26", "Time Alpha", "Time Beta", sofa_id=200, cards=7.0),
        row("2026-07-25", "Time Alpha", "Time Beta", sofa_id=100, cards=4.0),
    ]
    got = find_result(feed, "2026-07-25", "time alpha", "time beta", field="cards")
    assert got is not None and got["sofa_id"] == 100
    got2 = find_result(feed, "2026-07-26", "time alpha", "time beta", field="cards")
    assert got2 is not None and got2["sofa_id"] == 200
