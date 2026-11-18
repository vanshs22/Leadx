"""LOCAL TEST stand-in for the Stripe API calls LeadX makes (checkout + billing-portal sessions). Records requests; never talks to Stripe.
usage: fake_stripe.py <port>"""
import json, sys, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
LOG = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/__log": return self._send(200, LOG)
        self._send(404, {"error": {"message": "not modelled"}})
    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()); LOG.append({"path": self.path, "form": {k: v[0] for k, v in form.items()}, "auth": self.headers.get("Authorization", "")[:12]})
        if self.path == "/v1/checkout/sessions":
            sid = "cs_test_" + uuid.uuid4().hex[:12]; return self._send(200, {"id": sid, "object": "checkout.session", "url": f"https://checkout.stripe.test/c/pay/{sid}"})
        if self.path == "/v1/billing_portal/sessions":
            return self._send(200, {"id": "bps_test", "object": "billing_portal.session", "url": "https://billing.stripe.test/p/session/test"})
        self._send(404, {"error": {"message": "not modelled"}})
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
