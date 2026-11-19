"""SMTP probe against a local scripted SMTP server (outbound port 25 is not needed)."""
from __future__ import annotations

import asyncio
import importlib.util
import socket
import ssl
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _mesh():
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))
    spec = importlib.util.spec_from_file_location("cvm_fake", ROOT / "app/integrations/prod/contact_verification_mesh.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Fake:
    """known: accepted mailboxes. catch_all: accept every RCPT. rcpt_override: {addr: reply line}.
    grey_first: reply 451 to the first N RCPTs (all connections). starttls: advertise STARTTLS (never completes)."""

    def __init__(self, known=(), catch_all=False, rcpt_override=None, grey_first=0, starttls=False, silent=False, control_reply=None):
        self.known, self.catch_all, self.over = set(known), catch_all, rcpt_override or {}
        self.grey, self.starttls, self.silent, self.control_reply = grey_first, starttls, silent, control_reply
        self.cmds: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0)); self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        if self.silent:
            threading.Event().wait(5); c.close(); return
        f = c.makefile("rwb", buffering=0)
        f.write(b"220 fake ESMTP\r\n")
        while True:
            try:
                line = f.readline()
            except OSError:
                return
            if not line:
                return
            cmd = line.decode().strip(); up = cmd.upper()
            self.cmds.append(up.split()[0].split(":")[0])
            if up.startswith("EHLO"):
                f.write(b"250-fake\r\n250-STARTTLS\r\n250 8BITMIME\r\n" if self.starttls else b"250 fake\r\n")
            elif up.startswith("STARTTLS"):
                f.write(b"454 TLS not available\r\n")
            elif up.startswith("RCPT"):
                addr = cmd.split(":", 1)[1].strip().strip("<>").lower()
                if self.grey > 0:
                    self.grey -= 1; f.write(b"451 4.7.1 greylisted, try later\r\n")
                elif addr in self.over:
                    f.write((self.over[addr] + "\r\n").encode())
                elif self.control_reply and addr not in self.known:
                    f.write((self.control_reply + "\r\n").encode())
                elif self.catch_all or addr in self.known:
                    f.write(b"250 ok\r\n")
                else:
                    f.write(b"550 5.1.1 User unknown\r\n")
            elif up.startswith("QUIT"):
                f.write(b"221 bye\r\n"); return
            else:
                f.write(b"250 ok\r\n")

    def close(self):
        self.sock.close()


@pytest.fixture
def m():
    return _mesh()


def probe(m, srv, email, hosts=("127.0.0.1",), **kw):
    kw.setdefault("greylist_delay", 0)
    kw.setdefault("connect_timeout", 2)
    return m._smtp_probe_sync(email, list(hosts), helo="probe.example.com", mail_from="postmaster@probe.example.com",
                              timeout=2, port=srv.port, **kw)


def test_accept_is_valid_and_rset_sent_between_rcpts(m):
    s = Fake(known={"a@x.test"})
    r = probe(m, s, "a@x.test")
    s.close()
    assert r["result"] == "valid" and r["tls"] is False
    i = s.cmds.index("RCPT")
    assert s.cmds[i + 1] == "RSET" and s.cmds[i + 2] == "MAIL" and s.cmds[i + 3] == "RCPT"


def test_user_unknown_is_invalid(m):
    s = Fake(known={"a@x.test"})
    assert probe(m, s, "ghost@x.test")["result"] == "invalid"
    s.close()


def test_catch_all_is_never_valid(m):
    s = Fake(catch_all=True)
    r = probe(m, s, "a@x.test")
    s.close()
    assert r["result"] == "accept_all"


def test_control_4xx_is_inconclusive_not_valid(m):
    s = Fake(known={"a@x.test"}, control_reply="451 try later")
    r = probe(m, s, "a@x.test")
    s.close()
    assert r["result"] == "unknown" and r["reason"] == "catch_all_check_inconclusive"


def test_greylist_persistent_is_unknown_after_bounded_retry(m):
    s = Fake(known={"a@x.test"}, grey_first=99)
    r = probe(m, s, "a@x.test", greylist_retries=2)
    s.close()
    assert r["result"] == "unknown" and r["reason"] == "greylisted"
    assert s.cmds.count("RCPT") == 3


def test_greylist_then_accept_on_retry(m):
    s = Fake(known={"a@x.test"}, grey_first=1)
    r = probe(m, s, "a@x.test", greylist_retries=1)
    s.close()
    assert r["result"] == "valid"


def test_timeout_is_unknown(m):
    s = Fake(silent=True)
    r = probe(m, s, "a@x.test", connect_timeout=0.5)
    s.close()
    assert r["result"] == "unknown" and r["reason"].startswith("connect_")


def test_second_mx_used_when_first_refuses(m):
    s = Fake(known={"a@x.test"})  # listens on 127.0.0.1 only; 127.0.0.2 refuses
    r = probe(m, s, "a@x.test", hosts=("127.0.0.2", "127.0.0.1"))
    s.close()
    assert r["result"] == "valid" and r["mx"] == "127.0.0.1"


