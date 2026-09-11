"""Sportingbet parser regressions: portable, offline, no collector main.

Import the active repository parser via ordinary Python imports. During local
review run with the repository on PYTHONPATH; when copied into its root this file
runs unchanged in CI. Payloads below are compact synthetic parser fixtures, not
claims that these prices are currently offered by the bookmaker.
"""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

# The collector creates output directories at import. Suppress only that import
# side effect and opt-in raw capture; do not create anything in the checkout.
with patch.dict(os.environ, {'ODDS_RAW':'0'}), patch.object(Path,'mkdir'), \
     patch('curl_cffi.requests.get',side_effect=AssertionError('offline test attempted HTTP')):
    import fetch_odds_sportingbet as sporting

from bookmaker_contracts import event_participants


def option(name, price=1.9, types=None):
    return {'name':{'value':name},'price':{'odds':price},
            'parameters':{'optionTypes':types or []}}


def market(name, options):
    return {'name':{'value':name},'options':options}


class SportingNameContractTests(unittest.TestCase):
    def test_shots_on_target_match_aliases(self):
        for name in ('Total de Chutes no gol','Total de chutes a gol',
                     'TOTAL CHUTES NO GOL','  Total de Chutes no gol  '):
            with self.subTest(name=name):
                self.assertEqual(sporting.canon_match(name),'Chutes no gol')

    def test_shots_on_target_team_participant(self):
        participants=('Chelsea','Liverpool')
        self.assertEqual(sporting.canon_team('Chelsea - Total de Chutes no gol',participants),
                         ('Chutes no gol','Chelsea'))
        self.assertEqual(sporting.canon_team('Liverpool - Total de Chutes a gol',participants),
                         ('Chutes no gol','Liverpool'))
        self.assertIsNone(sporting.canon_match('Chelsea - Total de Chutes no gol'))

    def test_total_shots_remain_distinct_from_shots_on_target(self):
        self.assertEqual(sporting.canon_match('Total de Chutes'),'Finalizações')
        self.assertEqual(sporting.canon_match('Total de Finalizações'),'Finalizações')
        self.assertEqual(sporting.canon_match('Total de Chutes no gol'),'Chutes no gol')

    def test_queens_park_rangers_not_rejected_by_par_substring(self):
        # Event identity observed in the 11/09 normalized snapshot, id 2:7856564.
        participants=event_participants('West Bromwich - Queens Park Rangers')
        self.assertEqual(participants,('West Bromwich','Queens Park Rangers'))
        for stat,expected in [('Cartões','Cartões'),('Chutes no gol','Chutes no gol'),
                              ('Escanteios','Escanteios')]:
            with self.subTest(stat=stat):
                self.assertEqual(sporting.canon_team('Queens Park Rangers - Total de '+stat,participants),
                                 (expected,'Queens Park Rangers'))

    def test_paris_and_case_accents_do_not_trigger_global_exclusions(self):
        self.assertEqual(sporting.canon_team('Paris Saint-Germain - Total de Cartões',
                                             ('Paris Saint-Germain','São Paulo')),('Cartões','Paris Saint-Germain'))
        self.assertEqual(sporting.canon_team('SAO PAULO - Total de Chutes no gol',
                                             ('Paris Saint-Germain','São Paulo')),('Chutes no gol','SAO PAULO'))

    def test_player_rejected_when_real_participants_supplied(self):
        for name in ('Cole Palmer - Total de Chutes','Cole Palmer - Total de Chutes no gol',
                     'Mohamed Salah - Total de Cartões'):
            with self.subTest(name=name):
                self.assertIsNone(sporting.canon_team(name,('Chelsea','Liverpool')))

    def test_wrong_age_group_or_gender_is_not_same_participant(self):
        for team in ('Chelsea U21','Chelsea (F)','Liverpool U20'):
            self.assertIsNone(sporting.canon_team(team+' - Total de Chutes no gol',('Chelsea','Liverpool')))

    def test_missing_or_ambiguous_participants_fail_closed(self):
        for participants in ((),('Chelsea','Chelsea'),('Other','Liverpool')):
            with self.subTest(participants=participants):
                self.assertIsNone(sporting.canon_team('Chelsea - Total de Chutes no gol',participants))
        self.assertEqual(event_participants('Chelsea'),())
        self.assertEqual(event_participants('Chelsea - Liverpool - Arsenal'),())

    def test_periods_handicap_goals_exact_and_threeway_rejected(self):
        rejected=('Total de Gols','Total de Gol','Total de Gols Exatos',
                  'Total de Chutes no gol - 1º Tempo','1º Tempo - Total de Chutes no gol',
                  'Total de Cartões 2º Tempo','Total de Chutes no gol Handicap',
                  'Total de Chutes no gol Exatos','Total de Cartões Exatamente',
                  'Total de Cartões Ímpar/Par','Total de Cartões 3 Vias',
                  'Total de Desarmes do Jogador')
        for name in rejected:
            with self.subTest(name=name):
                self.assertIsNone(sporting.canon_match(name))
                self.assertIsNone(sporting.canon_team('Chelsea - '+name,('Chelsea','Liverpool')))

    def test_empty_names_are_rejected(self):
        for name in (None,'','   '):
            self.assertIsNone(sporting.canon_match(name))
            self.assertIsNone(sporting.canon_team(name,('Chelsea','Liverpool')))


