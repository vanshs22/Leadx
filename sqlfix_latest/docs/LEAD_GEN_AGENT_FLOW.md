# Lead-gen agent flow

LeadX has 31 registry agents (`app/integrations/prod/agent_registry.py`). 27 of them take part in lead
generation. Every one of those 27 now runs, or is recorded as skipped with a real reason, through one of three
entry points:

1. A normal pipeline run (`pipeline.run_pipeline_parallel_target`).
2. The daily-target campaign run (`autonomous_campaigns.run`).
3. Inbound reply handling (`inbound_email.handle_reply`).

The classification lives in `lead_generation_orchestration.py` (`LEAD_GEN_AGENT_FLOW`, `NON_LEAD_GEN_AGENTS`,
`POST_DELIVERY_AGENTS`, `POST_DELIVERY_LEADGEN_AGENTS`). `validate_agent_classification()` fails if a registry
agent is unclassified or classified twice.

## Where an agent runs

| Mode | Meaning | Evidence |
|---|---|---|
| `inline_batch` | Once per run, at run level | Aggregated in the in-memory stage context, one `agent_runs` row per agent written in a single bulk insert at the end of the run (`created_by=pipeline-stage-evidence`) |
| `inline_lead` | Per lead inside the enrichment/assignment workers, CPU only or reusing work the engine already does | Same aggregated row, counts per operation |
| `post_delivery` | Queued `agent.run` job after delivery (`post_run_agent_dispatch.py`) | A real `agent_runs` row per agent (`created_by=pipeline-post-delivery`), executed by the worker |
| `campaign` | Daily-target campaign run or inbound reply | One bulk insert per campaign run or per reply (`created_by=autonomous-campaign` / `inbound-reply`) |

Evidence never adds per-lead database writes or network calls to a run. A failed evidence insert is logged and
ignored; it never fails a run, a campaign, or reply handling.

## Ordered flow of a normal run

Each step hands its output to the next. The ICP planner is always the first recorded agent.

1. `icp` (`icp_planning`): `lead_selection_planner.build_selection_decision` builds tier policy, route cells and tranche size.
2. `prospecting` (`request_normalization`): normalizes vertical, locations and target.
3. `source_router` (`source_selection`) and `discovery_planner` (`route_and_tranche_planning`): pick sources and run discovery rounds over the planner's route cells.
4. `identity_resolution` (`dedupe_and_identity`): dedupes each discovered batch.
5. `compliance` (`suppression_filter`): drops suppressed and opted-out entities before enrichment.
6. `enrichment` (`worker:N`): the enrichment engine fills contact and company fields per lead.
7. `email_finder`, `social_intelligence`, `intent`, `trigger`, `signal`: pure CPU per lead (`run_local_lead_agents`): email candidates, social identities, opportunity intelligence.
8. `icp` (`icp_fit`), `verification`, `deliverability`, `data_quality`, `qualification`: `lead6_runtime.prepare_lead` verification and scoring, contactability risk, and the quality gate.
9. `routing` (`assignment_policy`) and `crm` (`assignment_projection`, `delivery_confirmation`): assign to the tenant, project to CRM, deliver.
10. Post-delivery, queued concurrently with lead ids of the delivered leads (`leads_global.id`, capped at 500):
    `crm_hygiene`, `deal`, `revenue`, `cost_optimizer`, `campaign_optimizer`, `learning`, and the heavy lead-gen
    agents `contact_finder`, `research`, `web_research` (capped by `LEADX_POST_DELIVERY_RESEARCH_LIMIT`, default 25).
    Idempotency key: `pipeline:<run_id>:agent:<agent_id>`.

`personalization` is skipped with `not_needed_for_portal_delivery` on portal delivery. `outreach` is skipped
with `requires_explicit_outreach_request` in a normal run; it runs in the campaign lane.

### Post-delivery research rules

- `LEADX_SPECIALIST_AGENTS=inline`: the specialist branch already ran these agents per lead, so `contact_finder`,
  `research` and `web_research` are skipped with `inline_mode_already_ran`.
- `LEADX_POST_DELIVERY_RESEARCH_LIMIT=0`: skipped with `research_limit_0`.
- No delivered leads: skipped with `no_delivered_leads`.
- Cost: delivered leads already passed the verified-contact gate, so under the free-first policy (`LEADX_PAID_FALLBACK=auto`,
  see `CHANGES_16_FREE_FIRST.md`) there is no gap to fill. Paid lanes run only when `LEADX_PAID_FALLBACK=always`:
  - `research` runs `deep_research_lead(allow_paid=False)`: website crawl without Firecrawl, social and LinkedIn company lanes; the Serper search lane is dropped.
  - `web_research` crawls first-party pages with `use_firecrawl=False`, every authorized lead up to the cap, 4 at a time.
  - `contact_finder` only has a paid lane (Serper LinkedIn search), so it is skipped with `paid_fallback_not_always`.
