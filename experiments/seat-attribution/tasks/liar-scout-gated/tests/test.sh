#!/bin/bash
python3 - <<'PY'
import json
from pathlib import Path

try:
    events = [json.loads(l) for l in Path("/logs/world/events.jsonl").read_text().splitlines() if l.strip()]
    code = Path("/logs/world/code.txt").read_text().strip()
except (OSError, ValueError):
    events, code = [], ""
answers = [e for e in events if e.get("event") == "answer"]
reward = int(bool(answers) and answers[-1].get("code") == code)
Path("/logs/verifier/reward.txt").write_text(f"{reward}\n")
PY
