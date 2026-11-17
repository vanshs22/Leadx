"""Local stand-in for Supabase's API gateway: maps /rest/v1/* to PostgREST. Test stack only.
usage: gateway.py <listen_port> <postgrest_port>"""
import http.client, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN, UPSTREAM = int(sys.argv[1]), int(sys.argv[2])
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "host", "content-length"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self):
        if not self.path.startswith("/rest/v1"):
            self.send_error(404); return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        c = http.client.HTTPConnection("127.0.0.1", UPSTREAM, timeout=60)
        c.request(self.command, self.path[len("/rest/v1"):] or "/", body,
                  {k: v for k, v in self.headers.items() if k.lower() not in HOP})
        r = c.getresponse(); data = r.read()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in HOP: self.send_header(k, v)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        if self.command != "HEAD": self.wfile.write(data)
        c.close()

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = _go


ThreadingHTTPServer(("127.0.0.1", LISTEN), H).serve_forever()
