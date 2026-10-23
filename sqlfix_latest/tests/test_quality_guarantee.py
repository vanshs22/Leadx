import pytest

@pytest.mark.asyncio
async def test_quality_guarantee_handler_is_registered():
    from app.integrations.prod import job_handlers
    from app.integrations.prod.jobs import HANDLERS
    assert "quality.guarantee_tick" in HANDLERS
