"""Precision/recall per field over the golden pages (tests/golden/build_golden.py). A release must not fall below these floors."""
from tests.golden.build_golden import cases
from app.integrations.prod.field_extract import extract_all

FLOORS = {"email": (0.99, 0.95), "phone": (0.99, 0.95), "social": (0.99, 0.93)}


def _score(got: set, want: set):
    tp = len(got & want)
    return tp, len(got - want), len(want - got)


def test_golden_set_precision_and_recall():
    tot = {k: [0, 0, 0] for k in FLOORS}
    misses: list[str] = []
    cs = cases()
    assert len(cs) >= 90
    for c in cs:
        r = extract_all(c["html"], c["url"])
        got = {"email": {e["value"] for e in r["emails"]}, "phone": {p["value"] for p in r["phones"]},
               "social": {f"{k}:{v['handle'].lstrip('@')}" for k, v in r["social"].items() if k != "whatsapp"}}
        want = {"email": {e.lower() for e in c["emails"]}, "phone": c["phones"], "social": {f"{k}:{v.lstrip('@')}" for k, v in c["social"].items()}}
        assert not (got["email"] & {e.lower() for e in c["bad_emails"]}), (c["id"], "forbidden email extracted", got["email"] & c["bad_emails"])
        for f in FLOORS:
            tp, fp, fn = _score(got[f], want[f])
            tot[f][0] += tp; tot[f][1] += fp; tot[f][2] += fn
            if fp or fn:
                misses.append(f"{c['id']} {f}: extra={sorted(got[f] - want[f])} missing={sorted(want[f] - got[f])}")
    for f, (pmin, rmin) in FLOORS.items():
        tp, fp, fn = tot[f]
        precision, recall = tp / max(1, tp + fp), tp / max(1, tp + fn)
        print(f"{f}: precision={precision:.3f} recall={recall:.3f} (tp={tp} fp={fp} fn={fn})")
        assert precision >= pmin and recall >= rmin, (f, precision, recall, misses[:12])
