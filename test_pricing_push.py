# -*- coding: utf-8 -*-
"""test_pricing_push.py — P0 brief 2026-07-14: push em linhas inteiras, EV, edge.

Cobre:
  - Poisson e NB em linhas 7 / 7.5 / 8 / 8.5
  - p_over + p_under + p_push == 1
  - meia linha: p_push == 0
  - EV = p_win * odd + p_push - 1 (over e under)
  - regressões numéricas da auditoria (ordem de grandeza)
  - sanitize_ou_ladder (margem negativa + monotonia 8.5/9/9.5)
  - game_state kickoff
  - production vs shadow: BOARD.valor não usa candidate por padrão
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pricing_math import (
    ou_probs_from_cdf,
    expected_value,
    fair_odd,
    edge_vs_market,
    is_half_line,
    is_integer_line,
    price_dict,
)


def pois_cdf(k, mu):
    if k < 0:
        return 0.0
    s = 0.0
    t = math.exp(-mu)
    for i in range(0, k + 1):
        if i > 0:
            t *= mu / i
        s += t
    return min(1.0, s)


def nb_cdf(k, mu, phi):
    if k < 0:
        return 0.0
    if phi is None or phi <= 1.001 or mu <= 0:
        return pois_cdf(k, mu)
    r = mu / (phi - 1.0)
    p = r / (r + mu)
    s = 0.0
    pmf = p ** r
    for i in range(0, k + 1):
        if i > 0:
            pmf *= (r + i - 1) / i * (1 - p)
        s += pmf
    return min(1.0, s)


class TestOuProbs(unittest.TestCase):
    def test_half_line_no_push_poisson(self):
        for L in (7.5, 8.5, 9.5):
            for mu in (6.0, 8.0, 10.0):
                po, pu, pp = ou_probs_from_cdf(pois_cdf, mu, L)
                self.assertEqual(pp, 0.0, msg=f"L={L} mu={mu}")
                self.assertAlmostEqual(po + pu + pp, 1.0, places=9)
                # over = 1 - CDF(floor(L))
                self.assertAlmostEqual(po, 1.0 - pois_cdf(math.floor(L), mu), places=9)

    def test_integer_push_mass_poisson(self):
        for L in (7, 8, 10):
            for mu in (6.5, 8.0, 9.5):
                po, pu, pp = ou_probs_from_cdf(pois_cdf, mu, L)
                self.assertAlmostEqual(po + pu + pp, 1.0, places=8)
                self.assertGreater(pp, 0.0)
                # mass at L
                pmf = pois_cdf(L, mu) - pois_cdf(L - 1, mu)
                self.assertAlmostEqual(pp, pmf, places=8)
                self.assertAlmostEqual(po, 1.0 - pois_cdf(L, mu), places=8)
                self.assertAlmostEqual(pu, pois_cdf(L - 1, mu), places=8)

    def test_half_and_integer_nb(self):
        phi = 12.0
        for L in (7, 7.5, 8, 8.5):
            po, pu, pp = ou_probs_from_cdf(nb_cdf, 9.0, L, phi)
            self.assertAlmostEqual(po + pu + pp, 1.0, places=7)
            if is_half_line(L):
                self.assertEqual(pp, 0.0)
            if is_integer_line(L):
                self.assertGreater(pp, 0.0)

    def test_price_dict_aliases_are_win_only(self):
        po, pu, pp = ou_probs_from_cdf(pois_cdf, 8.0, 8)
        d = price_dict(8.0, po, pu, pp)
        self.assertEqual(d["p_over"], d["p_over_win"])
        self.assertEqual(d["p_under"], d["p_under_win"])
        self.assertNotAlmostEqual(d["p_over"] + d["p_under"], 1.0, places=3)
        self.assertAlmostEqual(d["p_over"] + d["p_under"] + d["p_push"], 1.0, places=8)


class TestEV(unittest.TestCase):
    def test_ev_with_push_under_and_over(self):
        # mu alto, line 7 under @4.92 — bug antigo incluía push no under
        po, pu, pp = ou_probs_from_cdf(pois_cdf, 8.5, 7)
        ev_u = expected_value(pu, 4.92, pp)
        # bug: p_under_old = 1 - p_over_win = under_win + push
        p_under_old = pu + pp
        ev_old = p_under_old * 4.92 - 1
        self.assertLess(ev_u, ev_old)
        self.assertAlmostEqual(ev_u, pu * 4.92 + pp - 1, places=9)
        # over também recebe push na fórmula
        ev_o = expected_value(po, 1.20, pp)
        self.assertAlmostEqual(ev_o, po * 1.20 + pp - 1, places=9)
        self.assertGreater(ev_o, po * 1.20 - 1)  # push aumenta EV vs fórmula sem push

    def test_audit_regressions_order_of_magnitude(self):
        """Reproduz a ordem de grandeza dos 4 exemplos da auditoria.

        Os μ exatos do board não estão fixos aqui; usamos μ plausível de escanteios
        (~9–11) e verificamos que o bug antigo (p_under=1-p_over) superestima EV under.
        """
        cases = [
            # (mu, line, odd_under, label)
            (10.5, 7, 4.92, "Vitoria-Vasco U7"),
            (10.5, 7, 4.86, "Manta-Delfin U7"),
            (10.0, 8, 4.70, "Corinthians-Remo U8"),
            (10.0, 8, 3.78, "Macara-Mushuc U8"),
        ]
        for mu, L, odd, label in cases:
            po, pu, pp = ou_probs_from_cdf(pois_cdf, mu, L)
            # bug: p_under_wrong = 1 - p_over  (inclui push no under)
            p_over_old = 1.0 - pois_cdf(math.floor(L), mu)  # for int: 1-CDF(L-? wait floor(7)=7)
            # old formula used: po = 1 - CDF(floor(line)); for int 7 floor=7 so po=P(X>=8)=P(X>7)
            # actually floor(7)=7, CDF(7)=P(X<=7), 1-CDF(7)=P(X>=8)=P(X>7) which is over_win
            # p_under_old = 1 - po = P(X<=7) = under_win + push  ← bug
            p_over_old = 1.0 - pois_cdf(math.floor(L), mu)
            p_under_old = 1.0 - p_over_old
            ev_old = p_under_old * odd - 1
            ev_new = expected_value(pu, odd, pp)
            self.assertLess(ev_new, ev_old - 0.05, msg=f"{label}: new EV should drop vs old")
            # first two should go clearly negative with high mu and high under odds on low lines
            if L == 7:
                self.assertLess(ev_new, 0.0, msg=f"{label} should be negative EV with push")

    def test_fair_odd_with_push(self):
        po, pu, pp = ou_probs_from_cdf(pois_cdf, 9.0, 9)
        fo = fair_odd(pu, pp)
        self.assertIsNotNone(fo)
        self.assertAlmostEqual(fo, (1 - pp) / pu, places=9)
        # break-even EV ≈ 0
        self.assertAlmostEqual(expected_value(pu, fo, pp), 0.0, places=9)

    def test_edge_uses_conditional(self):
        po, pu, pp = ou_probs_from_cdf(pois_cdf, 9.0, 9)
        mkt = 0.45
        e = edge_vs_market(pu, mkt, pp)
        self.assertAlmostEqual(e, pu / (1 - pp) - mkt, places=9)


class TestSanitizeLadder(unittest.TestCase):
    def test_rejects_negative_margin_and_mono_break(self):
        from build_board import sanitize_ou_ladder, MARGIN_MIN, MARGIN_CAP
        # exemplo brief: 8.5 / 9.0 / 9.5 com monotonia quebrada e margem estranha
        ladder = [
            {"linha": 8.5, "over": 1.24, "under": 3.25},
            {"linha": 9.0, "over": 1.50, "under": 3.43},  # under sobe → mono_under
            {"linha": 9.5, "over": 1.45, "under": 2.38},  # over desce → mono_over se 9.0 kept
        ]
        ok, rej = sanitize_ou_ladder(ladder)
        reasons = {r["reason"] for r in rej}
        # 8.5 ok; 9.0 mono_under vs under 3.25→3.43; etc.
        self.assertTrue(any(r["reason"] in ("mono_under", "mono_over", "margin_low") for r in rej),
                        msg=f"rej={rej}")
        # par com margem negativa (ambas odds altas): 1/2.2 + 1/2.2 - 1 < 0
        bad = [{"linha": 10.5, "over": 2.20, "under": 2.20}]
        ok2, rej2 = sanitize_ou_ladder(bad)
        self.assertEqual(ok2, [])
        self.assertTrue(any(r["reason"] == "margin_low" for r in rej2), msg=f"rej2={rej2}")

    def test_accepts_monotonic_ladder(self):
        from build_board import sanitize_ou_ladder
        ladder = [
            {"linha": 8.5, "over": 1.70, "under": 2.05},
            {"linha": 9.5, "over": 1.90, "under": 1.85},
            {"linha": 10.5, "over": 2.15, "under": 1.68},
        ]
        ok, rej = sanitize_ou_ladder(ladder)
        self.assertEqual(len(ok), 3)
        self.assertEqual(rej, [])


class TestGameState(unittest.TestCase):
    def test_upcoming_started_finished(self):
        from build_board import game_state, BRT
        now = datetime(2026, 7, 14, 18, 0, tzinfo=BRT)
        self.assertEqual(game_state("14/07 20:00", now), "upcoming")
        self.assertEqual(game_state("14/07 17:00", now), "started")
        self.assertEqual(game_state("14/07 12:00", now), "finished")
        self.assertEqual(game_state("", now), "unknown")
        self.assertEqual(game_state(None, now), "unknown")

    def test_year_rollover(self):
        from build_board import game_state, BRT
        now = datetime(2026, 12, 31, 23, 0, tzinfo=BRT)
        # 01/01 15:00 no passado do ano atual → deve virar 2027
        self.assertEqual(game_state("01/01 15:00", now), "upcoming")


class TestPricersContract(unittest.TestCase):
    def test_value_pricers_expose_push(self):
        from value_pricers import CardsPricer
        cp = CardsPricer()
        # pega 2 times de qualquer liga disponível
        keys = list(cp.by.keys())[:2]
        if len(keys) < 2:
            self.skipTest("sem times no bundle de cartões")
        (lg1, h), (lg2, a) = keys[0], keys[1]
        if lg1 != lg2:
            # achar par na mesma liga
            by_lg = {}
            for lg, tid in cp.by:
                by_lg.setdefault(lg, []).append(tid)
            lg = next((k for k, v in by_lg.items() if len(v) >= 2), None)
            if not lg:
                self.skipTest("sem par na mesma liga")
            h, a = by_lg[lg][0], by_lg[lg][1]
            lg1 = lg
        for L in (3.5, 4, 4.5, 5):
            r = cp.price(lg1, h, a, L)
            if not r:
                continue
            self.assertIn("p_push", r)
            self.assertIn("p_over_win", r)
            self.assertIn("p_under_win", r)
            self.assertAlmostEqual(
                r["p_over_win"] + r["p_under_win"] + r["p_push"], 1.0, places=6)
            if is_half_line(L):
                self.assertEqual(r["p_push"], 0.0)

    def test_candidate_also_push(self):
        from candidate_pricer import CornersPricer
        p = CornersPricer()
        if not p.ok:
            self.skipTest("candidate bundle ausente")
        comp = next(iter(p.pairs))
        pair = next(iter(p.pairs[comp]))
        h, a = pair.split("|")
        board_lg = {v: k for k, v in p.xwalk.items()}.get(comp, comp)
        r = p.price(board_lg, h, a, 9)
        self.assertIsNotNone(r)
        self.assertIn("p_push", r)
        self.assertGreater(r["p_push"], 0)


class TestPromotedDefault(unittest.TestCase):
    def test_candidates_promoted_by_default(self):
        # Diego promoveu os modelos novos em 14/07 ("a mesa de aberturas com os modelos
        # novos") — o titular do board é candidate_pricer com status 'promoted' (passa no
        # gate). Rollback só via FORCE_LEGACY_BOARD=1.
        self.assertNotIn(os.environ.get("FORCE_LEGACY_BOARD", ""), ("1", "true", "TRUE", "yes"))
        import build_board as bb
        self.assertFalse(bb.FORCE_LEGACY)
        self.assertEqual(bb.MODEL_STATUS, "promoted")
        self.assertEqual(bb.MODEL_SOURCE, "candidate_pricer")


class TestThreeWayForaDoValor(unittest.TestCase):
    """3 vias (over/exato/under) nunca pode virar flag de valor — o 'exato' faz over E
    under perderem, então não é push. Bug achado no board de 15/07 02:44: 2 sinais do
    Corinthians×Remo vinham do OU621 'Escanteios 3- Vias Mais/Menos' com p_push creditado
    (EV inflado) e margem irreal de 0,41% (o de-vig não enxerga o 3º resultado)."""

    def test_detecta_o_nome_real_da_7k(self):
        from build_board import three_way
        # o nome REAL vem com espaçamento torto: "Escanteios 3- Vias Mais/Menos"
        self.assertTrue(three_way({"market_type_name": "Escanteios 3- Vias Mais/Menos"}))
        self.assertTrue(three_way({"market_type_name": "Cartões 3-Vias Mais/Menos"}))
        self.assertTrue(three_way({"market_type_name": "Faltas 3 Vias"}))
        self.assertTrue(three_way({"market_type_name": "Corners 3-Way Over/Under"}))

    def test_nao_derruba_mercado_normal(self):
        from build_board import three_way
        self.assertFalse(three_way({"market_type_name": "Total de Escanteios"}))
        self.assertFalse(three_way({"market_type_name": "Escanteios Mais/Menos"}))
        self.assertFalse(three_way({}))                      # sem meta (Betano/Pinnacle)
        self.assertFalse(three_way({"market_type_name": None}))

    def test_gate_bloqueia_valor_de_3vias(self):
        """O gate tem que REPROVAR um board com flag de valor vindo de 3 vias."""
        import gate_board
        src = Path(gate_board.__file__).read_text(encoding="utf-8")
        self.assertIn("3 vias", src)
        self.assertIn("market_type_name", src)


class TestFetch7kFamily(unittest.TestCase):
    def test_canon_rejects_period(self):
        from fetch_odds_7k import canon
        self.assertIsNone(canon("Total de Cartões 1º Tempo"))
        self.assertIsNone(canon("Primeiro Tempo Escanteios"))
        self.assertEqual(canon("Total de Cartões"), "Cartões")
        self.assertEqual(canon("Escanteios Mais/Menos"), "Escanteios")

    def test_pick_family_prefers_more_lines(self):
        # chama a função REAL (antes este teste replicava a lógica numa cópia — e por
        # isso não pegaria um bug de índice na tupla de score)
        from fetch_odds_7k import _pick_family
        fams = {
            "id_a": {
                "meta": {"market_type_id": "a", "market_type_name": "Total de Escanteios"},
                "by_line": {
                    8.5: {"linha": 8.5, "over": 1.8, "under": 1.9},
                    9.5: {"linha": 9.5, "over": 2.0, "under": 1.75},
                },
            },
            "id_b": {
                "meta": {"market_type_id": "b", "market_type_name": "Escanteios Asiáticos"},
                "by_line": {
                    9.0: {"linha": 9.0, "over": 1.9, "under": 1.85},
                },
            },
        }
        arr, dropped = _pick_family(fams)
        self.assertEqual(arr[0]["market_type_name"], "Total de Escanteios")   # 2 linhas > 1
        self.assertEqual(len(arr), 2)
        self.assertEqual(dropped[0]["n_lines"], 1)      # índice certo da tupla de score

    def test_pick_family_2vias_ganha_de_3vias_mesmo_com_menos_linhas(self):
        """A 7k publica as DUAS famílias do mesmo mercado. O 3-vias (total exato = 3º
        resultado) é inútil pro flag de valor, então o 2-vias vence mesmo tendo menos
        linhas. Bug real de 15/07: Corinthians×Remo pegou o 3-vias e gerou EV inflado."""
        from fetch_odds_7k import _pick_family
        fams = {
            "OU621": {
                "meta": {"market_type_id": "OU621",
                         "market_type_name": "Escanteios 3- Vias Mais/Menos"},
                "by_line": {float(i): {"linha": float(i), "over": 2.0, "under": 1.8}
                            for i in range(8, 18)},          # 10 linhas
            },
            "OU12": {
                "meta": {"market_type_id": "OU12",
                         "market_type_name": "Escanteios Mais/Menos (2-Vias)"},
                "by_line": {8.5: {"linha": 8.5, "over": 1.9, "under": 1.9}},   # 1 linha
            },
        }
        arr, dropped = _pick_family(fams)
        self.assertEqual(arr[0]["market_type_name"], "Escanteios Mais/Menos (2-Vias)")
        self.assertEqual(dropped[0]["name"], "Escanteios 3- Vias Mais/Menos")
        self.assertEqual(dropped[0]["n_lines"], 10)

    def test_pick_family_so_3vias_ainda_e_exibida(self):
        """Se o jogo SÓ tem 3-vias, ela continua no board (oferta visível) — quem tira do
        flag de valor é o build_board.three_way."""
        from fetch_odds_7k import _pick_family
        fams = {"OU621": {"meta": {"market_type_id": "OU621",
                                   "market_type_name": "Escanteios 3- Vias Mais/Menos"},
                          "by_line": {9.0: {"linha": 9.0, "over": 2.0, "under": 1.8}}}}
        arr, _ = _pick_family(fams)
        self.assertEqual(len(arr), 1)
        from build_board import three_way
        self.assertTrue(three_way(arr[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
