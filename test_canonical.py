# -*- coding: utf-8 -*-
"""test_canonical.py — testes mínimos de identidade canônica (P0 do relatório)."""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from canonical import (
    norm_team, history_key, parse_history_key, match_to_sofa, resolve_fixture,
    gscore, ALIASES, unify_gids,
)


def test_aliases_basic():
    cases = [
        ("Ceará", "ceara"),
        ("Ceará CE", "ceara"),
        ("CRB", "crb al"),
        ("CRB AL", "crb al"),
        ("Operário PR", "operario ferroviario"),
        ("Operário Ferroviário", "operario ferroviario"),
        ("France", "franca"),
        ("Spain", "espanha"),
        ("RB Bragantino", "red bull bragantino"),
        ("Sport", "sport recife"),
        ("América Mineiro", "america mg"),
        ("Athletic Club", "athletic club mg"),
        ("Londrina-PR", "londrina"),
        ("Vasco", "vasco da gama"),
    ]
    for raw, expect in cases:
        got = norm_team(raw)
        assert got == expect, f"norm_team({raw!r}) = {got!r}, want {expect!r}"


def test_history_key_sofa_and_legacy():
    k_sofa = history_key("superbet", "2026-07-13", "ceara", "athletic club mg",
                         "Finalizações", 22.5, "over", sofa_id=12345)
    assert k_sofa == "superbet|sofa:12345|Finalizações|22.5|over"
    meta = parse_history_key(k_sofa)
    assert meta["format"] == "sofa"
    assert meta["sofa_id"] == "12345"
    assert meta["mercado"] == "Finalizações"
    assert meta["lado"] == "over"

    k_leg = history_key("betano", "2026-07-13", "ceara", "athletic club mg",
                        "Cartões", 4.5, "under", sofa_id=None)
    assert k_leg == "betano|2026-07-13|ceara|athletic club mg|Cartões|4.5|under"
    meta2 = parse_history_key(k_leg)
    assert meta2["format"] == "legacy"
    assert meta2["hn"] == "ceara"
    assert meta2["day"] == "2026-07-13"


def test_history_key_lado_normalizes():
    k = history_key("7k", "2026-07-13", "a", "b", "Faltas", 20.5, "Mais", sofa_id=1)
    assert k.endswith("|over")
    k2 = history_key("7k", "2026-07-13", "a", "b", "Faltas", 20.5, "menos", sofa_id=1)
    assert k2.endswith("|under")


def test_history_key_lados_com_mando_nao_colidem():
    """As duas pernas do handicap de cartões TÊM que gerar chaves diferentes.

    ⚠️ Este teste existe porque o conserto foi REVERTIDO sem querer em 04/08
    (commit 2b8739cf) e NENHUM dos 169 testes acusou — a suíte inteira passou
    com o código quebrado. Sem LADOS_CANON, 'casa' e 'fora' caem ambos em
    'under': as duas pernas OPOSTAS do mesmo jogo colidem na mesma chave, o
    ingest sobrescreve uma com a outra no mesmo ciclo e a migração apaga a
    chave depois, em loop e sem log.
    """
    casa = history_key("bet365", "2026-08-04", "mirassol", "remo",
                       "Handicap de Cartões", 0.5, "casa")
    fora = history_key("bet365", "2026-08-04", "mirassol", "remo",
                       "Handicap de Cartões", 0.5, "fora")
    assert casa != fora, "pernas casa/fora colidiram na mesma chave"
    assert casa.endswith("|casa") and fora.endswith("|fora")
    # e o sinônimo em inglês tem que cair no mesmo lado canônico
    assert history_key("bet365", "d", "h", "a", "M", 0.5, "home").endswith("|casa")
    assert history_key("bet365", "d", "h", "a", "M", 0.5, "away").endswith("|fora")


def test_history_key_lado_desconhecido_vira_under():
    """Comportamento histórico preservado: lado fora da lista cai em 'under'.

    Guarda contra o conserto ser feito 'ao contrário' (ex.: passar o lado cru
    adiante), o que criaria chave nova pra cada grafia que a casa inventar.
    """
    assert history_key("x", "d", "h", "a", "M", 1.5, "banana").endswith("|under")
    assert history_key("x", "d", "h", "a", "M", 1.5, "").endswith("|under")


