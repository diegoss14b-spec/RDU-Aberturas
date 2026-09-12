"""Incomplete full scans are explicit, not a second whole-catalog request storm."""
import unittest
import capture_common
import run_capture

class CaptureIncomplete(RuntimeError):
    pass

class SuperbetRetryTest(unittest.TestCase):
    def test_incomplete_is_preserved_and_not_retried(self):
        error=CaptureIncomplete('captura incompleta Superbet: 2 falhas, 3 não consultados')
        kind=capture_common.classify_error(error)
        self.assertEqual('CaptureIncomplete',kind)
        status={'ok':False,'attempted':True,'error_class':kind,'error':str(error)}
        self.assertFalse(run_capture.should_retry(status,'superbet'))
        self.assertFalse(run_capture.status_ok(status))

    def test_no_change_to_other_collectors_or_existing_budget_policy(self):
        status={'ok':False,'attempted':True,'error_class':'CaptureIncomplete','error':'incompleta'}
        self.assertTrue(run_capture.should_retry(status,'7k'))
        status['error_class']='CaptureBudgetExceeded'
        self.assertFalse(run_capture.should_retry(status,'superbet'))
        self.assertEqual(900,next(t for c,_,t in run_capture.FETCHERS if c=='superbet'))

if __name__=='__main__':unittest.main()
