"""LeadX 7.0 Lead Intelligence + Quality + Delivery engine.

Deterministic, evidence-first post-enrichment intelligence.  The module keeps
prediction separate from eligibility: scores prioritize, gates decide whether
a lead can be delivered.  All functions are pure unless explicitly suffixed
with a persistence helper.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Iterable

CAPABILITIES_27 = (
    "multidimensional_lead_score", "field_level_confidence", "truth_resolution",
    "freshness_intelligence", "quality_gate", "smart_assignment", "capacity_assignment",
    "delivery_intelligence", "why_this_lead_explainability", "buying_group_intelligence",
    "account_level_intelligence", "negative_signal_engine", "opportunity_timing",
    "adaptive_icp", "closed_loop_learning", "score_calibration", "autonomous_reverification",
    "lead_state_machine", "quality_guarantee", "provider_intelligence", "cost_aware_enrichment",
    "autonomous_stopping", "lead_value_prediction", "delivery_optimization", "outcome_feedback",
    "evidence_based_revenue_graph", "contact_verification_mesh",
)

STATE_ORDER = ("DISCOVERED","IDENTIFIED","ENRICHING","ENRICHED","RESOLVING","VERIFIED","SCORED","GATED","ASSIGNED","DELIVERY_READY","DELIVERED","CONTACTED","ENGAGED","QUALIFIED","OPPORTUNITY","WON","LOST")
TERMINAL = {"WON", "LOST", "SUPPRESSED", "DUPLICATE", "REJECTED"}
SIDE_STATES = {"NEEDS_REVIEW", "STALE", "REQUIRES_REFRESH", "SUPPRESSED", "DUPLICATE", "REJECTED"}

@dataclass(frozen=True)
class ScoreBundle:
    dimensions: dict[str, float]
    overall: float
    priority: str
    reasons: list[str]
    blockers: list[str]


def _clamp(v: float, lo=0.0, hi=100.0) -> float:
    return round(max(lo, min(hi, float(v))), 2)


def _num(v: Any, default=0.0) -> float:
    try: return float(v)
    except (TypeError, ValueError): return default


def _bool(v: Any) -> bool:
    return v is True or str(v).lower() in {"true","1","yes","valid","verified"}


def score_dimensions(lead: dict, icp: dict | None = None, outcomes: dict | None = None, weights: dict[str, float] | None = None) -> ScoreBundle:
    """27-upgrade #1: separate fit, intent, engagement, contactability, quality,
    readiness, opportunity, freshness and risk instead of one opaque score."""
    icp = icp or {}; outcomes = outcomes or {}; weights = weights or {}
    industry = str(lead.get("industry") or "").lower()
    target_industries = {str(x).lower() for x in icp.get("industries", [])}
    fit = 25 + (45 if industry and industry in target_industries else 0)
    if _num(lead.get("employee_count")) and icp.get("min_employees"):
        fit += 10 if _num(lead["employee_count"]) >= _num(icp["min_employees"]) else -5
    if icp.get("countries") and str(lead.get("country") or "").upper() in {str(x).upper() for x in icp["countries"]}: fit += 15
    fit += min(10, _num(lead.get("firmographic_fit")))
    fit = _clamp(fit)

    intent = _clamp(_num(lead.get("intent_score")) * 1.0 if lead.get("intent_score") is not None else 20)
    trigger_count = len(lead.get("triggers") or lead.get("signals") or [])
    intent = _clamp(intent + min(35, trigger_count * 8) + (15 if lead.get("why_now") else 0))
    engagement = _clamp(_num(lead.get("engagement_score"), 0) + min(40, _num(lead.get("email_opens"),0)*3 + _num(lead.get("replies",0))*15 + _num(lead.get("meetings",0))*25))
    from app.integrations.prod.contact_state import email_level as _el
    _lvl = _el(lead)
    contact = 10 + (35 if lead.get("email_verified") or lead.get("email_status") == "valid" or _lvl == "valid" else 20 if _lvl == "probable" else 0) + (30 if lead.get("phone_verified") else 0) + (15 if lead.get("phone") else 0) + (10 if lead.get("email") else 0)
    contactability = _clamp(contact)
    from app.integrations.prod.contact_state import derive_data_quality
    _dq = lead.get("data_quality_score") if lead.get("data_quality_score") is not None else lead.get("verification_score")
    quality = _clamp(_num(_dq) if _dq is not None else derive_data_quality(lead))
    readiness = _clamp(20 + (30 if lead.get("decision_maker_name") or lead.get("owner_name") else 0) + (25 if lead.get("buying_stage") in {"buy_now","buy_soon"} else 0) + (25 if lead.get("why_now") else 0))
    opportunity = _clamp(0.35*fit + 0.30*intent + 0.20*readiness + 0.15*quality)
    freshness = _clamp(_num(lead.get("freshness_score"), 75))
    risk = _clamp(_num(lead.get("risk_score"), 0))
    value = _clamp(_num(lead.get("lead_value_score"), 0.5*opportunity + 0.5*fit))
    priority = _clamp(0.20*fit + 0.15*intent + 0.10*engagement + 0.15*contactability + 0.10*quality + 0.10*readiness + 0.10*opportunity + 0.05*freshness + 0.05*value - 0.10*risk)
    # Tenant learning adjusts ranking only; hard evidence gates remain intact.
    weighted_lift = 0.0
    if lead.get("email_verified") or lead.get("email_status") == "valid" or _lvl in {"valid", "probable"}:
        weighted_lift += (float(weights.get("email", 1.0)) - 1.0) * 8
    if lead.get("has_booking"):
        weighted_lift += (float(weights.get("booking", 1.0)) - 1.0) * 6
    if _num(lead.get("review_count"), 0) >= 20:
        weighted_lift += (float(weights.get("reviews", 1.0)) - 1.0) * 5
    if lead.get("owner_name") or lead.get("decision_maker_name"):
        weighted_lift += (float(weights.get("owner", 1.0)) - 1.0) * 5
    if _num(lead.get("google_rating"), 0) >= 4.5:
        weighted_lift += (float(weights.get("rating_high", 1.0)) - 1.0) * 5
    priority = _clamp(priority + weighted_lift)
    blockers=[]; reasons=[]
    for name,val in (("ICP fit",fit),("intent",intent),("contactability",contactability),("quality",quality),("readiness",readiness)):
        reasons.append(f"{name}={val:.0f}")
    if contactability < 50: blockers.append("weak_contactability")
    if quality < 55: blockers.append("weak_data_quality")
    if risk >= 60: blockers.append("high_risk")
    tier = "P1" if priority >= 80 else "P2" if priority >= 65 else "P3" if priority >= 45 else "P4"
    return ScoreBundle({"icp_fit":fit,"intent":intent,"engagement":engagement,"contactability":contactability,"data_quality":quality,"buying_readiness":readiness,"opportunity":opportunity,"freshness":freshness,"risk":risk,"lead_value":value,"priority":priority}, priority, tier, reasons, blockers)


def field_confidence(field: str, observations: Iterable[dict]) -> dict:
    """#2/#3: field-level confidence and conflict-aware truth resolution."""
    rows=[x for x in observations if x.get("value") not in (None, "")]
    if not rows: return {"field":field,"value":None,"confidence":0,"conflict":False,"evidence":[]}
    source_weights={"first_party":1.0,"crm":1.0,"verified_api":0.95,"native_provider":0.9,"public_web":0.75,"search":0.65,"inferred":0.35}
    groups={}
    for r in rows:
        key=str(r["value"]).strip().lower(); groups.setdefault(key,[]).append(r)
    ranked=[]
    for key,rs in groups.items():
        score=sum(source_weights.get(str(r.get("source_type") or "public_web"),0.6) * (1.0 if r.get("verified") else 0.7) * (1.0 if r.get("fresh",True) else 0.5) for r in rs)
        ranked.append((score,key,rs))
    ranked.sort(reverse=True)
    best_score,key,rs=ranked[0]
    total=sum(x[0] for x in ranked) or 1
    confidence=_clamp(100*(best_score/total)*(1-math.exp(-len(rs)/2)))
    return {"field":field,"value":rs[0].get("value"),"confidence":confidence,"conflict":len(groups)>1,"alternatives":[x[1] for x in ranked[1:]],"evidence":rs}


