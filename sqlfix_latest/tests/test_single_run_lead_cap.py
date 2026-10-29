"""Focused contract for the hard 500-lead single-run limit."""

import pytest
from pydantic import ValidationError

from app.core.runtime_config import MAX_LEADS_PER_RUN
from app.api.prod.leads_api import DiscoveryApprovalRequest, PipelineRequest
from app.api.prod.ops_api import OperatorProspectRequest, ScheduleRequest, ScheduleUpdate
from app.crm.crm_api import DiscoveryRequestBody, SearchRunBody


def test_single_run_cap_is_shared_and_accepts_exact_limit():
    assert MAX_LEADS_PER_RUN == 500
    assert PipelineRequest(tenant_id="tenant", target_new_leads=500).target_new_leads == 500
    assert DiscoveryRequestBody(vertical="gym", requested_leads=500).requested_leads == 500
    assert SearchRunBody(vertical="gym", target_new_leads=500).target_new_leads == 500
    assert OperatorProspectRequest(target_new_leads=500).target_new_leads == 500
    assert ScheduleRequest(tenant_id="tenant", name="daily", industry="gym", target_new_leads=500).target_new_leads == 500
    assert ScheduleUpdate(target_new_leads=500).target_new_leads == 500


@pytest.mark.parametrize(
    "model, field",
    [
        (PipelineRequest, "target_new_leads"),
        (DiscoveryApprovalRequest, "approved_leads"),
        (DiscoveryRequestBody, "requested_leads"),
        (SearchRunBody, "target_new_leads"),
        (OperatorProspectRequest, "target_new_leads"),
        (ScheduleRequest, "target_new_leads"),
        (ScheduleUpdate, "target_new_leads"),
    ],
)
def test_single_run_cap_rejects_501(model, field):
    values = {field: 501}
    if model is PipelineRequest:
        values["tenant_id"] = "tenant"
    elif model is DiscoveryApprovalRequest:
        values.update(reason="approved")
    elif model is DiscoveryRequestBody:
        values["vertical"] = "gym"
    elif model is SearchRunBody:
        values["vertical"] = "gym"
    elif model is ScheduleRequest:
        values.update(tenant_id="tenant", name="daily", industry="gym")
    with pytest.raises(ValidationError):
        model(**values)
