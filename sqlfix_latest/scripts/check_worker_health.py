"""Validate durable worker health evidence for the container healthcheck."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path


def _timestamp(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


path = Path(os.getenv("WORKER_HEALTH_FILE", "/tmp/leadx-worker-heartbeat"))
if not path.exists():
    raise SystemExit("worker health evidence missing")
try:
    evidence = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit("worker health evidence invalid") from None

worker_id = evidence.get("worker_id")
status = evidence.get("status")
if not worker_id:
    raise SystemExit("worker health evidence missing worker_id")
if status not in {"ready", "healthy"}:
    raise SystemExit("worker health evidence unready")
if evidence.get("queue_capability") is not True:
    raise SystemExit("worker queue capability unavailable")
if evidence.get("reclaim_capability") is not True:
    raise SystemExit("worker reclaim capability unavailable")
if "last_terminal_error_category" not in evidence:
    raise SystemExit("worker terminal error evidence missing")

observed_epoch = _timestamp(evidence.get("observed_at"))
queue_checked_epoch = _timestamp(evidence.get("queue_checked_at"))
reclaim_checked_epoch = _timestamp(evidence.get("reclaim_checked_at"))
if not queue_checked_epoch:
    raise SystemExit("worker queue capability timestamp missing")
if not reclaim_checked_epoch:
    raise SystemExit("worker reclaim capability timestamp missing")
if not observed_epoch:
    observed_epoch = path.stat().st_mtime
if time.time() - min(observed_epoch, queue_checked_epoch, reclaim_checked_epoch) > 120:
    raise SystemExit("worker health evidence stale")

print("worker health: PASS")
