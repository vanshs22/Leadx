from app.api.prod import platform_api
from app.memory import supabase_client


class _Q:
    def __init__(self, rows):
        self.rows, self.f = rows, {}

    def select(self, *_a): return self
    def or_(self, _e): return self
    def order(self, *_a, **_k): return self
    def limit(self, _n): return self
    def eq(self, c, v): self.f[c] = v; return self

    def execute(self):
        data = [r for r in self.rows if all(r.get(c) == v for c, v in self.f.items())]
        return type('R', (), {'data': data})()


class _DB:
    def __init__(self, rows): self.rows = rows
    def table(self, _n): return _Q(self.rows)


def _ev(i, run, order):
    return {'id': i, 'agent_id': f'a{i}', 'created_by': 'pipeline-stage-evidence', 'input_data': {'source_run_id': run, 'order': order}}


def test_latest_flow_grouped_ordered_and_runs_exclude_evidence(monkeypatch):
    rows = [_ev(1, 'new', 2), _ev(2, 'new', 1), _ev(3, 'old', 1), {'id': 4, 'agent_id': 'x', 'created_by': None}]
    monkeypatch.setattr(supabase_client, 'get_supabase', lambda: _DB(rows))
    out = platform_api.agents(admin=None)
    assert [r['id'] for r in out['latest_pipeline_flow']] == [2, 1]
    assert [r['id'] for r in out['runs']] == [4]