def freshness_profile(lead: dict, now: datetime | None=None) -> dict:
    """#4: per-field TTL instead of one lead-wide freshness flag."""
    now=now or datetime.now(timezone.utc); observed=lead.get("field_observed_at") or {}
    ttl_days={"email":14,"phone":14,"owner_name":30,"title":30,"website":30,"employee_count":45,"revenue":60,"intent_score":7,"signals":3,"linkedin_url":60,"address":90}
    out={}; stale=[]
    for field,days in ttl_days.items():
        raw=observed.get(field)
        if not raw: age=None; fresh=False
        else:
            try: age=(now-datetime.fromisoformat(str(raw).replace("Z","+00:00"))).total_seconds()/86400; fresh=age<=days
            except Exception: age=None; fresh=False
        out[field]={"fresh":fresh,"age_days":None if age is None else round(age,1),"ttl_days":days,"next_refresh":(now+timedelta(days=max(0,days-(age or 0)))).isoformat()}
        if not fresh: stale.append(field)
    return {"fields":out,"stale_fields":stale,"freshness_score":_clamp(100*(len(ttl_days)-len(stale))/len(ttl_days))}


def quality_gate(lead: dict, scores: ScoreBundle | None=None, require_email=False, require_phone=False) -> dict:
    """#5/#6: hard eligibility gates remain separate from prioritization."""
    s=scores or score_dimensions(lead); reasons=[]
    if str(lead.get("business_status") or "").upper()=="CLOSED": reasons.append("closed_business")
    if lead.get("suppressed") or lead.get("opted_out"): reasons.append("suppressed_or_opted_out")
    if lead.get("duplicate_of"): reasons.append("duplicate")
    if s.dimensions["data_quality"] < 55: reasons.append("quality_below_gate")
    if s.dimensions["risk"] >= 70: reasons.append("risk_above_gate")
    from app.integrations.prod.contact_state import email_ok as _email_ok, phone_ok as _phone_ok
    email_verified = _email_ok(lead)
    phone_verified = _phone_ok(lead)
    if require_email and not email_verified: reasons.append("verified_email_required")
    if require_phone and not phone_verified: reasons.append("verified_phone_required")
    contact_ok = email_verified or phone_verified
    if not contact_ok: reasons.append("no_verified_contact")
    decision="PASS" if not reasons else "REVIEW" if len(reasons)<=1 and s.overall>=60 else "REJECT"
    return {"decision":decision,"eligible":decision=="PASS","reasons":reasons,"score":s.overall}


