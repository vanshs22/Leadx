"""Signal Agent executor."""
from app.integrations.prod.agent_runtime import execute_agent_task

async def execute(run: dict) -> dict:
    return await execute_agent_task(run, expected_agent="signal")