class SportingQuoteContractTests(unittest.TestCase):
    def test_real_option_shape_yields_portuguese_decimal_pair(self):
        source=market('Total de Chutes no gol',[option('Mais de 8,5',1.87),option('Menos de 8,5',1.91)])
        before=copy.deepcopy(source)
        self.assertEqual(sporting.ou_lines(source),{8.5:{'over':1.87,'under':1.91}})
        self.assertEqual(source,before)

    def test_provider_side_metadata_is_supported(self):
        source=market('Total de Chutes no gol',[option('8.5',1.87,['Over']),option('8.5',1.91,['Under'])])
        self.assertEqual(sporting.ou_lines(source),{8.5:{'over':1.87,'under':1.91}})

    def test_exactly_outcome_never_becomes_under(self):
        source=market('Total de Chutes no gol Exatos',[option('Exatamente 8',2.3,['Exact']),option('8',3.0,['Draw'])])
        self.assertEqual(sporting.ou_lines(source),{})
        self.assertIsNone(sporting.canon_match(source['name']['value']))

    def test_invalid_prices_and_missing_line_do_not_invent_quotes(self):
        source=market('Total de Chutes no gol',[option('Mais de 8.5',1),option('Menos de 8.5',0),
                                               option('Mais de',1.9),option('Menos de',1.9)])
        self.assertEqual(sporting.ou_lines(source),{})

    def test_partial_source_market_stays_partial_not_paired_with_another(self):
        over=market('Total de Chutes no gol',[option('Mais de 8.5',1.9)])
        under=market('Total de Chutes no gol',[option('Menos de 8.5',1.8)])
        self.assertEqual(sporting.ou_lines(over),{8.5:{'over':1.9}})
        self.assertEqual(sporting.ou_lines(under),{8.5:{'under':1.8}})

    def test_recursive_market_discovery_preserves_objects(self):
        one=market('Total de Chutes no gol',[option('Mais de 8.5'),option('Menos de 8.5')])
        two=market('Queens Park Rangers - Total de Chutes no gol',[option('Mais de 3.5'),option('Menos de 3.5')])
        payload={'fixtures':[{'optionMarkets':[one],'nested':{'markets':[two]}}]}
        before=copy.deepcopy(payload)
        found=list(sporting.iter_markets(payload))
        self.assertEqual(found,[one,two]);self.assertIs(found[0],one)
        self.assertEqual(payload,before)


if __name__=='__main__':unittest.main(verbosity=2)
