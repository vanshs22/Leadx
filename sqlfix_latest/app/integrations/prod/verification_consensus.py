"""Field-level verification consensus for LeadX 14.0."""
from __future__ import annotations
from collections import Counter
from typing import Any

def consensus(observations: list[dict[str,Any]], positive_statuses: set[str] | None = None, negative_statuses: set[str] | None = None) -> dict[str,Any]:
    positive_statuses = positive_statuses or {"valid","deliverable","active","reachable"}
    negative_statuses = negative_statuses or {"invalid","undeliverable","disposable","spamtrap","do_not_mail"}
    obs=[o for o in observations if o]
    positives=[o for o in obs if str(o.get("status","")).lower() in positive_statuses]
    negatives=[o for o in obs if str(o.get("status","")).lower() in negative_statuses]
    independent=len({str(o.get("provider_class") or o.get("provider") or "unknown") for o in obs})
    if len(negatives) > len(positives): status="invalid"
    elif len(positives) > len(negatives): status="valid"
    else: status="unknown"
    base=0.5 if not obs else max(float(o.get("confidence",0) or 0) for o in obs)
    agreement=max(len(positives),len(negatives))/max(1,len(obs))
    conf=min(0.99, 0.5*base+0.35*agreement+0.15*min(1.0, independent/3))
    return {"status":status,"confidence":round(conf,4),"observations":obs,"independent_providers":independent,"positive_count":len(positives),"negative_count":len(negatives)}
