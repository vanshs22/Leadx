import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def test_layer_modules_parse():
    for name in ['contracts','discovery_layer','enrichment_layer','research_layer','scoring_layer','persistence_layer','providers','repositories','reliability','telemetry']:
        ast.parse((ROOT/'app/layers'/f'{name}.py').read_text())

def test_pipeline_has_no_private_persistence_implementations():
    text=(ROOT/'app/integrations/prod/pipeline.py').read_text()
    assert 'def _legacy_' not in text
    assert 'from app.layers.persistence_layer import' in text
