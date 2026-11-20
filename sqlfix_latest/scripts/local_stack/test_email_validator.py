"""LOCAL TEST ADAPTER for LEADX_EMAIL_VALIDATOR_URL. Sends nothing: it only resolves MX records over DNS and
reports the address as 'valid' when the domain has an MX and the local part looks like a mailbox.
Stands in for the SMTP probe, which the sandbox cannot run (outbound port 25 blocked). Never use in production.
usage: test_email_validator.py <port>"""
import json, re, socket, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
try:
    import dns.resolver as R
except Exception:
    R = None

def has_mx(domain):
    try:
        if R: return bool(list(R.resolve(domain, "MX", lifetime=4)))
        return bool(socket.getaddrinfo(domain, 25))
    except Exception:
        return False

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        e = (parse_qs(urlparse(self.path).query).get("email") or [""])[0].lower()
        ok_syntax = bool(re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", e))
        mx = ok_syntax and has_mx(e.rsplit("@", 1)[1])
        body = json.dumps({"status": "valid" if mx else "invalid", "smtp_valid": bool(mx), "mx_valid": bool(mx), "test_adapter": True}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
