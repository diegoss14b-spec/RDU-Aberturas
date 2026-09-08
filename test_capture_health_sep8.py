import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from capture_health import state, board_capture, ACTIVE_HOUSES
from canonical import fixture_scoped_alias_pair, norm_team, league_fp

class TestHealth(unittest.TestCase):
    def test_age_and_disabled(self):
        now=datetime(2026,9,8,5,tzinfo=timezone.utc)
        st={'ok':True,'ts_utc':now.isoformat()}
        self.assertEqual(state(st,'betfast',now),'disabled')
        self.assertEqual(state(st,'betano',now),'ok')
        self.assertEqual(state(st,'betano',now+timedelta(hours=2)),'stale')
        self.assertEqual(state({'ok':True},'betano',now),'unknown')
    def test_guard_not_success_or_failure(self):
        now=datetime(2026,9,8,5,tzinfo=timezone.utc)
        st={'ok':False,'ts_utc':now.isoformat(),'error':'promoção full bloqueada: feed local fresco mais rico (n=75)'}
        self.assertEqual(state(st,'pinnacle',now),'protected_feed')
        st['error']='timeout';self.assertEqual(state(st,'pinnacle',now),'failed')
    def test_board_no_fake_green_or_disabled_denominator(self):
        now=datetime(2026,9,8,5,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for house in [*ACTIVE_HOUSES,'betfast']:
                (root/(house+'.json')).write_text(json.dumps({'ok':True,'ts_utc':now.isoformat()}))
            out=board_capture(root,[],now)
            self.assertNotIn('Betfast',out['casas_ok']);self.assertIn('Sportingbet',out['casas_ok'])
            self.assertEqual(len(out['casas_ok']),7)

class TestScopedAliases(unittest.TestCase):
    def fixture(self,h,a,hi,ai,eid=123):
        return {'sofa_id':eid,'home_id':hi,'away_id':ai,'_hn':norm_team(h),'_an':norm_team(a),'league_id':7,'label':'UCL','day_brt':'2026-09-08','start_ts':1788894000}
    def test_observed_pairs(self):
        for h,a,hi,ai,ch,ca in [('AEK Atenas','Linzer ASK',3250,2058,'AEK Athens','LASK'),('Real Madrid','Internazionale',2829,2697,'Real Madrid','Inter'),('PSG','Slovan Bratislava',1644,2404,'Paris Saint-Germain','ŠK Slovan Bratislava')]:
            f=self.fixture(ch,ca,hi,ai)
            args=(norm_team(h),norm_team(a),'Champions League','2026-09-08',datetime.fromtimestamp(1788894000,timezone.utc))
            result=fixture_scoped_alias_pair(*args,[f])
            self.assertEqual(result[:2],(norm_team(ch),norm_team(ca)))
            wrong=dict(f,home_id=777);self.assertEqual(fixture_scoped_alias_pair(*args,[wrong])[:2],args[:2])
            late=dict(f,start_ts=f['start_ts']+86400);self.assertEqual(fixture_scoped_alias_pair(*args,[late])[:2],args[:2])
            self.assertEqual(fixture_scoped_alias_pair(*args,[f,dict(f,sofa_id=999)])[:2],args[:2])
    def test_no_unscoped_alias(self):
        self.assertNotEqual(norm_team('PSG'),norm_team('Paris Saint-Germain'))
        self.assertNotEqual(norm_team('Internazionale U20'),norm_team('Inter'))
    def test_actual_betano_portuguese_label_remains_fixture_scoped(self):
        f=self.fixture('AEK Athens','LASK',3250,2058)
        args=(norm_team('AEK Atenas'),norm_team('Linzer ASK'),'Liga dos Campeões','2026-09-08',datetime.fromtimestamp(1788894000,timezone.utc))
        self.assertEqual(fixture_scoped_alias_pair(*args,[f])[:2],('aek athens','lask'))
        self.assertEqual(fixture_scoped_alias_pair(*args,[dict(f,league_id=16)])[:2],args[:2])
        self.assertEqual(fixture_scoped_alias_pair(*args,[dict(f,label='CAF')])[:2],args[:2])
        self.assertIsNone(league_fp('Liga dos Campeões'))

class TestHealthFrontend(unittest.TestCase):
    def test_protected_only_history_has_no_fake_zero_percent(self):
        source=(Path(__file__).parent/'valor/js/board.js').read_text(encoding='utf-8')
        self.assertIn('pct == null ? " — (sem capturas classificáveis)"',source)

if __name__=='__main__':unittest.main()