def test_starttls_failure_falls_back_to_plain(m):
    s = Fake(known={"a@x.test"}, starttls=True)
    r = probe(m, s, "a@x.test")
    s.close()
    assert r["result"] == "valid" and r["tls"] is False and "STARTTLS" in s.cmds


def test_starttls_not_attempted_when_not_advertised(m):
    s = Fake(known={"a@x.test"})
    probe(m, s, "a@x.test")
    s.close()
    assert "STARTTLS" not in s.cmds


class _Ans:
    def __init__(self, pref, ex): self.preference, self.exchange = pref, ex


def _dns(monkeypatch, table):
    import dns.resolver as r

    def fake(domain, rtype, lifetime=8):
        v = table.get((domain, rtype), r.NoAnswer())
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(r, "resolve", fake)
    return r


def test_no_mx_falls_back_to_a_record(m, monkeypatch):
    _dns(monkeypatch, {("x.test", "A"): [object()]})
    assert m._resolve_mx_sync("x.test") == ["x.test"]


def test_null_mx_nxdomain_and_nothing_mean_no_mail(m, monkeypatch):
    import dns.resolver as r
    _dns(monkeypatch, {("n.test", "MX"): [_Ans(0, ".")], ("d.test", "MX"): r.NXDOMAIN()})
    assert m._resolve_mx_sync("n.test") == []
    assert m._resolve_mx_sync("d.test") == []
    assert m._resolve_mx_sync("empty.test") == []  # no MX, no A, no AAAA


def test_mx_sorted_by_priority(m, monkeypatch):
    _dns(monkeypatch, {("x.test", "MX"): [_Ans(20, "b.x.test."), _Ans(10, "a.x.test.")]})
    assert m._resolve_mx_sync("x.test") == ["a.x.test", "b.x.test"]


def test_local_probe_end_to_end_and_identity_gate(m, monkeypatch):
    s = Fake(known={"a@x.test"})

    async def mx(d): return ["127.0.0.1"]
    monkeypatch.setattr(m, "_mx_hosts", mx)
    monkeypatch.setenv("LEADX_SMTP_PORT", str(s.port))
    monkeypatch.setenv("LEADX_SMTP_PREFLIGHT_HOST", "127.0.0.1")
    monkeypatch.setenv("LEADX_SMTP_PREFLIGHT_PORT", str(s.port))
    monkeypatch.setenv("LEADX_SMTP_PROBE_TIMEOUT", "2")
    monkeypatch.delenv("LEADX_SMTP_HELO", raising=False)
    monkeypatch.delenv("LEADX_SMTP_PROBE_FROM", raising=False)
    r = asyncio.run(m.local_smtp_probe("a@x.test"))
    assert r["status"] == "unknown" and r["reason"] == "probe_identity_invalid"
    monkeypatch.setenv("LEADX_SMTP_HELO", "probe.example.com")
    r = asyncio.run(m.local_smtp_probe("a@x.test"))
    s.close()
    assert r["status"] == "valid" and r["catch_all"] is False


def _clean(monkeypatch):
    for k in ("LEADX_SMTP_PROBE_ENABLED", "LEADX_SMTP_PROBE", "LEADX_SMTP_HELO", "LEADX_SMTP_PROBE_FROM", "LEADX_SMTP_PROBE_TIMEOUT",
              "LEADX_SMTP_PROBE_CONCURRENCY", "LEADX_SMTP_SOCKS_PROXY"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("helo", ["", "localhost", "10.0.0.5", "myhost", "bad_name.example.com"])
def test_readiness_flags_bad_helo(m, monkeypatch, helo):
    _clean(monkeypatch)
    if helo:
        monkeypatch.setenv("LEADX_SMTP_HELO", helo)
    r = m.verification_readiness()
    assert r["smtp_probe_config_ok"] is False and any("LEADX_SMTP_HELO" in e for e in r["errors"])


def test_readiness_flags_bad_from_and_numbers_and_notes_ptr(m, monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("LEADX_SMTP_HELO", "mail.example.com")
    monkeypatch.setenv("LEADX_SMTP_PROBE_FROM", "not-an-email")
    monkeypatch.setenv("LEADX_SMTP_PROBE_TIMEOUT", "abc")
    r = m.verification_readiness()
    assert any("PROBE_FROM" in e for e in r["errors"]) and any("PROBE_TIMEOUT" in e for e in r["errors"])
    assert any("PTR" in w for w in r["warnings"])


def test_readiness_ok_config(m, monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("LEADX_SMTP_HELO", "mail.example.com")
    monkeypatch.setenv("LEADX_SMTP_PROBE_FROM", "probe@other.org")
    r = m.verification_readiness()
    assert r["smtp_probe_config_ok"] is True and any("differs" in w for w in r["warnings"])


def test_probe_enabled_flag_overrides_legacy(m, monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("LEADX_SMTP_PROBE", "1")
    monkeypatch.setenv("LEADX_SMTP_PROBE_ENABLED", "0")
    assert m.smtp_probe_enabled() is False
