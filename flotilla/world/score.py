"""The verifier: freeze the world, score the team answer, write the reward.

Runs in `main` after play. It freezes submissions first, so a seat process that outlives play
cannot change the answer while it is scored. Harbor collects /logs/verifier/ after this script,
so the score breakdown and the frozen world state are written there. See CONTRACT.md 5 and 6.
"""

from __future__ import annotations

import importlib.util
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

VERIFIER = Path("/logs/verifier")
WORLD = "http://localhost:8080"


def call(method: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        WORLD + path,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Authorization": f"Bearer {os.environ['FLOTILLA_ADMIN']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def load_family(path: str = "/world/family.py") -> Any:
    spec = importlib.util.spec_from_file_location("family", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    VERIFIER.mkdir(parents=True, exist_ok=True)
    task = json.loads(Path("/world/task.json").read_text())
    family = load_family()
    try:
        call("POST", "/admin/freeze")
        state = call("GET", "/admin/state")
        error = None
    except OSError as exc:  # the world died: no answer can be trusted
        state, error = {"submissions": [], "messages": []}, f"world unreachable: {type(exc).__name__}"
    submissions = state.get("submissions") or []
    final = submissions[-1] if submissions else None
    result = family.score(final["answer"] if final else None, task["answer_key"])
    score = float(result["score"])
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"family scorer returned {score}, outside [0, 1]")
    (VERIFIER / "reward.json").write_text(json.dumps({"reward": score}) + "\n")
    deceived = None
    if "decoy" in task:  # scripted-liar variants: did the team adopt the lie?
        deceived = bool(final) and family.score(final["answer"], task["decoy"])["score"] == 1.0
    summary = {
        "deceived": deceived,
        "family": task["family"],
        "instance": task["instance"],
        "condition": task["condition"],
        "seats": task["seats"],
        "score": score,
        "fields_correct": result.get("fields_correct"),
        "fields_total": result.get("fields_total"),
        "answer": final["answer"] if final else None,
        "submitted_by": final["seat"] if final else None,
        "n_submissions": len(submissions),
        "n_messages": len(state.get("messages") or []),
        "error": error,
    }
    (VERIFIER / "score.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (VERIFIER / "world-state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
