"""Send one safe SES smoke-test email; optionally inspect an IMAP inbox.

Usage:
  python scripts/test_ses_email.py --send
  python scripts/test_ses_email.py --send --from-email verified@yourdomain.com --to vansh1998singh@gmail.com
  python scripts/test_ses_email.py --receive

This script never includes leads, client data, or provider payloads. SES must
already verify the From identity; sandbox accounts must also verify the To
identity. Receiving mail is not exposed by SES SendEmail, so --receive requires
IMAP_* credentials or an SES inbound receipt rule that stores/forwards mail.
"""
from __future__ import annotations

import argparse
from email import message_from_bytes
from email.header import decode_header, make_header
import imaplib
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

DEFAULT_ADDRESS = "vansh1998singh@gmail.com"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip().strip('"').strip("'")


def _send(from_email: str, to_email: str) -> dict[str, Any]:
    import boto3
    region = _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1"
    kwargs: dict[str, Any] = {"region_name": region}
    if _env("AWS_ACCESS_KEY_ID") and _env("AWS_SECRET_ACCESS_KEY"):
        kwargs.update({
            "aws_access_key_id": _env("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": _env("AWS_SECRET_ACCESS_KEY"),
        })
    client = boto3.client("sesv2", **kwargs)
    subject = "LeadX SES smoke test"
    text = (
        "This is a single LeadX SES smoke-test message.\n\n"
        "No lead, client, or production data was included.\n"
        f"From: {from_email}\nTo: {to_email}\n"
    )
    response = client.send_email(
        FromEmailAddress=from_email,
        Destination={"ToAddresses": [to_email]},
        Content={"Simple": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": text, "Charset": "UTF-8"}},
        }},
        ReplyToAddresses=[from_email],
        EmailTags=[{"Name": "leadx_test", "Value": "ses_smoke"}],
    )
    return {"ok": True, "provider": "ses", "region": region, "message_id": response.get("MessageId"), "from": from_email, "to": to_email}


def _decode(value: str) -> str:
    return str(make_header(decode_header(value or "")))


def _receive(from_filter: str | None = None) -> dict[str, Any]:
    host = _env("IMAP_HOST")
    user = _env("IMAP_USER")
    password = _env("IMAP_PASSWORD")
    if not host or not user or not password:
        return {"ok": False, "error": "IMAP_HOST, IMAP_USER, and IMAP_PASSWORD are required for inbox receive testing"}
    port = int(_env("IMAP_PORT", "993"))
    mailbox = _env("IMAP_MAILBOX", "INBOX")
    with imaplib.IMAP4_SSL(host, port) as inbox:
        inbox.login(user, password)
        inbox.select(mailbox, readonly=True)
        criteria = f'FROM "{from_filter}"' if from_filter else "ALL"
        status, data = inbox.search(None, criteria)
        if status != "OK":
            return {"ok": False, "error": "imap_search_failed"}
        ids = (data[0] or b"").split()
        latest = ids[-1:] if ids else []
        messages = []
        for message_id in latest:
            status, raw = inbox.fetch(message_id, "(RFC822)")
            if status != "OK":
                continue
            payload = next((part[1] for part in raw if isinstance(part, tuple)), b"")
            message = message_from_bytes(payload)
            messages.append({
                "subject": _decode(message.get("Subject", "")),
                "from": _decode(message.get("From", "")),
                "date": message.get("Date"),
                "message_id": message.get("Message-ID"),
            })
        return {"ok": True, "mailbox": mailbox, "matched": len(ids), "latest": messages}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send or inspect one LeadX SES smoke-test message")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--receive", action="store_true")
    parser.add_argument("--from-email", default=_env("SES_FROM") or DEFAULT_ADDRESS)
    parser.add_argument("--to", default=DEFAULT_ADDRESS)
    parser.add_argument("--from-filter", default=None)
    args = parser.parse_args()
    if not args.send and not args.receive:
        parser.error("choose --send or --receive")
    results: dict[str, Any] = {}
    if args.send:
        try:
            results["send"] = _send(args.from_email, args.to)
        except Exception as exc:
            results["send"] = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:500]}
    if args.receive:
        try:
            results["receive"] = _receive(args.from_filter or args.from_email)
        except Exception as exc:
            results["receive"] = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:500]}
    import json
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(value.get("ok") for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