def assignment_plan(lead: dict, reps: list[dict], rules: dict | None=None) -> dict:
    """#7/#8: territory, specialization, capacity and historical performance."""
    rules=rules or {}; candidates=[]
    for rep in reps:
        if rep.get("active") is False: continue
        if rules.get("territories") and lead.get("country") and lead.get("country") not in set(rep.get("territories") or []): continue
        if rep.get("max_open_leads") is not None and _num(rep.get("open_leads")) >= _num(rep.get("max_open_leads")): continue
        score=0.0
        if lead.get("industry") in (rep.get("verticals") or []): score+=35
        if lead.get("country") in (rep.get("territories") or []): score+=25
        score+=max(0,20-_num(rep.get("open_leads"))*0.5)
        score+=min(20,_num(rep.get("historical_conversion_rate"))*20)
        score+=min(10,_num(rep.get("response_sla_score")))
        candidates.append((score,rep))
    candidates.sort(key=lambda x:x[0], reverse=True)
    return {"assigned_to":candidates[0][1].get("id") if candidates else None,"score":round(candidates[0][0],2) if candidates else 0,"candidates":[{"rep_id":r.get("id"),"score":round(s,2)} for s,r in candidates[:10]],"reason":"capacity+territory+vertical+performance"}


def delivery_decision(lead: dict, gate: dict, scores: ScoreBundle) -> dict:
    """#9/#24/#25: decide deliver/hold/research and stop enrichment when marginal value falls."""
    if not gate.get("eligible"): return {"action":"hold","reason":gate.get("reasons")}
    if scores.dimensions["priority"] >= 80: return {"action":"deliver_now","reason":"P1 high-value verified lead"}
    if scores.dimensions["intent"] >= 70 and scores.dimensions["freshness"] < 60: return {"action":"refresh_then_deliver","reason":"high intent but stale evidence"}
    return {"action":"deliver","reason":"passed hard gates"}


