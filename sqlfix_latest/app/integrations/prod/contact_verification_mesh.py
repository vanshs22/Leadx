"""LeadX 7.0 contact verification mesh.

Verification is deliberately ordered by economics and evidence strength:
1) free/local checks that need no key: syntax, MX, disposable list, a polite
   SMTP RCPT probe with catch-all detection (email) and libphonenumber/NANP
   plan rules (phone)
2) providers with free monthly tiers (only when their key is set)
3) paid-only providers (only when their key is set AND steps 1-2 were not
   decisive)

Evidence strength differs by tier: the SMTP probe is real mailbox evidence;
the local phone check proves the number is well-formed and dialable, not that
the line is live, so it is recorded with a lower confidence and any configured
phone provider still gets to confirm or overrule it.

A provider is only called when configured/enabled. Results are normalized into
field-level observations; the caller can persist them in lead_verification_observations.
"""
from __future__ import annotations
import asyncio, logging, os, re, secrets, socket, time
from typing import Any
from urllib.parse import quote_plus
import httpx

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
logger = logging.getLogger("lead_engine.verification_mesh")
PUBLIC_FREE = False  # No customer PII is sent to an unaudited public verifier.

# Tier 0 = local/open-source/no-key. Tier 1 = free plan/free credits. Tier 2 = paid-only fallback.
EMAIL_PROVIDERS = (
    {"name":"local_dns_disposable", "tier":0, "mode":"free", "env_key":None, "capabilities":["syntax","mx","disposable"]},
    {"name":"local_smtp_probe", "tier":0, "mode":"free_local", "env_key":None, "capabilities":["mx","smtp","catch_all"]},
    {"name":"self_hosted_email_validator", "tier":0, "mode":"free_self_hosted", "env_key":"LEADX_EMAIL_VALIDATOR_URL", "capabilities":["syntax","mx","smtp","disposable"]},
    {"name":"hunter", "tier":1, "mode":"free_plan_or_paid", "env_key":"HUNTER_API_KEY", "capabilities":["deliverability","score","status"]},
    {"name":"zerobounce", "tier":1, "mode":"free_plan_or_paid", "env_key":"ZEROBOUNCE_API_KEY", "capabilities":["deliverability","disposable","trap","risk","status"]},
    {"name":"abstract", "tier":1, "mode":"free_plan_or_paid", "env_key":"ABSTRACT_API_KEY", "capabilities":["smtp","mx","disposable","catch_all","quality"]},
    {"name":"neverbounce", "tier":2, "mode":"paid", "env_key":"NEVERBOUNCE_API_KEY", "capabilities":["deliverability","status"]},
    {"name":"bouncer", "tier":2, "mode":"paid", "env_key":"BOUNCER_API_KEY", "capabilities":["deliverability","risk","status"]},
    {"name":"kickbox", "tier":2, "mode":"paid", "env_key":"KICKBOX_API_KEY", "capabilities":["deliverability","status"]},
)

PHONE_PROVIDERS = (
    {"name":"libphonenumber_local", "tier":0, "mode":"free_local", "env_key":None, "capabilities":["format","country","type","possible"]},
    {"name":"randomapi_libphonenumber", "tier":0, "mode":"free_public", "env_key":None, "capabilities":["validation","country","type","e164"]},
    {"name":"phonevalidationapi", "tier":0, "mode":"free_plan", "env_key":"PHONEVALIDATIONAPI_KEY", "capabilities":["validation","confidence","carrier","line_type","disposable"]},
    {"name":"numbers_online", "tier":0, "mode":"free_plan", "env_key":"NUMBERS_ONLINE_API_KEY", "capabilities":["carrier","line_type","caller_name","risk"]},
    {"name":"twilio_lookup", "tier":1, "mode":"free_basic_paid_data", "env_key":"TWILIO_AUTH_TOKEN", "capabilities":["validation","line_type","carrier","risk"]},
    {"name":"veriphone", "tier":1, "mode":"free_plan_or_paid", "env_key":"VERIPHONE_API_KEY", "capabilities":["validation","carrier","line_type","location"]},
    {"name":"abstract", "tier":1, "mode":"free_plan_or_paid", "env_key":"ABSTRACT_API_KEY", "capabilities":["validation","carrier","line_type","location","risk"]},
    {"name":"numverify", "tier":1, "mode":"free_plan_or_paid", "env_key":"NUMVERIFY_API_KEY", "capabilities":["validation","carrier","line_type","location"]},
    {"name":"ipqs", "tier":2, "mode":"paid_or_trial", "env_key":"IPQS_API_KEY", "capabilities":["validation","carrier","line_type","fraud_risk"]},
)


