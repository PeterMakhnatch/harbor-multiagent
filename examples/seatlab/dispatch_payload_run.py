import argparse
import os
from pathlib import Path
from typing import Any

from . import dispatch_payload
from .calendar_notices.presentation_run import _collect
from .live_answers.protocol import digest, wire

PREFIX = "dpp-"
SCHEMA = "seatlab.dispatch-payload-private/v1"
WORLDS = tuple(range(6801, 6809))


def plan(
    sources: dict[int, dispatch_payload.Source],
) -> list[tuple[dict[str, Any], str]]:
    if set(sources) != set(WORLDS):
        raise ValueError("incomplete_panel")
    result = []
    for index, world in enumerate(WORLDS):
        source = sources[world]
        if source.seed != world:
            raise ValueError("source_seed_mismatch")
        order = (("shown", source.shown), ("masked", source.masked))
        for repeat in range(2):
            arms = order if (index + repeat) % 2 == 0 else order[::-1]
            for arm, prompt in arms:
                result.append(
                    (
                        {
                            "run": f"{PREFIX}s{world}-r{repeat}-{arm}",
                            "world": world,
                            "repeat": repeat,
                            "arm": arm,
                            "source_uuid": source.source_uuid,
                            "source_result_sha256": source.source_result_sha256,
                            "prompt_sha256": digest(prompt),
                            "status": "unobserved",
                            "served_model": None,
                            "response_sha256": None,
                            "choices": None,
                        },
                        prompt,
                    )
                )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay the registered dispatcher decision contexts.",
        allow_abbrev=False,
    )
    parser.add_argument("operation", choices=("plan", "direct"))
    parser.add_argument("native", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.operation == "plan":
        if args.destination is not None:
            parser.error("plan does not create a destination")
        print(wire([row for row, _ in plan(dispatch_payload.sources(args.native))]))
        return 0
    bound = os.environ.get("SEATLAB_SPEND_DIR")
    if args.destination is None or not bound or not Path(bound).is_absolute():
        parser.error("direct requires --destination and the existing SEATLAB_SPEND_DIR")
    result = _collect(
        plan(dispatch_payload.sources(args.native)),
        args.destination.absolute(),
        Path(bound),
        run_prefix=PREFIX,
        schema=SCHEMA,
        original_receipt_sha256=dispatch_payload.SOURCE_SHA256,
        decode_dispatch=dispatch_payload.decode_choices,
    )
    print(
        wire(
            {
                "settled_or_held_calls": len(result["meter"]["calls"]),
                "stopped": result["stopped"],
            }
        )
    )
    return int(result["stopped"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
