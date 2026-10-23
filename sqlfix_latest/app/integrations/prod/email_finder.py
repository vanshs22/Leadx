"""Deterministic work-email candidate generation; verification remains separate."""
from __future__ import annotations
import re
from app.integrations.prod.utils import extract_domain

PATTERNS=("{first}.{last}","{first}","{first}{last}","{f}{last}","{first}{l}","{f}.{last}","{last}.{first}","{last}")

def _slug(s:str)->str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def generate_email_candidates(lead:dict)->list[str]:
    name=str(lead.get("owner_name") or lead.get("contact_name") or "").strip()
    domain=extract_domain(str(lead.get("website") or lead.get("website_domain") or ""))
    if not name or not domain: return []
    parts=[_slug(x) for x in name.split() if _slug(x)]
    if len(parts)<2: return [f"{parts[0]}@{domain}"] if parts else []
    first,last=parts[0],parts[-1]; f=first[0]; l=last[0]
    out=[]
    for pat in PATTERNS:
        local=pat.format(first=first,last=last,f=f,l=l)
        e=f"{local}@{domain}"
        if e not in out: out.append(e)
    return out