def _env_enabled(name: str) -> bool:
    return bool(name and os.getenv(name))


def smtp_probe_enabled() -> bool:
    return (os.getenv("LEADX_SMTP_PROBE", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def verification_readiness() -> dict[str, Any]:
    """Describe which verification tiers are available.

    Verification no longer depends on a paid key: the local SMTP probe can
    return ``valid`` for emails and the local plan check can return ``valid``
    for phones. Paid/free-plan providers are optional upgrades that are called
    only when the local tiers are not decisive. ``ready`` is therefore True
    whenever any tier can produce a verdict; ``warnings`` says what is weak.
    """
    email_keys = [x["env_key"] for x in EMAIL_PROVIDERS if x.get("env_key")]
    phone_keys = [x["env_key"] for x in PHONE_PROVIDERS if x.get("env_key")]
    email_ok = any((os.getenv(k) or "").strip() for k in email_keys)
    phone_ok = any((os.getenv(k) or "").strip() for k in phone_keys)
    probe = smtp_probe_enabled()
    warnings: list[str] = []
    if not probe and not email_ok:
        warnings.append("SMTP probe disabled and no email provider set: no email can reach 'valid'; leads rely on verified phones.")
    if probe and not _port25_open():
        warnings.append("Outbound SMTP (port 25) looks blocked from this host: the local probe will return 'unknown'. Set an email provider key or LEADX_EMAIL_VALIDATOR_URL.")
    if not email_ok:
        warnings.append("Domains on Microsoft/Yahoo/gateway MX cannot be probed locally; they stay 'unknown' until an email provider key is set.")
    if not phone_ok:
        warnings.append("Phones are format/plan-validated locally only (confidence ~70); set a phone provider key to confirm the line is live.")
    return {
        "ready": True,
        "mode": "local_plus_providers" if (email_ok or phone_ok) else "local_only",
        "smtp_probe_enabled": probe,
        "email_provider_configured": bool(email_ok),
        "phone_provider_configured": bool(phone_ok),
        "set_one_of": sorted(set(email_keys + phone_keys)),
        "warnings": warnings,
    }


_DISPOSABLE = {
    "mailinator.com","guerrillamail.com","guerrillamail.net","10minutemail.com","tempmail.com","yopmail.com",
    "temp-mail.org","trashmail.com","throwawaymail.com","getnada.com","sharklasers.com","dispostable.com",
    "maildrop.cc","fakeinbox.com","mintemail.com","mailnesia.com","tempail.com","emailondeck.com","moakt.com",
}
_ROLE_LOCALPARTS = {"abuse","postmaster","hostmaster","webmaster","noreply","no-reply","donotreply","do-not-reply","mailer-daemon","bounce","bounces","spam","unsubscribe"}
_mx_cache: dict[str, tuple[list[str], float]] = {}
_MX_TTL = 3600.0


def _resolve_mx_sync(domain: str) -> list[str] | None:
    """Return MX hosts by preference. [] = domain has no MX. None = cannot resolve (no resolver)."""
    try:
        import dns.resolver  # dnspython
    except ImportError:
        logger.warning("dnspython is not installed: MX/SMTP verification unavailable (pip install dnspython)")
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        hosts = sorted(((r.preference, str(r.exchange).rstrip(".")) for r in answers), key=lambda x: x[0])
        return [h for _, h in hosts if h and h != "."]
    except Exception as exc:
        name = type(exc).__name__
        if name in {"NXDOMAIN", "NoAnswer", "NoNameservers"}:
            return []
        logger.debug("MX lookup failed for %s: %s", domain, name)
        return None


async def _mx_hosts(domain: str) -> list[str] | None:
    now = time.monotonic()
    hit = _mx_cache.get(domain)
    if hit and now - hit[1] < _MX_TTL:
        return hit[0]
    hosts = await asyncio.to_thread(_resolve_mx_sync, domain)
    if hosts is not None:
        _mx_cache[domain] = (hosts, now)
    return hosts


def email_local_check(email: str) -> dict[str, Any]:
    """Synchronous, key-free check: syntax + disposable + role-account flag.

    MX/SMTP evidence is added asynchronously by ``local_smtp_probe``.
    """
    e=(email or '').strip().lower()
    out={"provider":"local_dns_disposable","email":e,"syntax_valid":bool(EMAIL_RE.match(e)),"mx_found":None,"disposable":None,"status":"unknown","tier":0}
    if not out["syntax_valid"]:
        out["status"]="invalid"; return out
    local, domain = e.rsplit('@',1)
    out["disposable"] = domain in _DISPOSABLE
    out["role_account"] = local in _ROLE_LOCALPARTS
    if out["disposable"] or out["role_account"]:
        out["status"]="invalid"; out["reason"]="disposable_domain" if out["disposable"] else "system_mailbox"
        return out
    cached = _mx_cache.get(domain)
    if cached is not None:
        out["mx_found"] = bool(cached[0])
        out["status"] = "syntax_mx_valid" if cached[0] else "no_mx_or_unresolved"
    return out


# ---- local SMTP probe (free, no key) -------------------------------------------------
# Hosts that accept every RCPT or tarpit unknown probers; a probe there proves nothing.
_UNPROBEABLE_MX = ("protection.outlook.com","mail.protection","pphosted.com","ppe-hosted.com","mimecast","barracudanetworks",
                   "yahoodns.net","messagelabs.com","iphmx.com","mxlogic.net","fireeyecloud.com","trendmicro.com")
_USER_UNKNOWN = ("5.1.1","5.1.0","user unknown","unknown user","no such user","does not exist","doesn't exist","mailbox unavailable",
                 "mailbox not found","invalid recipient","recipient rejected","recipient address rejected: user","no mailbox","unknown recipient","address rejected")
_probe_cache: dict[str, tuple[dict[str, Any], float]] = {}
_PROBE_TTL = 6 * 3600.0
_probe_global_sem: asyncio.Semaphore | None = None
_probe_host_sems: dict[str, asyncio.Semaphore] = {}
_egress = {"fails": 0, "open_until": 0.0, "warned": False}


def _port25_open() -> bool:
    return time.monotonic() >= _egress["open_until"]


def _note_connect(ok: bool) -> None:
    if ok:
        _egress["fails"] = 0
        return
    _egress["fails"] += 1
    if _egress["fails"] >= 3:
        _egress["open_until"] = time.monotonic() + 600
        if not _egress["warned"]:
            _egress["warned"] = True
            logger.error("Outbound SMTP (port 25) appears blocked: local email probe paused for 10 min. "
                         "Use a host that allows port 25, or set an email provider key / LEADX_EMAIL_VALIDATOR_URL.")


def _smtp_probe_sync(email: str, mx_hosts: list[str], *, helo: str, mail_from: str, timeout: float, port: int = 25) -> dict[str, Any]:
    import smtplib
    domain = email.rsplit("@", 1)[1]
    last = "no_mx_reached"
    for host in mx_hosts[:2]:
        srv = smtplib.SMTP(local_hostname=helo, timeout=timeout)
        try:
            try:
                srv.connect(host, port)
            except (OSError, smtplib.SMTPException) as exc:
                _note_connect(False)
                last = f"connect_{type(exc).__name__}"
                continue
            _note_connect(True)
            code, _ = srv.ehlo()
            if code >= 400:
                srv.helo()
            code, msg = srv.mail(mail_from)
            if code >= 400:
                return {"result": "unknown", "reason": f"mail_from_{code}", "mx": host}
            rcode, rmsg = srv.rcpt(email)
            rtext = (rmsg.decode("utf-8", "ignore") if isinstance(rmsg, bytes) else str(rmsg)).lower()
            if rcode in (250, 251):
                pcode, _ = srv.rcpt(f"lx-{secrets.token_hex(6)}@{domain}")
                if pcode in (250, 251):
                    return {"result": "accept_all", "reason": "catch_all_domain", "mx": host, "code": rcode}
                return {"result": "valid", "reason": "rcpt_accepted_non_catch_all", "mx": host, "code": rcode}
            if rcode >= 500 and any(t in rtext for t in _USER_UNKNOWN):
                return {"result": "invalid", "reason": "mailbox_unknown", "mx": host, "code": rcode}
            return {"result": "unknown", "reason": f"rcpt_{rcode}_not_conclusive", "mx": host, "code": rcode}
        except (OSError, smtplib.SMTPException) as exc:
            last = f"smtp_{type(exc).__name__}"
        finally:
            try:
                srv.close()
            except Exception as exc:
                logger.debug("smtp close failed: %s", exc)
    return {"result": "unknown", "reason": last}


async def local_smtp_probe(email: str) -> dict[str, Any]:
    """Free mailbox check: MX -> RCPT TO (never sends mail) -> catch-all control address.

    Returns an observation. Only ``valid`` (accepted, domain not catch-all) and
    ``invalid`` (clear 'user unknown') are verdicts; everything else is
    ``unknown``/``accept_all`` so a configured provider can take over.
    """
    e = (email or "").strip().lower()
    obs: dict[str, Any] = {"provider": "local_smtp_probe", "tier": 0, "email": e, "status": "unknown"}
    if not smtp_probe_enabled():
        obs["reason"] = "probe_disabled"; return obs
    if not EMAIL_RE.match(e):
        obs["status"] = "invalid"; obs["reason"] = "syntax"; return obs
    domain = e.rsplit("@", 1)[1]
    hosts = await _mx_hosts(domain)
    if hosts is None:
        obs["reason"] = "mx_lookup_unavailable"; return obs
    if not hosts:
        # No MX does not prove the domain cannot receive mail (RFC 5321 A-record fallback).
        obs["reason"] = "no_mx"; obs["mx_found"] = False; return obs
    obs["mx_found"] = True
    if any(any(t in h.lower() for t in _UNPROBEABLE_MX) for h in hosts[:2]):
        obs["reason"] = "mx_provider_not_probeable"; return obs
    if not _port25_open():
        obs["reason"] = "smtp_egress_blocked"; return obs
    hit = _probe_cache.get(e)
    if hit and time.monotonic() - hit[1] < _PROBE_TTL:
        return {**obs, **hit[0]}
    global _probe_global_sem
    if _probe_global_sem is None:
        _probe_global_sem = asyncio.Semaphore(max(1, int(os.getenv("LEADX_SMTP_PROBE_CONCURRENCY", "8"))))
    host_sem = _probe_host_sems.setdefault(hosts[0].lower(), asyncio.Semaphore(2))
    helo = (os.getenv("LEADX_SMTP_HELO") or socket.getfqdn() or "localhost").strip()
    mail_from = (os.getenv("LEADX_SMTP_PROBE_FROM") or f"postmaster@{helo}").strip()
    timeout = float(os.getenv("LEADX_SMTP_PROBE_TIMEOUT", "12"))
    async with _probe_global_sem, host_sem:
        res = await asyncio.to_thread(_smtp_probe_sync, e, hosts, helo=helo, mail_from=mail_from, timeout=timeout)
    result = res.get("result", "unknown")
    out = {"status": result, "reason": res.get("reason"), "mx": res.get("mx"), "smtp_code": res.get("code")}
    if result == "valid":
        out["score"] = 88; out["catch_all"] = False
    elif result == "accept_all":
        out["catch_all"] = True
    if result in {"valid", "invalid", "accept_all"}:
        _probe_cache[e] = (out, time.monotonic())
        if len(_probe_cache) > 20000:
            _probe_cache.clear()
    return {**obs, **out}


async def _get_json(url: str, **kwargs) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
            r=await c.get(url, **kwargs)
            if r.status_code >= 400: return {"_http_status":r.status_code}
            return r.json()
    except Exception as exc:
        return {"_error":type(exc).__name__}


async def verify_email(email: str, providers: list[str] | None = None) -> dict[str, Any]:
    """Production free-first cascade: local -> free-plan -> paid fallback."""
    from app.integrations.prod.discovery_runtime import local_validation_mode
    if local_validation_mode():
        base = email_local_check(email)
        base["provider"] = "local_fixture"
        base["status"] = "valid" if base.get("status") not in {"invalid", "malformed"} else "invalid"
        base["confidence"] = 100 if base["status"] == "invalid" else 90
        return {"ok": True, "canonical": {**base, "email": base.get("email")},
                "observations": [{**base, "billable": False}]}
    # Paid/free-plan providers are called only when every earlier tier was
    # inconclusive (``decisive`` below), so a lead the local probe already
    # settled never spends a credit.
    base=email_local_check(email); e=base["email"]
    if base["status"] == "invalid":
        return {"ok":True,"canonical":{**base,"confidence":100},"observations":[base]}
    hosts = await _mx_hosts(e.rsplit("@",1)[1])
    if hosts is not None:
        base["mx_found"] = bool(hosts)
        base["status"] = "syntax_mx_valid" if hosts else "no_mx_or_unresolved"
    observations=[base]; explicit=bool(providers)
    order=[x["name"] for x in EMAIL_PROVIDERS]; requested=set(providers or order) & set(order)
    def decisive(obs):
        st=str(obs.get("status") or "unknown").lower()
        if st in {"invalid","undeliverable","do_not_mail","spamtrap","disposable"}: obs["confidence"]=95; return True
        if st in {"valid","deliverable"}: obs["confidence"]=float(obs.get("score") or obs.get("quality_score") or 90); return obs["confidence"]>=85
        return False
    async def maybe_return(obs):
        observations.append(obs)
        return decisive(obs) and not explicit
    if "self_hosted_email_validator" in requested and os.getenv("LEADX_EMAIL_VALIDATOR_URL"):
        try:
            d=await _get_json(os.getenv("LEADX_EMAIL_VALIDATOR_URL"),params={"email":e})
            if "_error" not in d and "_http_status" not in d:
                o={"provider":"self_hosted_email_validator","tier":0,"status":str(d.get("status","unknown")).lower(),"smtp":d.get("smtp_valid"),"mx":d.get("mx_valid")}
                if await maybe_return(o): return _canonical_email(e,observations)
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    if "local_smtp_probe" in requested:
        try:
            probe = await local_smtp_probe(e)
            if probe.get("reason") not in {"probe_disabled"}:
                if await maybe_return(probe): return _canonical_email(e,observations)
        except Exception as exc:
            logger.warning("local SMTP probe failed for a lead: %s", exc)
    async with httpx.AsyncClient(timeout=30,follow_redirects=False) as c:
        for name in ("hunter","zerobounce","abstract"):
            if name not in requested: continue
            key=next((x["env_key"] for x in EMAIL_PROVIDERS if x["name"]==name),None)
            if key and not os.getenv(key): continue
            try:
                if name=="hunter":
                    d=(await c.get('https://api.hunter.io/v2/email-verifier',params={'email':e,'api_key':os.getenv('HUNTER_API_KEY')})).json().get('data',{}); o={"provider":name,"tier":1,"status":str(d.get('status','unknown')).lower(),"score":d.get('score')}
                elif name=="zerobounce":
                    d=(await c.get('https://api.zerobounce.net/v2/validate',params={'email':e,'api_key':os.getenv('ZEROBOUNCE_API_KEY')})).json(); o={"provider":name,"tier":1,"status":str(d.get('status','unknown')).lower(),"sub_status":d.get('sub_status'),"disposable":d.get('disposable')}
                else:
                    d=(await c.get('https://emailvalidation.abstractapi.com/v1/',params={'api_key':os.getenv('ABSTRACT_API_KEY'),'email':e})).json(); o={"provider":name,"tier":1,"status":"valid" if d.get('is_valid_format',{}).get('value') and d.get('is_smtp_valid',{}).get('value') else "unknown","deliverability":d.get('deliverability'),"quality_score":d.get('quality_score')}
                if await maybe_return(o): return _canonical_email(e,observations)
            except Exception: continue
        for name in ("neverbounce","bouncer","kickbox"):
            if name not in requested: continue
            key=next((x["env_key"] for x in EMAIL_PROVIDERS if x["name"]==name),None)
            if key and not os.getenv(key): continue
            try:
                if name=="neverbounce":
                    d=(await c.get('https://api.neverbounce.com/v4/single/check',params={'key':os.getenv('NEVERBOUNCE_API_KEY'),'email':e})).json(); o={"provider":name,"tier":2,"status":str(d.get('result','unknown')).lower(),"flags":d.get('flags')}
                elif name=="bouncer":
                    d=(await c.get('https://api.usebouncer.com/v1.1/email/verify',params={'key':os.getenv('BOUNCER_API_KEY'),'email':e})).json(); o={"provider":name,"tier":2,"status":str(d.get('status','unknown')).lower(),"reason":d.get('reason'),"score":d.get('score')}
                else:
                    d=(await c.get('https://api.kickbox.com/v2/verify',params={'apikey':os.getenv('KICKBOX_API_KEY'),'email':e})).json(); o={"provider":name,"tier":2,"status":str(d.get('result','unknown')).lower(),"reason":d.get('reason'),"sendex":d.get('sendex')}
                if await maybe_return(o): return _canonical_email(e,observations)
            except Exception: continue
    return _canonical_email(e,observations)


def _canonical_email(e, observations):
    statuses=[str(x.get('status','')).lower() for x in observations]
    invalid=sum(s in {'invalid','undeliverable','do_not_mail','disposable','spamtrap'} for s in statuses); valid=sum(s in {'valid','deliverable'} for s in statuses)
    canonical='invalid' if invalid and invalid>=valid else 'valid' if valid else 'unknown'
    agree={'invalid':{'invalid','undeliverable','do_not_mail','disposable','spamtrap'},'valid':{'valid','deliverable'}}.get(canonical,set())
    confs=[float(x.get('confidence') or x.get('score') or 90) for x in observations if str(x.get('status','')).lower() in agree]
    evidence=[x.get('provider') for x in observations if str(x.get('status','')).lower() in agree]
    catch_all=any(x.get('catch_all') for x in observations)
    return {"ok":True,"canonical":{"email":e,"status":canonical,"confidence":(max(confs) if confs else 50),"providers":len(observations),
            "evidence":evidence,"catch_all":catch_all,"tier_order":"free->free_plan->paid"},"observations":observations}


async def _phone_randomapi(p: str, country_code: str | None) -> dict[str, Any] | None:
    if not PUBLIC_FREE:
        return None
    params={"value":p}
    if country_code: params["country"]=country_code
    d=await _get_json("https://randomapi.dev/api/phone-numbers/parse", params=params)
    if "_http_status" in d or "_error" in d: return None
    d=d.get("data",d)
    return {"provider":"randomapi_libphonenumber","tier":0,"status":"valid" if d.get("isValid") else "invalid","possible":d.get("isPossible"),"line_type":d.get("numberType"),"e164":d.get("e164"),"country":d.get("region")}


def phone_local_check(phone: str, country_code: str | None = None) -> dict[str, Any]:
    """Key-free plan/format validation (libphonenumber if installed, NANP rules otherwise).

    This proves the number is well-formed and allocated in the numbering plan.
    It does NOT prove the line is live, so confidence is capped at 70 and any
    configured phone provider still runs afterwards.
    """
    p=(phone or '').strip(); digits=re.sub(r'\D','',p)
    out={"provider":"libphonenumber_local","phone":p,"tier":0,"status":"unknown","shape_valid":len(digits)>=7}
    if not out["shape_valid"]:
        out["status"]="invalid"; out["reason"]="too_short"; return out
    try:
        import phonenumbers  # optional
        region=(country_code or os.getenv("LEADX_DEFAULT_PHONE_REGION") or "US").upper()
        num=phonenumbers.parse(p, None if p.startswith("+") else region)
        out["engine"]="phonenumbers"
        out["e164"]=phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        if not phonenumbers.is_valid_number(num):
            out["status"]="invalid"; out["reason"]="not_valid_for_region"; return out
        t=phonenumbers.number_type(num)
        out["line_type"]=str(t).split(".")[-1].lower()
        if t in (phonenumbers.PhoneNumberType.PREMIUM_RATE, phonenumbers.PhoneNumberType.SHARED_COST, phonenumbers.PhoneNumberType.VOICEMAIL):
            out["status"]="invalid"; out["reason"]="non_dialable_type"; return out
        out["status"]="valid"; out["confidence"]=70
        return out
    except ImportError:
        pass
    except Exception as exc:  # NumberParseException etc.
        out["engine"]="phonenumbers"; out["status"]="invalid"; out["reason"]=type(exc).__name__
        return out
    # NANP fallback (US/CA): NXX-NXX-XXXX, N=2-9, no N11 area/exchange, no 555-01xx fictional range.
    out["engine"]="nanp_rules"
    d=digits[1:] if len(digits)==11 and digits.startswith("1") else digits
    if len(d)!=10:
        if (country_code or "US").upper() in {"US","CA"}:
            out["status"]="invalid"; out["reason"]="not_10_digits"
        return out
    area, exch, line = d[:3], d[3:6], d[6:]
    bad = (area[0] in "01" or exch[0] in "01" or area[1:]=="11" or exch[1:]=="11" or (exch=="555" and line.startswith("01")) or len(set(d))==1)
    if bad:
        out["status"]="invalid"; out["reason"]="outside_numbering_plan"; return out
    out["e164"]="+1"+d; out["status"]="valid"; out["confidence"]=70
    return out


async def verify_phone(phone: str, country_code: str | None = None, providers: list[str] | None = None) -> dict[str, Any]:
    """Production free-first phone cascade; paid risk lookup is last resort."""
    from app.integrations.prod.discovery_runtime import local_validation_mode
    p=(phone or '').strip(); digits=re.sub(r'\D','',p); base=phone_local_check(p, country_code)
    if local_validation_mode():
        base["provider"] = "local_fixture"
        base["confidence"] = 100 if base.get("status") == "invalid" else 70
        return {"ok": True, "canonical": {**base, "phone": p},
                "observations": [{**base, "billable": False}]}
    if base['status']=='invalid':
        return {"ok":True,"canonical":{**base,"confidence":100},"observations":[base]}
    observations=[base]; explicit=bool(providers); requested=set(providers or [x["name"] for x in PHONE_PROVIDERS]) & {x["name"] for x in PHONE_PROVIDERS}
    def decisive(o):
        st=str(o.get('status') or '').lower()
        if st=='invalid': o['confidence']=95; return True
        if st=='valid': o['confidence']=float(o.get('confidence') or 90); return o['confidence']>=85
        return False
    if "randomapi_libphonenumber" in requested and PUBLIC_FREE:
        o=await _phone_randomapi(p,country_code)
        if o: observations.append(o)  # possible/valid shape; not reachability proof
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as c:
        for name in ("phonevalidationapi","numbers_online","twilio_lookup","veriphone","abstract","numverify"):
            if name not in requested: continue
            key=next((x["env_key"] for x in PHONE_PROVIDERS if x["name"]==name),None)
            if key and not os.getenv(key): continue
            try:
                if name=="phonevalidationapi":
                    d=(await c.post('https://phonevalidationapi.com/api/v1/validate',headers={'Authorization':f'Bearer {os.getenv("PHONEVALIDATIONAPI_KEY")}'},json={'phone':p,'country':country_code})).json(); o={'provider':name,'tier':0,'status':'valid' if d.get('valid') else 'invalid','confidence':d.get('confidence'),'line_type':d.get('line_type'),'carrier':d.get('carrier')}
                elif name=="numbers_online":
                    d=(await c.get(os.getenv('NUMBERS_ONLINE_LOOKUP_URL') or 'https://numbers.online/api/lookup',params={'number':p,'api_key':os.getenv('NUMBERS_ONLINE_API_KEY')})).json(); o={'provider':name,'tier':0,'status':'valid' if d.get('valid',True) else 'invalid','line_type':d.get('line_type'),'carrier':d.get('carrier'),'risk':d.get('risk')}
                elif name=="twilio_lookup":
                    auth=(os.getenv('TWILIO_ACCOUNT_SID'),os.getenv('TWILIO_AUTH_TOKEN')); params={'CountryCode':country_code} if country_code else {}; d=(await c.get('https://lookups.twilio.com/v2/PhoneNumbers/'+quote_plus(p),params=params,auth=auth)).json(); li=d.get('line_type_intelligence') or {}; o={'provider':name,'tier':1,'status':'valid' if d.get('valid',True) else 'invalid','line_type':li.get('type'),'carrier':li.get('carrier_name'),'phone_number':d.get('phone_number')}
                elif name=="veriphone":
                    d=(await c.get('https://api.veriphone.io/v3/verify',params={'phone':p,'key':os.getenv('VERIPHONE_API_KEY')})).json(); o={'provider':name,'tier':1,'status':'valid' if d.get('phone_valid') else 'invalid','line_type':d.get('phone_type'),'carrier':d.get('carrier'),'country':d.get('country')}
                elif name=="abstract":
                    d=(await c.get('https://phonevalidation.abstractapi.com/v1/',params={'api_key':os.getenv('ABSTRACT_API_KEY'),'phone':digits})).json(); o={'provider':name,'tier':1,'status':'valid' if d.get('valid') else 'invalid','line_type':d.get('line_type'),'carrier':d.get('carrier'),'risk_score':d.get('risk_score')}
                else:
                    d=(await c.get('http://apilayer.net/api/validate',params={'access_key':os.getenv('NUMVERIFY_API_KEY'),'number':p,'country_code':country_code or ''})).json(); o={'provider':name,'tier':1,'status':'valid' if d.get('valid') else 'invalid','line_type':d.get('line_type'),'carrier':d.get('carrier'),'country':d.get('country_name')}
                observations.append(o)
                if decisive(o) and not explicit: return _canonical_phone(p,observations)
            except Exception: continue
        if "ipqs" in requested and os.getenv('IPQS_API_KEY'):
            try:
                d=(await c.get(f'https://ipqualityscore.com/api/json/phone/{os.getenv("IPQS_API_KEY")}/{quote_plus(p)}')).json(); o={'provider':'ipqs','tier':2,'status':'valid' if d.get('valid') else 'invalid','line_type':d.get('line_type'),'carrier':d.get('carrier'),'fraud_score':d.get('fraud_score'),'active':d.get('active')}; observations.append(o)
            except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return _canonical_phone(p,observations)


def _canonical_phone(p, observations):
    statuses=[str(x.get('status','')).lower() for x in observations]; invalid=sum(s=='invalid' for s in statuses); valid=sum(s=='valid' for s in statuses)
    canonical='invalid' if invalid and invalid>=valid else 'valid' if valid else 'shape_valid'
    agree={'invalid':{'invalid'},'valid':{'valid'}}.get(canonical,set())
    confs=[float(x.get('confidence') or 90) for x in observations if str(x.get('status','')).lower() in agree]
    evidence=[x.get('provider') for x in observations if str(x.get('status','')).lower() in agree]
    return {'ok':True,'canonical':{'phone':p,'status':canonical,'confidence':(max(confs) if confs else 55),'providers':len(observations),
            'evidence':evidence,'line_confirmed':any(x.get('tier',0)>=1 or x.get('provider') in {'phonevalidationapi','numbers_online'} for x in observations if str(x.get('status','')).lower()=='valid'),
            'tier_order':'free->free_plan->paid'},'observations':observations}


def verification_catalog() -> dict[str,list[dict]]:
    return {'email':EMAIL_PROVIDERS,'phone':PHONE_PROVIDERS}