def test_gscore_order_swap():
    # confrontos com ordem trocada ainda casam
    s = gscore("ceara", "athletic club mg", "athletic club mg", "ceara")
    assert s >= 95


def test_match_to_sofa_pair():
    BRT = timezone(timedelta(hours=-3))
    day = "2026-07-13"
    start = datetime(2026, 7, 13, 20, 30, tzinfo=BRT)
    fixtures = [{
        "home": "Ceará", "away": "Athletic Club MG",
        "day_brt": day, "time_brt": "20:30",
        "start_ts": int(start.astimezone(timezone.utc).timestamp()),
        "sofa_id": 999, "league": "Brasileirão Série B",
        "_hn": norm_team("Ceará"), "_an": norm_team("Athletic Club MG"),
        "_lfp": "br-b",
    }]
    fx, sc, method, _info = match_to_sofa(
        norm_team("Ceará CE"), norm_team("Athletic Club"),
        day, start, fixtures, book_league="Série B",
    )
    assert fx is not None, f"expected match, got sc={sc} method={method}"
    assert fx["sofa_id"] == 999
    assert method in ("pair", "one_side", "slot_unique")


def test_match_to_sofa_one_side():
    """Contrato NOVO (brief 22/07 §6): 1 lado forte só casa com evidência do
    SEGUNDO lado. Segundo lado lixo → quarentena (sem match)."""
    BRT = timezone(timedelta(hours=-3))
    day = "2026-07-13"
    start = datetime(2026, 7, 13, 16, 0, tzinfo=BRT)
    fixtures = [{
        "home": "Londrina", "away": "Novorizontino",
        "day_brt": day, "time_brt": "16:00",
        "start_ts": int(start.astimezone(timezone.utc).timestamp()),
        "sofa_id": 777, "league": "Brasileirão Série B",
        "_hn": norm_team("Londrina"), "_an": norm_team("Novorizontino"),
        "_lfp": "br-b",
    }]
    # segundo lado plausível (variante de grafia) → casa
    fx, sc, method, _info = match_to_sofa(
        norm_team("Londrina PR"), norm_team("Grêmio Novorizontino"),
        day, start, fixtures, book_league="Brasileirão Série B",
    )
    assert fx is not None, f"one-side c/ 2º lado ok deveria casar, sc={sc} m={method}"
    assert fx["sofa_id"] == 777
    # segundo lado lixo → NÃO casa (antes casava — era a brecha do caso Sporting)
    fx2, sc2, m2, _i2 = match_to_sofa(
        norm_team("Londrina PR"), "time esquisito xyz",
        day, start, fixtures, book_league="Brasileirão Série B",
    )
    assert fx2 is None, f"2º lado lixo não pode casar (sc={sc2} m={m2})"


def test_resolve_fixture_unmatched():
    idt = resolve_fixture("Time Fantasma FC", "Outro Inventado", "2026-07-13T20:00:00-03:00",
                          fixtures=[])
    assert idt["sofa_id"] is None
    assert idt["match_method"] == "unmatched"
    assert idt["hn"]
    assert idt["an"]


def test_parse_unknown_key():
    meta = parse_history_key("broken")
    assert meta["format"] == "unknown"


