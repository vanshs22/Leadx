from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / 'app' / 'integrations' / 'prod' / 'pipeline.py'
REALTIME = ROOT.parent / 'lead-crm-portal' / 'lib' / 'crmRealtime.ts'
PIPELINE_UI = ROOT.parent / 'lead-crm-portal' / 'app' / '(portal)' / 'pipeline' / 'page.tsx'


def test_streaming_pipeline_writes_live_run_board_stages():
    text = PIPE.read_text()
    assert '"discovered", lead' in text
    assert '"enriched", lead' in text
    assert '"gated", lead' in text
    assert '"assigned", lead' in text


def test_pipeline_ui_does_not_reload_for_noisy_run_events():
    if not PIPELINE_UI.is_file():
        pytest.skip("backend/core-engine release does not bundle the frontend")
    text = PIPELINE_UI.read_text()
    assert 'tables: ["lead_assignments", "pipeline_stages"]' in text
    assert 'load(true)' in text
    assert 'await crm.changeStage' in text
    assert 'void load(true);' in text


def test_realtime_supports_table_filters():
    if not REALTIME.is_file():
        pytest.skip("backend/core-engine release does not bundle the frontend")
    text = REALTIME.read_text()
    assert 'options?: { tables?: string[] }' in text
    assert 'allowed.includes(detail.table)' in text
