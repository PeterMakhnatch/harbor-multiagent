from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import replay_fidelity_calendar, replay_fidelity_dispatch, replay_fidelity_score

LOADERS = {"dispatch": replay_fidelity_dispatch.load, "calendar": replay_fidelity_calendar.load}
TASK_LIMITS = {"dispatch": replay_fidelity_dispatch.LIMITS, "calendar": replay_fidelity_calendar.LIMITS}
REGISTRATION = "60e2fa148e6d2a9ad7d85deffce03b8ba25e77a5"
CODE = ("replay_fidelity.py", "replay_fidelity_calendar.py", "replay_fidelity_dispatch.py",
        "replay_fidelity_evidence.py", "replay_fidelity_score.py")
LIMITS = (
    "The prompt digest covers the receiver's user message, not a complete provider envelope or a backend version.",
    "Each world contributes one native receiver draw, not two independent native teams.",
    "This is conditional on a fixed recorded transcript. It does not evaluate new upstream seat behavior.",
    "No warning means screen only. It does not establish equivalence or replace native team confirmation.",
)


def audit(root: Path, task: str) -> dict[str, Any]:
    tasks = tuple(LOADERS) if task == "both" else (task,)
    if not root.is_dir() or any(name not in LOADERS for name in tasks):
        raise ValueError("provide a result directory and a registered task")
    reports: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    draws: set[str] = set()
    for name in tasks:
        rows, inputs = LOADERS[name](root)
        result = replay_fidelity_score.analyze(rows)
        identities = []
        for row in sorted(rows, key=lambda item: item["world"]):
            attempts = [row["native"], *row["replays"]]
            for attempt in attempts:
                draw = attempt["id"]
                if draw in draws:
                    raise ValueError(f"duplicate draw across inputs: {draw}")
                draws.add(draw)
            identities.append({"world": row["world"], "source_result_sha256": row["source_result_sha256"],
                               "native": row["native"], "replays": row["replays"]})
        for artifact, digest in inputs.items():
            if hashes.setdefault(artifact, digest) != digest:
                raise ValueError(f"two readers observed different bytes in {artifact}")
        reports[name] = {**result, "draw_identities": identities, "input_files": sorted(inputs),
                         "identity_limits": TASK_LIMITS[name]}
    code_root = Path(__file__).resolve().parent
    return {"schema": "seatlab.receiver-replay-fidelity/v1", "registration_commit": REGISTRATION,
            "new_model_calls": 0, "new_provider_cost_usd": "0", "draws": len(draws),
            "limits": LIMITS, "tasks": reports, "input_sha256": hashes,
            "code_sha256": {name: hashlib.sha256((code_root / name).read_bytes()).hexdigest() for name in CODE}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check receiver replay grades against native draws without an API key.",
                                     allow_abbrev=False)
    parser.add_argument("results", type=Path)
    parser.add_argument("--task", choices=(*LOADERS, "both"), default="both")
    args = parser.parse_args(argv)
    try:
        report = audit(args.results, args.task)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        parser.exit(2, f"receiver replay not evaluable: {exc}\n")
    print(json.dumps(report, sort_keys=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
