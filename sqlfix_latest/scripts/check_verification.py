"""Live check of the free email/phone verification lanes from this host. No keys needed, nothing is sent.

Run: python scripts/check_verification.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("LOCAL_VALIDATION_MODE", None)
from app.integrations.prod import contact_verification_mesh as m  # noqa: E402


async def main() -> None:
    rows: list[tuple[str, str, str]] = []
    mx = {d: await m._mx_hosts(d) for d in ("gmail.com", "microsoft.com", "no-such-domain-leadx-test.invalid", "example.com")}
    rows.append(("dns_mx_lookup", "yes" if mx["gmail.com"] else "no", f"gmail.com MX={len(mx['gmail.com'] or [])}"))
    rows.append(("mx_missing_domain", "yes" if mx["no-such-domain-leadx-test.invalid"] == [] else "no", "NXDOMAIN gives []"))
    rows.append(("disposable_list", "yes" if m.email_local_check("a@mailinator.com")["status"] == "invalid" else "no", "mailinator.com rejected"))
    rows.append(("role_mailbox_block", "yes" if m.email_local_check("postmaster@example.com")["status"] == "invalid" else "no", "system mailboxes rejected"))
    await m._egress_preflight()
    open25 = m._port25_open()
    rows.append(("smtp_probe_port25", "yes" if open25 else "no", "port 25 reachable" if open25 else "port 25 blocked, probe returns unknown"))
    ms = await m.local_smtp_probe("info@microsoft.com")
    rows.append(("smtp_probe_microsoft_mx", "no", f"{ms.get('reason')} (cannot be probed from any host; needs a provider key)"))
    good, bad = m.phone_local_check("+1 713 555 0123"), m.phone_local_check("+1 123 456 7890")
    rows.append(("phone_local_valid_us", "yes" if good["status"] in {"valid"} else "no", f"{good['status']} engine={good.get('engine')} type={good.get('line_type')}"))
    rows.append(("phone_local_invalid_us", "yes" if bad["status"] == "invalid" else "no", f"{bad['status']} {bad.get('reason')}"))
    rows.append(("phone_line_live_check", "no", "local check proves format only; needs a phone provider key"))
    print(f"{'check':28} {'works':6} reason")
    for n, w, r in rows:
        print(f"{n:28} {w:6} {r}")
    r = m.verification_readiness()
    print("\nreadiness:", {k: r[k] for k in ("mode", "email_provider_configured", "phone_provider_configured")})
    for w in r["warnings"]:
        print(" warning:", w)
    print("keyed providers:", [k for k in r["set_one_of"] if os.getenv(k)] or "none")


asyncio.run(main())