def buying_group(people: list[dict]) -> dict:
    """#10: map buying roles without assuming a single decision maker."""
    roles={}
    weights={"economic_buyer":1.0,"decision_maker":.95,"technical_buyer":.8,"champion":.85,"influencer":.65,"user":.5}
    for p in people:
        role=str(p.get("buying_role") or "unknown").lower(); score=_num(p.get("role_confidence"),50)*weights.get(role,0.4)
        roles.setdefault(role,[]).append({"person_id":p.get("id"),"score":round(score,2),"name":p.get("name")})
    return {"roles":roles,"coverage":_clamp(len([r for r in roles if r!="unknown"])/6*100),"missing_roles":[r for r in weights if r not in roles]}


def account_intelligence(lead: dict, people: list[dict] | None=None) -> dict:
    """#11/#27: account/person/opportunity intelligence joins into revenue graph."""
    people=people or []; s=score_dimensions(lead)
    bg=buying_group(people)
    account_score=_clamp(.55*s.dimensions["icp_fit"]+.25*s.dimensions["intent"]+.20*s.dimensions["data_quality"])
    opportunity_score=_clamp(.45*account_score+.30*s.dimensions["buying_readiness"]+.25*bg["coverage"])
    return {"account_score":account_score,"person_score":s.overall,"buying_group_score":bg["coverage"],"opportunity_score":opportunity_score,"buying_group":bg}


def negative_signals(lead: dict) -> list[dict]:
    """#12: explicit suppression/risk signals."""
    checks=[("closed",str(lead.get("business_status") or "").upper()=="CLOSED",-100),("opt_out",bool(lead.get("opted_out")),-100),("duplicate",bool(lead.get("duplicate_of")),-80),("invalid_email",lead.get("email_status")=="invalid",-70),("risky_phone",lead.get("phone_risk") in {"high","fraud"},-70),("person_left",bool(lead.get("person_left")),-65),("territory_mismatch",bool(lead.get("territory_mismatch")),-50),("recently_contacted",bool(lead.get("recently_contacted")),-25),("competitor_customer",bool(lead.get("competitor_customer")),-35)]
    return [{"signal":n,"active":a,"impact":i} for n,a,i in checks if a]


def opportunity_timing(lead: dict, scores: ScoreBundle | None=None) -> dict:
    """#13: fit + intent + trigger + freshness → timing band."""
    s=scores or score_dimensions(lead); intent=s.dimensions["intent"]; fit=s.dimensions["icp_fit"]
    if fit>=75 and intent>=75 and lead.get("why_now"): stage="BUY_NOW"
    elif fit>=70 and intent>=55: stage="BUY_SOON"
    elif fit>=55: stage="WATCH"
    else: stage="NURTURE"
    return {"stage":stage,"fit":fit,"intent":intent,"trigger":bool(lead.get("why_now"))}


def adaptive_icp(base: dict, outcomes: list[dict]) -> dict:
    """#14: update weights/thresholds from observed wins, not intuition."""
    if not outcomes: return dict(base)
    wins=[x for x in outcomes if str(x.get("outcome")).lower() in {"won","opportunity","meeting"}]
    out=dict(base); out["learned_sample_size"]=len(outcomes); out["positive_rate"]=round(len(wins)/len(outcomes),4)
    industries={}
    for x in wins: industries[str(x.get("industry") or "unknown").lower()]=industries.get(str(x.get("industry") or "unknown").lower(),0)+1
    out["learned_industries"]=[k for k,v in sorted(industries.items(),key=lambda z:z[1],reverse=True)[:10]]
    return out


