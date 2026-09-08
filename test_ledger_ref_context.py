"""Forward stamp follows the board, without re-pricing any historical stamp."""
from datetime import datetime, timedelta
import json
from build_model_ledger import stamp_records, emit_ledger, BRT
from test_model_ledger import rec, NOW, FUTURO, fixidx

class ContextPricer:
    def __init__(self):self.calls=[]
    def price(self,comp,hid,aid,line,**context):
        self.calls.append(context)
        return {"mu_cal":4.7,"mu_raw":4.5,"p_over_win":.6,"p_push":0,
                "ref_applied":bool(context.get("fixture")),"referee":"Verified Ref",
                "ref_source":"fixture-feed","ref_observed_at":NOW.isoformat(),
                "settlement_rule":"R2","math_version":"cards-convolution-v1"}

def test_exact_fixture_book_and_observed_context_are_stamped_once(tmp_path):
    key="betano|sofa:555|Cartões|4.5|over"; row=rec(); pr=ContextPricer()
    stamp_records({key:row},fixidx(),{"cards":pr},"2026-07-28","hash",NOW)
    context=pr.calls[0]
    assert context["bookmaker"]=="betano" and context["now"]==NOW
    fx=context["fixture"]
    assert fx=={"eid":555,"comp":"BR-A","home_id":10,"away_id":20,"date":"2026-08-05",
                "kickoff_ts":int(datetime.fromisoformat(FUTURO).timestamp())}
    assert row["m_price_context"]["ref_applied"] is True
    previous=dict(row)
    stamp_records({key:row},fixidx(),{"cards":pr},"2026-07-30","different",NOW+timedelta(hours=1))
    assert len(pr.calls)==1 and row==previous
    row.update(status="settled",result=5,won=True)
    emit_ledger({key:row},fixidx(),{}, {"cards":"verified_fixture_override_else_neutral"},NOW,ledger_dir=tmp_path)
    output=json.loads(next(tmp_path.glob("*.jsonl")).read_text())
    assert output["ref_applied"] is True
    assert output["pricing_context"]["referee"]=="Verified Ref"
    assert output["model_settlement_rule"]=="R2"
    assert output["math_version"]=="cards-convolution-v1"

def test_late_stamp_cannot_import_current_referee():
    key="betano|sofa:555|Cartões|4.5|over"; row=rec(kickoff="2026-07-20T19:00:00-03:00")
    pr=ContextPricer()
    stamp_records({key:row},fixidx(),{"cards":pr},"2026-07-19","hash",NOW)
    assert pr.calls[0]["fixture"] is None
    assert row["m_price_context"]["ref_applied"] is False
    assert row["m_price_context"]["ref_reason"]=="late_stamp_referee_not_reconstructed"

def test_brt_date_identity_not_raw_utc_day():
    key="betano|sofa:555|Cartões|4.5|over"; row=rec(kickoff="2026-08-06T01:00:00+00:00")
    pr=ContextPricer()
    stamp_records({key:row},fixidx(),{"cards":pr},"2026-07-19","hash",NOW)
    assert pr.calls[0]["fixture"]["date"]=="2026-08-05"
