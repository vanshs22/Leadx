"""Local stand-in for Supabase's API gateway: maps /rest/v1/* to PostgREST. Test stack only.
usage: gateway.py <listen_port> <postgrest_port>"""
import http.client, json, sys, os
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN, UPSTREAM = int(sys.argv[1]), int(sys.argv[2])
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "host", "content-length"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    wbufsize = 65536  # headers and body leave in one segment; unbuffered writes + Nagle added ~40 ms to every call

    def setup(self):
        super().setup()
        self.connection.setsockopt(__import__("socket").IPPROTO_TCP, __import__("socket").TCP_NODELAY, 1)

    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, apikey, content-type, x-client-info, x-supabase-api-version, prefer, range")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _go(self):
        if self.command == "OPTIONS":  # browser preflight from the portal origin
            self.send_response(204); self._cors(); self.send_header("Content-Length", "0"); self.end_headers(); return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        if self.path.startswith("/auth/v1"):
            import authshim
            u = urlparse(self.path)
            code, obj = authshim.handle(self.command, u.path, parse_qs(u.query), self.headers, body)
            data = json.dumps(obj).encode() if obj is not None else b""
            self.send_response(code); self._cors(); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if not self.path.startswith("/rest/v1"):
            self.send_error(404); return
        _t0 = __import__("time").monotonic()
        c = http.client.HTTPConnection("127.0.0.1", UPSTREAM, timeout=60)
        c.connect(); c.sock.setsockopt(__import__("socket").IPPROTO_TCP, __import__("socket").TCP_NODELAY, 1)
        c.request(self.command, self.path[len("/rest/v1"):] or "/", body,
                  {k: v for k, v in self.headers.items() if k.lower() not in HOP})
        r = c.getresponse(); data = r.read()
        self.send_response(r.status); self._cors()
        for k, v in r.getheaders():
            if k.lower() not in HOP: self.send_header(k, v)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        if self.command != "HEAD": self.wfile.write(data)
        self.wfile.flush()
        c.close()
        if os.environ.get("GW_LOG"):  # per-request path and milliseconds, for performance work
            with open(os.environ["GW_LOG"], "a") as f:
                f.write(f"{self.command} {self.path.split('?')[0]} {int((__import__('time').monotonic() - _t0) * 1000)}\n")

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = _go


ThreadingHTTPServer(("127.0.0.1", LISTEN), H).serve_forever()