def learning_update(events: list[dict]) -> dict:
    """#15/#26: outcome feedback into deterministic priors."""
    total=len(events); positive=sum(1 for e in events if str(e.get("outcome") or "").lower() in {"replied","meeting","opportunity","won"})
    won=sum(1 for e in events if str(e.get("outcome") or "").lower()=="won")
    return {"samples":total,"positive_rate":round(positive/total,4) if total else 0,"win_rate":round(won/total,4) if total else 0,"signals":sorted({str(e.get("signal")) for e in events if e.get("signal")})}


def calibrate_scores(rows: list[dict]) -> dict:
    """#16: observed conversion by score band."""
    bands={"0-39":[],"40-59":[],"60-79":[],"80-100":[]}
    for r in rows:
        s=_num(r.get("score")); b="0-39" if s<40 else "40-59" if s<60 else "60-79" if s<80 else "80-100"; bands[b].append(r)
    return {b:{"n":len(v),"positive_rate":round(sum(1 for x in v if x.get("positive"))/len(v),4) if v else 0} for b,v in bands.items()}


def reverify_plan(lead: dict, now: datetime | None=None) -> dict:
    """#17: value/freshness-aware re-verification."""
    s=score_dimensions(lead); freq=7 if s.dimensions["lead_value"]>=80 else 30 if s.dimensions["lead_value"]>=55 else 90
    stale=freshness_profile(lead,now)["stale_fields"]
    return {"cadence_days":freq,"refresh_now":stale,"reason":"high-value" if freq==7 else "standard" if freq==30 else "low-priority"}


def transition(current: str, event: str, *, gate_passed=False, assigned=False) -> str:
    """#18: explicit state machine; invalid transitions never silently advance."""
    current=str(current or "DISCOVERED").upper(); event=event.lower()
    table={
        "discover":("DISCOVERED","IDENTIFIED"),"identify":("IDENTIFIED","ENRICHING"),"enrich":("ENRICHING","ENRICHED"),
        "resolve":("ENRICHED","RESOLVING"),"verify":("RESOLVING","VERIFIED"),"score":("VERIFIED","SCORED"),
        "gate":("SCORED","GATED"),"assign":("GATED","ASSIGNED"),"ready":("ASSIGNED","DELIVERY_READY"),
        "deliver":("DELIVERY_READY","DELIVERED"),"contact":("DELIVERED","CONTACTED"),"engage":("CONTACTED","ENGAGED"),
        "qualify":("ENGAGED","QUALIFIED"),"opportunity":("QUALIFIED","OPPORTUNITY"),"won":("OPPORTUNITY","WON"),"lost":("OPPORTUNITY","LOST"),
        "reject":("GATED","REJECTED"),"suppress":("GATED","SUPPRESSED"),"duplicate":("GATED","DUPLICATE"),"review":(current,"NEEDS_REVIEW"),
    }
    src,dst=table.get(event,(None,None))
    if src is None: raise ValueError(f"unknown lead event: {event}")
    if src!=current and not (event=="review" and src==current): raise ValueError(f"invalid transition {current} -> {event}")
    if event=="gate" and not gate_passed: return "NEEDS_REVIEW"
    if event=="assign" and not assigned: return "NEEDS_REVIEW"
    return dst


def quality_guarantee(lead: dict, delivered: bool, outcome: str | None=None) -> dict:
    """#19: root-cause quality feedback for bad-lead replacement/credit workflows."""
    bad=(outcome or "").lower() in {"invalid","bad","bounce","wrong_person","closed","duplicate"}
    reasons=negative_signals(lead)
    return {"eligible_for_credit":bool(delivered and bad),"root_causes":[x["signal"] for x in reasons],"replacement_needed":bool(delivered and bad)}