def main():
    tests = [
        test_aliases_basic,
        test_history_key_sofa_and_legacy,
        test_history_key_lado_normalizes,
        test_history_key_lados_com_mando_nao_colidem,
        test_history_key_lado_desconhecido_vira_under,
        test_gscore_order_swap,
        test_match_to_sofa_pair,
        test_match_to_sofa_one_side,
        test_resolve_fixture_unmatched,
        test_parse_unknown_key,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1




# ---------------------------------------------------------------------------
# O mesmo jogo em linhas separadas porque as casas escrevem o clube de 3 jeitos.
#
# Caso real (06/08/2026): FK Jablonec x RFS as 13:00 saia como 3 jogos no board —
# "Rigas Futbola Skola" (Betano/Betfast/EstrelaBet), "Riga FS" (Superbet) e "RFS"
# (Sportingbet, a unica com sofa_id). O `ratio` do unify_gids e token_set_ratio CRU,
# entao os ALIASES nao o alcancavam: os pares davam 46, 18 e 40 contra o piso de 90.
# Consequencia: a Mesa nao comparava as casas entre si — que e a razao de ela existir —
# e 2 das 3 linhas ficavam sem previsao do modelo por nao terem o id da Sofa.
# ---------------------------------------------------------------------------
_KO_JABLONEC = 1754496000  # 2026-08-06 13:00 -03


def _jogos_jablonec():
    return {
        "betano":   {"day": "2026-08-06", "hn": "FK Jablonec",
                     "an": "Rigas Futbola Skola", "n": 3, "sofa": None, "kick_ts": _KO_JABLONEC},
        "superbet": {"day": "2026-08-06", "hn": "Jablonec",
                     "an": "Riga FS", "n": 1, "sofa": None, "kick_ts": _KO_JABLONEC},
        "sporting": {"day": "2026-08-06", "hn": "FK Jablonec",
                     "an": "RFS", "n": 1, "sofa": 16585831, "kick_ts": _KO_JABLONEC},
    }


def test_unify_tres_grafias_do_mesmo_clube():
    j = _jogos_jablonec()
    m = unify_gids(j)
    raizes = {m.get(g, g) for g in j}
    assert len(raizes) == 1, "as 3 linhas do mesmo jogo tem que virar uma so"


def test_unify_raiz_e_a_linha_com_sofa_id():
    """Senao o grupo unificado fica sem previsao do modelo."""
    m = unify_gids(_jogos_jablonec())
    assert m.get("betano") == "sporting"
    assert m.get("superbet") == "sporting"


def test_alias_rfs_nao_cola_riga_fc():
    """Riga FC NAO e o RFS — o alias nao pode fundir os dois."""
    assert norm_team("RFS") == "rfs"
    assert norm_team("Rigas Futbola Skola") == "rfs"
    assert norm_team("Riga FS") == "rfs"
    assert norm_team("Riga FC") != "rfs"


def test_unify_normalizado_nao_atropela_guarda_de_kickoff():
    """A normalizacao nao pode colar partidas distintas do mesmo time."""
    j = _jogos_jablonec()
    j["superbet"]["kick_ts"] = _KO_JABLONEC + 6 * 3600   # 6h depois: outra partida
    m = unify_gids(j)
    assert m.get("superbet", "superbet") != m.get("sporting", "sporting")


def test_flag_de_uma_letra_so_no_fim_do_nome():
    """'Yokohama F Marinos' NAO e time feminino — o F e parte do nome.

    Caso real (06/08/2026): o board publicava o mesmo jogo em 2 linhas porque o F
    solto virava marcador de feminino e caia no flags_mismatch contra o fixture da
    Sofa ('Yokohama F. Marinos', cujo ponto cola o F em 'fmarinos'). Single-letter
    de FLAG_TOKENS so conta como marcador quando e o ULTIMO token; sufixo real
    ('Corinthians F', 'Atletico B', 'Cabo Verde (F)') continua guardado.
    """
    from canonical import _flags, flags_compatible
    assert _flags(norm_team("Yokohama F Marinos")) == set()
    assert _flags(norm_team("Corinthians F")) == {"f"}
    assert _flags(norm_team("Cabo Verde (F)")) == {"f"}
    assert _flags(norm_team("Atletico B")) == {"b"}
    assert flags_compatible(norm_team("Yokohama F Marinos"), "x",
                            norm_team("Yokohama F. Marinos"), "x")


def test_weak_town_suffix_nao_sustenta_lado_forte():
    """Caso Telford (08/08/2026): 'Brackley TOWN' × 'Crawley TOWN' ganhava 92 de
    lado forte pelo sufixo compartilhado e casou National League North com a EFL
    Cup no MESMO kickoff — sofa_id envenenado, deploy travado pela pureza por 3h.
    'town' (e irmãos ingleses) agora são fracos, como 'city' já era."""
    from canonical import side_hit_strong
    assert side_hit_strong("brackley town", "crawley town") < 70
    for a, b in [("tranmere rovers", "blackburn rovers"),
                 ("wycombe wanderers", "bolton wanderers"),
                 ("derby county", "notts county")]:
        assert side_hit_strong(a, b) < 70, f"{a} × {b} nao podem casar"


def test_weak_town_controle_positivo():
    # o próprio clube segue casando forte pelo token distintivo
    from canonical import side_hit_strong
    assert side_hit_strong("crawley town", "crawley town fc") >= 90
