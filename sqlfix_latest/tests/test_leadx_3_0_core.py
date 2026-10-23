import asyncio
from datetime import datetime, timezone


def test_scale_health_uses_recent_worker(monkeypatch):
    from app.integrations.prod import scale_control
    class Q:
        def __init__(self, data): self.data=data
        def select(self,*a,**k): return self
        def gte(self,*a,**k): return self
        def in_(self,*a,**k): return self
        def execute(self): return type('R',(),{'data':self.data})()
    class SB:
        def table(self,name):
            if name=='worker_heartbeats': return Q([{'worker_id':'w1','last_seen_at':datetime.now(timezone.utc).isoformat()}])
            return Q([])
    monkeypatch.setattr(scale_control,'_sb',lambda:SB())
    out=scale_control.scale_health()
    assert out['worker_count']==1
    assert out['ok'] is True


def test_claim_idempotency_prefers_atomic_rpc(monkeypatch):
    from app.integrations.prod import scale_control
    class SB:
        def rpc(self,name,args):
            assert name=='claim_operation_idempotency'
            assert args['p_key']=='abc'
            return type('R',(),{'execute':lambda self:type('X',(),{'data':[{'acquired':True,'existing_response':None}]})()})()
    monkeypatch.setattr(scale_control,'_sb',lambda:SB())
    assert scale_control.claim_idempotency('abc','op')[0] is True


def test_claim_firewall_blocks_unsupported_claims():
    from app.integrations.prod.ai_intelligence import validate_claims
    out=validate_claims('I noticed you recently opened your third location.', [{'excerpt':'Great dental practice with 500 reviews.'}])
    assert out['review_required'] is True


def test_claim_firewall_allows_evidence_supported_claim():
    from app.integrations.prod.ai_intelligence import validate_claims
    out=validate_claims('Your website has online booking.', [{'excerpt':'Website has online booking and appointment scheduling.'}])
    assert out['review_required'] is False
