"""Offline adversarial tests for frozen R1/R2 and exact-event referee selection."""
import copy
import importlib.util
import json
import math
import os
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import candidate_pricer as cp
from pricing_math import cards_pmf,ou_probs_from_pmf,ou_probs_from_cdf,expected_value,fair_odd

ROOT=Path(__file__).resolve().parent

class CardsContractTests(unittest.TestCase):
    def test_pmf_mass_moments_and_direct_red_variance(self):
        for phi in (None,8,35):
            mu,ld,ls=4.0,.18,.09
            p=cards_pmf(mu,ld,ls,phi,100)
            self.assertAlmostEqual(sum(p),1,places=13)
            mean=sum(i*v for i,v in enumerate(p));var=sum((i-mean)**2*v for i,v in enumerate(p))
            self.assertAlmostEqual(mean,mu+2*ld+ls,places=8)
            self.assertAlmostEqual(var,mu+(mu*mu/phi if phi else 0)+4*ld+ls,places=7)
            self.assertTrue(all(v>=0 for v in p))

    def test_r1_poisson_identity_push_and_quarter_rejection(self):
        p=cards_pmf(4,.0,.2,None,70)
        for line in (0,1.5,3,4.5,6,9.5):
            got=ou_probs_from_pmf(p,line)
            want=ou_probs_from_cdf(cp._pois_cdf,4.2,line)
            for a,b in zip(got,want):self.assertAlmostEqual(a,b,delta=1e-10)
            self.assertAlmostEqual(sum(got),1,places=12)
        for line in (3.25,4.75,float('nan')):
            with self.assertRaises(ValueError):ou_probs_from_pmf(p,line)
        po,pu,pp=ou_probs_from_pmf(p,4)
        self.assertGreater(pp,0);self.assertAlmostEqual(expected_value(po,fair_odd(po,pp),pp),0,places=12)

    def test_all_r1_lookup_neutral_lines_and_r2_mean(self):
        base=os.environ.get('RDU_LOOKUP_ROOT')
        if not base:self.skipTest('Set RDU_LOOKUP_ROOT for exhaustive local lookup parity; portable math tests remain active')
        source=Path(base)/cp._B['version']
        pr=cp.CardsPricer();worst=0;n=0
        for comp,pairs in pr.pairs.items():
            lookup=json.loads((source/'cards'/(comp+'.json')).read_text())
            for pair in pairs:
                h,a=pair.split('|');entry=lookup['predictions'].get(f'id:{h}|id:{a}|neutral')
                if not entry:continue
                for line,p in entry['p'].items():
                    result=pr.price(comp,h,a,float(line),bookmaker='betfair')
                    worst=max(worst,abs(result['p_over']-p));n+=1
                    self.assertAlmostEqual(result['mu'],entry['mu'],places=6)
                r2=pr.price(comp,h,a,4.5,bookmaker='betano')
                mu_y=max(.1,pr.a+pr.b*float(entry['mu_model']))
                self.assertAlmostEqual(r2['mu'],pr._mu_total(comp,mu_y),places=6)
        self.assertGreater(n,40000);self.assertLessEqual(worst,5.01e-5)
        print({'r1_lookup_cells':n,'max_probability_delta_rounding':worst})

    def test_verified_override_and_negative_fingerprints(self):
        pr=cp.CardsPricer();record=next(v for v in cp._B['fixture_ref_overrides'].values() if 'cards' in v['markets'])
        now=datetime.fromisoformat(record['observed_at']).replace(tzinfo=timezone(timedelta(hours=-3)))+timedelta(hours=1)
        fx={k:record[k] for k in ('eid','comp','home_id','away_id','date','kickoff_ts')}
        def price(f):return pr.price(record['comp'],record['home_id'],record['away_id'],4.5,fixture=f,bookmaker='betano',now=now)
        correct=price(fx);self.assertTrue(correct['ref_applied'])
        self.assertEqual(correct['mu_raw'],record['markets']['cards']['mu_model'])
        for key,value in [('eid',-1),('comp','WRONG'),('home_id',999),('away_id',999),('date','2000-01-01'),('kickoff_ts',1)]:
            wrong={**fx,key:value};self.assertFalse(price(wrong)['ref_applied'],key)
        stale=pr.price(record['comp'],record['home_id'],record['away_id'],4.5,fixture=fx,bookmaker='betano',now=now+timedelta(days=4))
        self.assertFalse(stale['ref_applied']);self.assertEqual(stale['ref_reason'],'ref_override_stale')
        with patch.dict(record,{'version':'bad'}):self.assertFalse(price(fx)['ref_applied'])

    def test_contract_unknown_quarter_and_incomplete_bundle(self):
        pr=cp.CardsPricer()
        self.assertIsNone(pr.price('BR-B',49202,342775,4.25,bookmaker='betano'))
        self.assertIsNone(pr.price('BR-B',49202,342775,4.5,bookmaker='unknown-new-book'))
        fake=copy.deepcopy(cp._B);fake['markets']['cards'].pop('card_contracts')
        with patch.object(cp,'_B',fake):self.assertFalse(cp.CardsPricer().ok)

if __name__=='__main__':unittest.main(verbosity=2)
