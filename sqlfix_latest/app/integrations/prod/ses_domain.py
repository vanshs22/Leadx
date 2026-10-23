
"""
Amazon SES (SESv2): automate domain identities on OUR account.
Client only adds DNS at their registrar; we never need the SES console per client.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("lead_engine.ses_domain")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip().strip('"').strip("'")


def _client():
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("boto3 not installed — pip install boto3") from e
    region = _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1"
    kwargs: dict[str, Any] = {"region_name": region}
    key = _env("AWS_ACCESS_KEY_ID")
    secret = _env("AWS_SECRET_ACCESS_KEY")
    if key and secret:
        kwargs["aws_access_key_id"] = key
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("sesv2", **kwargs)


def ses_configured() -> bool:
    """Whether boto3 has a usable credential path configured.

    Explicit access keys are supported, but ECS/EKS/EC2/Lambda/IRSA-style
    default credential chains are also valid and preferable in production.
    """
    if _env("AWS_ACCESS_KEY_ID") and _env("AWS_SECRET_ACCESS_KEY"):
        return True
    try:
        import boto3
        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def ses_status() -> dict[str, Any]:
    """
    Report whether SES is usable + human reason.
    ready | missing_keys | missing_boto3 | invalid_credentials | error
    """
    key = _env("AWS_ACCESS_KEY_ID")
    secret = _env("AWS_SECRET_ACCESS_KEY")
    region = _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        import boto3
    except ImportError:
        return {
            "ok": False,
            "ready": False,
            "reason": "missing_boto3",
            "message": "boto3 not installed in API venv. Run: pip install boto3 && restart API.",
            "region": region,
        }
    # boto3 may obtain credentials from an IAM role/default provider chain.
    # Do not require long-lived access keys when the runtime already has an
    # attached AWS identity.
    try:
        creds = boto3.Session().get_credentials()
    except Exception:
        creds = None
    if not creds and not (key and secret):
        return {
            "ok": False,
            "ready": False,
            "reason": "missing_credentials",
            "message": "No AWS credentials were found by boto3. Configure an IAM role or AWS access key/secret.",
            "region": region,
            "key_prefix": (key[:4] + "…") if key else None,
        }
    try:
        c = _client()
        # Lightweight call to validate credentials
        c.get_account()
        return {
            "ok": True,
            "ready": True,
            "reason": "ok",
            "message": "SES credentials valid — ready to connect domains.",
            "region": region,
            "key_prefix": key[:8] + "…",
        }
    except Exception as e:
        err = str(e)
        low = err.lower()
        if "security token" in low or "invalid" in low or "UnrecognizedClient" in err or "InvalidClientTokenId" in err or "SignatureDoesNotMatch" in err:
            reason = "invalid_credentials"
            message = "AWS rejected the access key/secret (wrong key, deactivated, or incomplete secret). Create a new IAM access key and restart API."
        elif "AccessDenied" in err or "not authorized" in low:
            reason = "access_denied"
            message = "Keys work but IAM policy lacks SES permissions. Attach AmazonSESFullAccess (or SES send/identity actions)."
        else:
            reason = "error"
            message = err[:300]
        return {
            "ok": False,
            "ready": False,
            "reason": reason,
            "message": message,
            "region": region,
            "key_prefix": key[:8] + "…",
            "error": err[:300],
        }


def create_domain_identity(domain: str) -> dict[str, Any]:
    """Register domain on our SES account; return DKIM CNAME records for the client."""
    domain = (domain or "").strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    if not domain or "." not in domain:
        return {"ok": False, "error": "invalid_domain"}
    st = ses_status()
    if not st.get("ready"):
        return {"ok": False, "error": st.get("reason") or "ses_not_ready", "hint": st.get("message")}

    try:
        c = _client()
        try:
            c.create_email_identity(EmailIdentity=domain)
        except Exception as e:
            if "AlreadyExists" not in str(e) and "already exists" not in str(e).lower():
                logger.info("create_email_identity: %s", e)

        info = c.get_email_identity(EmailIdentity=domain)
        dkim = info.get("DkimAttributes") or {}
        tokens = dkim.get("Tokens") or []
        records = [
            {
                "type": "CNAME",
                "name": f"{t}._domainkey.{domain}",
                "value": f"{t}.dkim.amazonses.com",
            }
            for t in tokens
        ]
        verified = bool(info.get("VerifiedForSendingStatus"))
        dkim_status = dkim.get("Status") or dkim.get("SigningStatus") or ""
        return {
            "ok": True,
            "domain": domain,
            "verified": verified,
            "dkim_status": dkim_status,
            "dns_records": records,
            "identity": domain,
        }
    except Exception as e:
        logger.exception("SES create_domain_identity")
        return {"ok": False, "error": str(e)[:400]}


def get_domain_status(domain: str) -> dict[str, Any]:
    domain = (domain or "").strip().lower()
    if not domain:
        return {"ok": False, "error": "invalid_domain"}
    st = ses_status()
    if not st.get("ready"):
        return {"ok": False, "error": st.get("reason") or "ses_not_ready", "hint": st.get("message")}
    try:
        c = _client()
        info = c.get_email_identity(EmailIdentity=domain)
        dkim = info.get("DkimAttributes") or {}
        tokens = dkim.get("Tokens") or []
        records = [
            {
                "type": "CNAME",
                "name": f"{t}._domainkey.{domain}",
                "value": f"{t}.dkim.amazonses.com",
            }
            for t in tokens
        ]
        verified = bool(info.get("VerifiedForSendingStatus"))
        return {
            "ok": True,
            "domain": domain,
            "verified": verified,
            "dkim_status": dkim.get("Status") or dkim.get("SigningStatus") or "",
            "dns_records": records,
        }
    except Exception as e:
        logger.exception("SES get_domain_status")
        return {"ok": False, "error": str(e)[:400]}


async def send_via_ses(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    from_addr: str = "",
    reply_to: Optional[str] = None,
    unsubscribe_url_value: Optional[str] = None,
) -> dict:
    """Send one email via OUR SES account (From must be on a verified identity)."""
    st = ses_status()
    if not st.get("ready"):
        return {"ok": False, "error": st.get("reason") or "ses_not_ready", "provider": "ses", "hint": st.get("message")}
    from_addr = (from_addr or _env("SES_FROM") or _env("EMAIL_FROM") or "").strip()
    if not from_addr or not to:
        return {"ok": False, "error": "missing_from_or_to", "provider": "ses"}
    try:
        c = _client()
        dest: dict[str, Any] = {"ToAddresses": [to]}
        body: dict[str, Any] = {"Html": {"Data": html or text or "", "Charset": "UTF-8"}}
        if text:
            body["Text"] = {"Data": text, "Charset": "UTF-8"}
        msg: dict[str, Any] = {
            "Subject": {"Data": subject or "(no subject)", "Charset": "UTF-8"},
            "Body": body,
        }
        kwargs: dict[str, Any] = {
            "FromEmailAddress": from_addr,
            "Destination": dest,
            "Content": {"Simple": msg},
        }
        if reply_to:
            kwargs["ReplyToAddresses"] = [reply_to]
        if unsubscribe_url_value:
            msg["Headers"] = [
                {"Name": "List-Unsubscribe", "Value": f"<{unsubscribe_url_value}>"},
                {"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"},
            ]
        resp = c.send_email(**kwargs)
        return {"ok": True, "provider": "ses", "id": resp.get("MessageId")}
    except Exception as e:
        logger.warning("SES send: %s", e)
        return {"ok": False, "error": str(e)[:400], "provider": "ses"}
