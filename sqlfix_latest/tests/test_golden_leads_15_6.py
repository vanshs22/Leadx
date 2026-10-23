import json
from pathlib import Path
from app.integrations.prod.scoring import score_lead_v3

def test_golden_leads_tiers():
    rows=json.loads((Path(__file__).parent/'data'/'golden_leads.json').read_text())
    assert len(rows)==50
    for row in rows:
        _, tier, _, _ = score_lead_v3(row['lead'])
        assert tier == row['label'], row['id']
