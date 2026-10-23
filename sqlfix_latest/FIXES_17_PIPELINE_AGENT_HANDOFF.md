# 17 – Pipeline gate + agent hand-off fixes (found with tests/rig)

Run offline, no paid APIs:  `python3 tests/rig/run_pipeline.py -q`, `python3 tests/rig/run_agents.py`, `python3 tests/rig/test_handoff_and_gate.py`
(tests/rig = migration-driven strict fake Supabase: rejects unknown columns/tables, enums, uuids, NOT NULL, FK, unique, missing RPCs.)

1. Gate passed 0 leads: data-quality defaulted to 50 (< gate 55) and free hosts can't SMTP-probe, so no email ever reached "valid".
   -> new `contact_state.py` (single definition; MX-confirmed own-domain email = "probable"; env LEADX_ACCEPT_PROBABLE_EMAIL=off|domain_match|any).
   Phone "invalid" no longer vetoes a good email. Quality derived from real evidence when not recorded.
2. Run result now has `gate_rejections` {reason: count}.
3. Specialized agents received no tenant_id (compliance failed, campaign_optimizer used tenant "None") -> runner injects it.
4. campaign_optimizer awaited a sync function -> to_thread.
5. qualification crashed on tier label 'P4' (used scores["priority"]) -> scores["overall"].
6. qualification never saved scores and agent_runtime never saved lead_account_memory: `.execute()` was missing -> now saved.
7. Hand-off: mission handoff ignored core-agent output nested under "result"; enrichment/research/contact_finder returned no lead_ids;
   qualification now forwards only qualified leads; downstream agents now see data saved by enrichment (overlay of `enrichment` table).
8. fabric enrichment now persists its patch (leads_global email/phone, enrichment.owner_name).
9. revenue_intelligence queried non-existent `meetings` table -> derived from deals.