def provider_intelligence(observations: list[dict]) -> dict:
    """#20: field/provider hit-rate, quality, latency and cost telemetry."""
    agg={}
    for o in observations:
        p=str(o.get("provider") or "unknown"); a=agg.setdefault(p,{"calls":0,"hits":0,"accepted":0,"latency_ms":0,"cost":0.0}); a["calls"]+=1; a["hits"]+=int(bool(o.get("hit"))); a["accepted"]+=int(bool(o.get("accepted"))); a["latency_ms"]+=_num(o.get("latency_ms")); a["cost"]+=_num(o.get("cost"))
    for p,a in agg.items(): a["hit_rate"]=round(a["hits"]/a["calls"],4) if a["calls"] else 0; a["accept_rate"]=round(a["accepted"]/a["calls"],4) if a["calls"] else 0; a["avg_latency_ms"]=round(a["latency_ms"]/a["calls"],1) if a["calls"] else 0
    return agg


def cost_aware_provider_order(providers: list[dict], field: str, lead: dict) -> list[dict]:
    """#21: rank provider by expected value per cost rather than fixed waterfall."""
    value=_num(lead.get("lead_value_score"),50); rows=[]
    for p in providers:
        if field not in (p.get("capabilities") or []): continue
        hit=_num(p.get("field_hit_rate"),.5); acc=_num(p.get("field_accept_rate"),.5); cost=max(.0001,_num(p.get("cost"),1)); lat=max(1,_num(p.get("latency_ms"),500))
        rows.append((hit*acc*max(1,value)/(cost*(1+lat/5000)),p))
    return [p for _,p in sorted(rows,key=lambda x:x[0],reverse=True)]


def should_keep_searching(lead: dict, evidence_count: int, budget_remaining: float) -> dict:
    """#22: stop when expected marginal evidence is below cost/risk threshold."""
    s=score_dimensions(lead); uncertainty=max(0,100-_num(lead.get("data_quality_score"),50)); expected_gain=uncertainty/(evidence_count+1); value=s.dimensions["lead_value"]
    keep=bool(budget_remaining>0 and expected_gain>=8 and value>=35 and evidence_count<20)
    return {"continue":keep,"expected_gain":round(expected_gain,2),"reason":"material_uncertainty" if keep else "diminishing_returns"}


def lead_value_prediction(lead: dict, scores: ScoreBundle | None=None) -> float:
    """#23: expected revenue value, distinct from quality and priority."""
    s=scores or score_dimensions(lead); deal=_num(lead.get("expected_deal_value"),_num(lead.get("deal_size"),1)); close_prob=_clamp(.55*s.dimensions["opportunity"]+.25*s.dimensions["intent"]+.20*s.dimensions["icp_fit"],0,100)/100
    return round(deal*close_prob,2)


def optimize_delivery(leads: list[dict], reps: list[dict]) -> list[dict]:
    """#24: joint lead-to-rep assignment/delivery objective."""
    available=list(reps); out=[]
    for lead in sorted(leads,key=lambda x:_num(x.get("priority_score",x.get("score",0))),reverse=True):
        plan=assignment_plan(lead,available); out.append({"lead_id":lead.get("id"),"assigned_to":plan["assigned_to"],"priority":lead.get("priority_score"),"reason":plan["reason"]})
        for r in available:
            if r.get("id")==plan["assigned_to"]: r["open_leads"]=_num(r.get("open_leads"))+1
    return out


def evaluate_lead(lead: dict, icp: dict | None=None, reps: list[dict] | None=None, weights: dict[str, float] | None = None) -> dict:
    """Full 7.0 pass: evidence → score → gates → timing → assignment → delivery."""
    scores=score_dimensions(lead,icp,weights=weights); freshness=freshness_profile(lead); gate=quality_gate(lead,scores); timing=opportunity_timing(lead,scores); delivery=delivery_decision(lead,gate,scores); assignment=assignment_plan(lead,reps or []) if delivery["action"]!="hold" else {"assigned_to":None}
    return {"scores":scores.__dict__,"freshness":freshness,"negative_signals":negative_signals(lead),"gate":gate,"timing":timing,"assignment":assignment,"delivery":delivery,"lead_value":lead_value_prediction(lead,scores),"explainability":{"why_this_lead":scores.reasons,"blockers":scores.blockers}}


def capabilities() -> list[str]: return list(CAPABILITIES_27)
