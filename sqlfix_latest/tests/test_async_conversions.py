"""Behavioural tests for code paths rewritten from sequential/blocking to concurrent."""
import asyncio
import sys
import types

# Only stub third-party modules that are genuinely absent (CI has the real ones).
try:
    import httpx  # noqa: F401
except ImportError:  # pragma: no cover
    _h = types.ModuleType("httpx")
    _h.AsyncClient = object
    sys.modules["httpx"] = _h

from app.integrations.prod import webhooks  # noqa: E402


class _Res:
    def __init__(self, data=None): self.data = data


class FakeSB:
    """Records queries; serves rows for the two tables deliver_due reads.
    table() returns a fresh builder per call, like the real client."""

    def __init__(self, deliveries, hooks):
        self.deliveries, self.hooks = deliveries, hooks
        self.updates, self.hook_queries = [], 0

    def table(self, t): return _Q(self, t)


class _Q:
    def __init__(self, root, t): self.root, self.t, self.op, self.payload, self.eqs = root, t, "select", None, {}
    def select(self, *_a): return self
    def update(self, payload): self.op, self.payload = "update", payload; return self
    def eq(self, k, v): self.eqs[k] = v; return self
    def in_(self, *_a): return self
    def lte(self, *_a): return self
    def order(self, *_a): return self
    def limit(self, *_a): return self

    def execute(self):
        r = self.root
        if self.op == "update":
            r.updates.append((self.eqs.get("id"), dict(self.payload)))
            return _Res([])
        if self.t == "webhook_deliveries": return _Res(r.deliveries)
        if self.t == "outbound_webhooks":
            r.hook_queries += 1
            return _Res(r.hooks)
        return _Res([])


class FakeClient:
    log, inflight, peak = [], 0, 0
    status = {}

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def post(self, url, content=None, headers=None):
        FakeClient.inflight += 1
        FakeClient.peak = max(FakeClient.peak, FakeClient.inflight)
        FakeClient.log.append(("start", url, headers["x-leadx-event"]))
        await asyncio.sleep(0.03)
        FakeClient.inflight -= 1
        FakeClient.log.append(("end", url, headers["x-leadx-event"]))
        return types.SimpleNamespace(status_code=FakeClient.status.get(url, 200), text="ok")


def _run(deliveries, hooks, status=None):
    fake = FakeSB(deliveries, hooks)
    FakeClient.log, FakeClient.inflight, FakeClient.peak, FakeClient.status = [], 0, 0, status or {}
    orig = (webhooks._sb, webhooks.httpx.AsyncClient, webhooks.assert_public_url)

    async def _public(_u): return None

    webhooks._sb, webhooks.httpx.AsyncClient, webhooks.assert_public_url = (lambda: fake), FakeClient, _public
    try:
        return fake, asyncio.run(webhooks.deliver_due())
    finally:
        webhooks._sb, webhooks.httpx.AsyncClient, webhooks.assert_public_url = orig


def _d(i, wid, ev): return {"id": i, "webhook_id": wid, "event_type": ev, "payload": {}, "attempts": 0}


def test_webhooks_endpoints_concurrent_same_endpoint_ordered_one_hook_query():
    hooks = [{"id": w, "active": True, "endpoint_url": f"https://{w}.example/h", "retry_limit": 6} for w in ("a", "b", "c")]
    rows = [_d(1, "a", "e1"), _d(2, "a", "e2"), _d(3, "b", "e1"), _d(4, "c", "e1"), _d(5, "a", "e3")]
    fake, out = _run(rows, hooks)
    assert out == {"processed": 5, "sent": 5, "failed": 0}
    assert fake.hook_queries == 1                       # was one query per delivery
    assert FakeClient.peak >= 3                         # a, b, c overlapped
    a_events = [e[2] for e in FakeClient.log if e[0] == "start" and "a.example" in e[1]]
    assert a_events == ["e1", "e2", "e3"]               # per-endpoint ordering preserved
    starts = [i for i, e in enumerate(FakeClient.log) if e[0] == "start" and "a.example" in e[1]]
    ends = [i for i, e in enumerate(FakeClient.log) if e[0] == "end" and "a.example" in e[1]]
    assert all(ends[k] < starts[k + 1] for k in range(2))  # e(n) finished before e(n+1) started


def test_webhooks_inactive_cancelled_failure_requeued_and_exhausted_failed():
    hooks = [
        {"id": "off", "active": False, "endpoint_url": "https://off.example/h"},
        {"id": "bad", "active": True, "endpoint_url": "https://bad.example/h", "retry_limit": 6},
        {"id": "dead", "active": True, "endpoint_url": "https://dead.example/h", "retry_limit": 1},
    ]
    rows = [_d(1, "off", "x"), _d(2, "bad", "x"), _d(3, "dead", "x"), _d(4, "gone", "x")]
    fake, out = _run(rows, hooks, status={"https://bad.example/h": 500, "https://dead.example/h": 503})
    upd = {i: p for i, p in fake.updates}
    assert upd[1]["status"] == "cancelled"
    assert upd[4]["status"] == "cancelled"              # hook row missing -> cancelled, not crash
    assert upd[2]["status"] == "queued" and upd[2]["attempts"] == 1 and "next_attempt_at" in upd[2]
    assert upd[3]["status"] == "failed"                 # retry_limit reached
    assert out == {"processed": 4, "sent": 0, "failed": 2}
