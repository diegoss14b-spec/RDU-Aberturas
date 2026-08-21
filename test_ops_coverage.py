# -*- coding: utf-8 -*-
"""§11 — Operação: contagem por mercado com a MESMA definição do gate (sets jogo/casa/mercado,
sem dupla contagem, incluindo linhas de time). Casas da ops = casas ativas no run_capture (Betfast desligada 21/08).
"""
import unittest

from build_ops import CASAS, DISP, coverage_from_jogos
from gate_board import board_coverage as gate_coverage


def _board():
    # 3 jogos: totais + linhas de time + Betfast + Desarmes só por time (o caso que zerava)
    return {"jogos": [
        {"sofa_id": 1, "mercados": {
            "Escanteios": {"7k": [{"linha": 9.5, "over": 1.9, "under": 1.9}],
                            "Betano": [{"linha": 9.5, "over": 1.9, "under": 1.9}],
                            "Betfast": [{"linha": 10.5, "over": 1.95, "under": 1.85}]}}},
        {"sofa_id": 2, "mercados": {
            "Escanteios": {"7k": [{"linha": 10.5, "over": 1.9, "under": 1.9}]}},
         "times": {"Desarmes": {
            "home": {"nome": "A", "casas": {"7k": [{"linha": 3.5, "over": 1.9, "under": 1.9}]}},
            "away": {"nome": "B", "casas": {"Betfast": [{"linha": 3.5, "over": 1.9, "under": 1.9}]}}}}},
        {"sofa_id": 3, "times": {"Desarmes": {
            "home": {"nome": "C", "casas": {"7k": [{"linha": 4.5, "over": 1.9, "under": 1.9}],
                                             "Betano": [{"linha": 4.5, "over": 1.9, "under": 1.9}]}}}}},
    ]}


class OpsCoverageTests(unittest.TestCase):
    def setUp(self):
        self.board = _board()
        self.por_m, self.houses = coverage_from_jogos(self.board["jogos"])

    def test_escanteios_counts_total_and_team_games(self):
        esc = self.por_m["Escanteios"]
        self.assertEqual(esc["jogos"], 2)          # jogos 1 e 2
        self.assertEqual(esc["casas"]["7k"], 2)    # 7k em ambos
        self.assertEqual(esc["casas"]["Betano"], 1)
        self.assertEqual(esc["casas"]["Betfast"], 1)
        self.assertEqual(esc["multi_casa"], 1)     # só o jogo 1 tem ≥2 casas

    def test_desarmes_team_only_market_not_zero(self):
        # antes zerava (só contava total-de-partida); agora conta linhas de time
        des = self.por_m["Desarmes"]
        self.assertEqual(des["jogos"], 2)          # jogos 2 e 3
        self.assertEqual(des["casas"]["7k"], 2)
        # jogo 2 = 7k(home)+Betfast(away); jogo 3 = 7k+Betano → ambos multicasa
        self.assertEqual(des["multi_casa"], 2)

    def test_houses_match_gate_definition(self):
        # build_ops.houses tem que bater com o house_games do gate (mesma def de conjunto)
        gate = gate_coverage(self.board)
        self.assertEqual(self.houses.get("7k"), gate["houses"].get("7k"))
        self.assertEqual(self.houses.get("Betano"), gate["houses"].get("Betano"))
        self.assertEqual(self.houses.get("Betfast"), gate["houses"].get("Betfast"))

    def test_casas_da_ops_sao_capturadas(self):
        # 21/08: a Betfast foi DESLIGADA do pool (decisão do Diego via brief do Cursor) e
        # saiu de build_ops.CASAS — o teste antigo exigia a presença dela e derrubou o CI
        # (captura bloqueada por 2h). O invariante real é este: toda casa que a ops lista
        # nos denominadores tem que ser uma casa ATIVA no run_capture.FETCHERS — casa
        # listada e não capturada vira denominador fantasma. Religar a Betfast =
        # descomentar no run_capture E devolver aqui; o teste acompanha sozinho.
        from run_capture import FETCHERS
        ativas = {c for c, _, _ in FETCHERS}
        for c in CASAS:
            self.assertIn(c, ativas, f"{c} está na ops mas não é capturada")
            self.assertIn(c, DISP, f"{c} sem nome de exibição")
        self.assertNotIn("betfast", CASAS)
        # a fixture ainda carrega linhas 'Betfast' de propósito: casa desligada que
        # sobrou em snapshot antigo continua sendo CONTADA (não some do histórico).
        self.assertIn("Betfast", self.houses)


if __name__ == "__main__":
    unittest.main()