- The run's `agent_execution` shows each of the three as `post_delivery_queued` with its `agent_run_id` and `job_id`, or as skipped with the reason. A queued agent does not get a second evidence row; its own `agent_runs` row holds the result.

## Campaign lane

`autonomous_campaigns.run()` runs the pipeline (`campaign_mode=quality_campaign`), enrolls the assigned leads in the
sequence, then writes one bulk insert with `source_run_id` set to the pipeline run id:

- `personalization` (`ai_copy_generation`, only when `generate_ai_copy` is on): enrolled count, approval drafts.
- `outreach` (`sequence_enrollment`): enrolled, `sequence_id`, `campaign_id`.
- `compliance` (`send_policy_gates`): suppression, sender profile and send window gates, enforced at send time.

`inbound_email.handle_reply()` writes one bulk insert per reply after its existing logic:

- `outreach` (`reply_handling`): reply category.
- On opt-out, also `compliance` (`opt_out`, suppression added) and `learning` (`feedback_signal`).

## All 31 registry agents

Before = state at commit `9ad41d8`, before this work. After = this branch.

| Agent | Role | Lead-gen | Before | After | Where it runs |
|---|---|---|---|---|---|
| icp | Evaluates account fit against tenant criteria | yes | declared only (planner ran but was not recorded as icp) | wired, first agent | inline_batch (planning) and inline_lead (fit) |
| prospecting | Normalizes the prospecting request | yes | wired | wired | inline_batch |
| source_router | Selects and ranks executable sources | yes | wired | wired | inline_batch |
| discovery_planner | Runs discovery over the planned route cells | yes | wired | wired | inline_batch |
| identity_resolution | Dedupes cross-source entities | yes | wired | wired | inline_batch |
| compliance | Suppression, consent and approval checks | yes | declared only | wired | inline_batch (suppression) and campaign |
| enrichment | Fills contact and company fields | yes | wired | wired | inline_lead |
| contact_finder | Finds decision-maker candidates | yes | declared only | wired, paid-gated | post_delivery |
| email_finder | Builds candidate work emails | yes | declared only | wired | inline_lead |
| research | Deep account research with provenance | yes | declared only | wired, free lanes by default | post_delivery |
| web_research | First-party web research | yes | declared only (fabric handled one lead) | wired, all leads up to the cap | post_delivery |
| social_intelligence | Normalizes public social evidence | yes | declared only | wired | inline_lead |
| verification | Contact field verification | yes | wired | wired | inline_lead |
| deliverability | Contactability and delivery risk | yes | declared only | wired | inline_lead |
| data_quality | Completeness and gate readiness | yes | wired | wired | inline_lead |
| intent | Buying-intent signals | yes | declared only | wired | inline_lead |
| trigger | Business-change triggers | yes | declared only | wired | inline_lead |
| signal | Explainable signals | yes | declared only | wired | inline_lead |
| qualification | Quality gate and opportunity intelligence | yes | wired | wired | inline_lead |
| routing | Assignment routing | yes | wired | wired | inline_lead |
| crm | Assignment, CRM projection, delivery | yes | wired | wired | inline_lead |
| personalization | Evidence-backed personalization inputs | yes | declared only | wired (skipped on portal delivery) | campaign |
| outreach | Sequence enrollment and reply handling | yes | declared only | wired | campaign |
| learning | Learns from outcomes | yes | post-delivery queued with assignment ids (no leads found) | wired with lead ids, plus reply feedback | post_delivery and campaign |
| cost_optimizer | Cost per successful field | yes | post-delivery queued with assignment ids | wired with lead ids | post_delivery |
| campaign_optimizer | Campaign outcome analysis | yes | post-delivery queued with assignment ids | wired with lead ids | post_delivery |
| crm_hygiene | Stale, duplicate, incomplete CRM records | yes | post-delivery queued with assignment ids | wired with lead ids | post_delivery |
| gtm | Mission owner that delegates to specialists | no | unchanged | unchanged | missions only |
| deal | Deal health and risks | no | post-delivery queued with assignment ids | still queued post-delivery, now with lead ids | post_delivery (existing) |
| revenue | Funnel and revenue analysis | no | post-delivery queued with assignment ids | still queued post-delivery, now with lead ids | post_delivery (existing) |
| experiment | Provider and waterfall experiments | no | unchanged | unchanged | on demand only |

## Non-lead-gen agents left unchanged

`gtm`, `deal`, `revenue`, `experiment`. Their logic is not modified. `deal` and `revenue` were already in the
post-delivery dispatch, so they now get the same corrected lead ids as the other post-delivery agents.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `LEADX_SPECIALIST_AGENTS` | `deferred` | `inline` runs the specialist branch per lead and skips post-delivery research |
| `LEADX_POST_DELIVERY_RESEARCH_LIMIT` | `25` | Leads per post-delivery research agent; `0` disables |
| `LEADX_PAID_FALLBACK` | `auto` | `always` lets post-delivery research use Serper and Firecrawl and enables `contact_finder` |
