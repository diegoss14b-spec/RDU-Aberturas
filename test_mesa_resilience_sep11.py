"""Independent offline review: no network, collectors, or checkout writes."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

with patch.dict(os.environ, {'NETLIFY_TOKEN':'offline-review-placeholder'}):
    import deploy
    import run_capture as capture


class TransportTests(unittest.TestCase):
    def response(self, body=b'ok'):
        return contextlib.closing(io.BytesIO(body))

    def test_timeout_then_success(self):
        with patch.object(deploy.urllib.request,'urlopen',side_effect=[TimeoutError('private body'),self.response()]) as get, patch.object(deploy.time,'sleep'):
            self.assertEqual(deploy._fetch_text('https://example.test/public'),'ok')
            self.assertEqual(get.call_count,2)

    def test_transient_http_three_attempts(self):
        error=urllib.error.HTTPError('https://example.test/private',503,'private body',{},None)
        with patch.object(deploy.urllib.request,'urlopen',side_effect=error) as get,patch.object(deploy.time,'sleep'):
            with self.assertRaises(urllib.error.HTTPError):deploy._fetch_text('https://example.test/public')
            self.assertEqual(get.call_count,3)

    def test_auth_access_error_not_retried(self):
        for status in (401,403,404):
            error=urllib.error.HTTPError('https://example.test/private',status,'private body',{},None)
            with patch.object(deploy.urllib.request,'urlopen',side_effect=error) as get,patch.object(deploy.time,'sleep'):
                with self.assertRaises(urllib.error.HTTPError):deploy._fetch_text('https://example.test/public')
                self.assertEqual(get.call_count,1)

    def test_invalid_utf8_fails_closed(self):
        with patch.object(deploy.urllib.request,'urlopen',return_value=self.response(b'\xff')) as get:
            with self.assertRaises(UnicodeDecodeError):deploy._fetch_text('https://example.test/public')
            self.assertEqual(get.call_count,1)

    def test_diagnostic_does_not_leak_url_or_body(self):
        error=urllib.error.HTTPError('https://example.test/SECRET',429,'SECRET',{},None)
        self.assertEqual(deploy._read_error(error),'HTTP 429')
        self.assertEqual(deploy._read_error(TimeoutError('SECRET')),'TimeoutError')


class CoherentTests(unittest.TestCase):
    def manifest(self, identifier, history):
        return ('window.MANIFEST='+json.dumps({'manifest_version':1,'build_id':identifier,'generated_iso':dt.datetime.now(dt.timezone.utc).isoformat(),'artifacts':{'/data/history.js':{'sha256':deploy.sha256_bytes(history)}}})+';').encode()

    def test_stable_pair(self):
        hist=b'window.HIST={"a":1};';man=self.manifest('a',hist)
        with patch.object(deploy,'DEPLOY_LIVE_BASE','https://example.test'),patch.object(deploy,'_fetch_text',side_effect=[hist.decode(),man.decode()]) as get:
            out=deploy._coherent_live_history(man)
            self.assertEqual(out[1],man);self.assertEqual(out[3],hist);self.assertEqual(get.call_count,2)

    def test_generation_changes_then_stabilizes(self):
        h1=b'window.HIST={"a":1};';h2=b'window.HIST={"a":2};'
        m1=self.manifest('a',h1);m2=self.manifest('b',h2)
        with patch.object(deploy,'DEPLOY_LIVE_BASE','https://example.test'),patch.object(deploy,'_fetch_text',side_effect=[h2.decode(),m2.decode(),h2.decode(),m2.decode()]) as get,patch.object(deploy.time,'sleep'):
            out=deploy._coherent_live_history(m1)
            self.assertEqual(out[1],m2);self.assertEqual(out[3],h2);self.assertEqual(get.call_count,4)

    def test_stable_manifest_wrong_history_never_accepted(self):
        hist=b'window.HIST={"a":1};';man=self.manifest('a',hist)
        with patch.object(deploy,'DEPLOY_LIVE_BASE','https://example.test'),patch.object(deploy,'_fetch_text',side_effect=['window.HIST={};',man.decode()]*3) as get,patch.object(deploy.time,'sleep'):
            with self.assertRaises(ValueError):deploy._coherent_live_history(man)
            self.assertEqual(get.call_count,6)

    def test_unavailable_strict_baseline_blocks(self):
        target={'artifacts':{'/data/history.js':{'history_contract':{'policy':'strict'}}}}
        with patch.object(deploy,'DEPLOY_LIVE_BASE','https://example.test'),patch.object(deploy,'_fetch_text',side_effect=TimeoutError('SECRET')):
            result=deploy._shrink_reason(target)
            self.assertIn('baseline verificável',result);self.assertIn('TimeoutError',result);self.assertNotIn('SECRET',result)

    def shrink(self, before, after, previous_policy='strict-v1',target_policy='strict-v1'):
        hist=b'window.HIST={};'
        previous={'policy':previous_policy,'counts':before}
        target=None if target_policy is None else {'policy':target_policy,'counts':after}
        live={'artifacts':{'/data/history.js':{'sha256':deploy.sha256_bytes(hist),'history_contract':previous,'valid_count':100}}}
        raw=('window.MANIFEST='+json.dumps(live)+';').encode()
        man={'artifacts':{'/data/history.js':{'history_contract':target,'valid_count':100}}}
        with patch.object(deploy,'DEPLOY_LIVE_BASE','https://example.test'),patch.object(deploy,'_fetch_text',return_value=raw.decode()),patch.object(deploy,'_coherent_live_history',return_value=(live,raw,live['artifacts']['/data/history.js'],hist)),patch.object(deploy,'history_contract',return_value=previous),patch.object(deploy,'history_counts',return_value=before):
            return deploy._shrink_reason(man)

    def test_count_regression_still_blocked(self):
        before={'monitoradas':100,'liquidadas':50}
        for after in ({'monitoradas':99,'liquidadas':50},{'monitoradas':100,'liquidadas':49}):
            self.assertIn('count fell',self.shrink(before,after))

    def test_growth_same_policy_allowed(self):
        self.assertIsNone(self.shrink({'monitoradas':100,'liquidadas':50},{'monitoradas':120,'liquidadas':60}))

    def test_rollback_policy_still_blocked(self):
        counts={'monitoradas':100,'liquidadas':50}
        self.assertIn('cannot roll back',self.shrink(counts,counts,target_policy=None))

    def test_policy_change_still_blocked(self):
        counts={'monitoradas':100,'liquidadas':50}
        self.assertIn('unsupported CLV policy',self.shrink(counts,counts,target_policy='different-policy'))


class CaptureTests(unittest.TestCase):
    def status(self, **kwargs):
        status={'casa':'betano','ok':True,'ts_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'mode':'full','n_events':20,'n_markets':2,'pointer_valid':True,'attempted':True}
        status.update(kwargs);return status

    def test_fresh_valid_no_retry(self):
        status=self.status();self.assertTrue(capture.status_ok(status));self.assertFalse(capture.should_retry(status,'betano'))

    def test_protected_no_retry_but_not_capture_success(self):
        status=self.status(ok=False,error='promoção full bloqueada: feed local fresco mais rico (n=95)',error_class='Other')
        self.assertFalse(capture.status_ok(status));self.assertFalse(capture.should_retry(status,'pinnacle'))

    def test_transient_failure_retry(self):
        for cls in ('Timeout','HTTP429','Other'):
            self.assertTrue(capture.should_retry(self.status(ok=False,error_class=cls),'betano'))

    def test_policy_access_parse_not_retried(self):
        for cls in ('Auth','Geo','Parse'):
            self.assertFalse(capture.should_retry(self.status(ok=False,error_class=cls),'betano'))

    def test_stale_old_green_not_healthy(self):
        old=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=3)).isoformat()
        self.assertFalse(capture.status_ok(self.status(ts_utc=old)))

    def test_stride_preserves_real_capture_clock(self):
        status=self.status()
        with tempfile.TemporaryDirectory() as temp,patch.object(capture,'STATUS',Path(temp)):
            (Path(temp)/'betano.json').write_text(json.dumps(status))
            capture.record_skip('betano','full_stride')
            saved=capture.load_status('betano')
            self.assertEqual(saved['ts_utc'],status['ts_utc']);self.assertFalse(saved['attempted'])
            self.assertFalse(capture.should_retry(saved,'betano'))

    def test_invalid_pointer_green_must_not_suppress_retry(self):
        status=self.status(pointer_valid=False)
        self.assertFalse(capture.status_ok(status))
        self.assertTrue(capture.should_retry(status,'betano'))


if __name__=='__main__':unittest.main(verbosity=2)
