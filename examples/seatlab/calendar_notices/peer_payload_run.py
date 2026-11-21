import argparse
import os
from pathlib import Path
from typing import Any

from . import peer_payload
from . import protocol as p
from .presentation_run import _collect
from .team_views import WORLDS

PREFIX = "ppv-"
SCHEMA = "seatlab.calendar-peer-payload-private/v1"


def plan(native: Path, ledger: Path) -> list[tuple[dict[str, Any], str]]:
    originals = peer_payload.originals()
    prompts = peer_payload.contexts(native, ledger)
    if set(originals) != set(WORLDS) or set(prompts) != set(WORLDS):
        raise ValueError("incomplete_panel")
    result = []
    for index, world in enumerate(WORLDS):
        original = originals[world]
        shown, masked = prompts[world]
        order = (("shown", shown), ("masked", masked))
        for repeat in range(2):
            presentations = order if (index + repeat) % 2 == 0 else order[::-1]
            for presentation, prompt in presentations:
                result.append(
                    (
                        {
                            "run": f"{PREFIX}{world}-s{repeat}-{presentation}",
                            "world": world,
                            "repeat": repeat,
                            "presentation": presentation,
                            "source_uuid": original["trial_uuid"],
                            "source_result_sha256": original["result_sha256"],
                            "prompt_sha256": p.digest(prompt),
                            "status": "unobserved",
                            "response_sha256": None,
                            "served_model": None,
                            "slot": None,
                        },
                        prompt,
                    )
                )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay the registered peer payload decision contexts.",
        allow_abbrev=False,
    )
    parser.add_argument("operation", choices=("plan", "direct"))
    parser.add_argument("native", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    destination = args.destination
    if args.operation == "plan":
        if destination is not None:
            parser.error("plan does not create a destination")
        print(p.wire([row for row, _ in plan(args.native, args.spend_dir)]))
        return 0
    ledger = args.spend_dir.expanduser().absolute()
    if destination is None or Path(os.environ.get("SEATLAB_SPEND_DIR", "")) != ledger:
        parser.error(
            "direct requires --destination and --spend-dir matching SEATLAB_SPEND_DIR"
        )
    result = _collect(
        plan(args.native, ledger),
        destination.absolute(),
        ledger,
        run_prefix=PREFIX,
        schema=SCHEMA,
        original_receipt_sha256=peer_payload.SOURCE_SHA256,
    )
    print(
        p.wire(
            {
                "settled_or_held_calls": len(result["meter"]["calls"]),
                "stopped": result["stopped"],
            }
        )
    )
    return int(result["stopped"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
