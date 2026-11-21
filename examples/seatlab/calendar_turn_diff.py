import sys
from pathlib import Path

from . import calendar_case_source
from .calendar_case import _Parser, _wire
from .calendar_notices import audit
from .calendar_notices import protocol as p


def compare(
    left: Path,
    right: Path,
    spend_dir: Path,
    right_spend_dir: Path | None = None,
) -> dict[str, object]:
    first = calendar_case_source.load(left, spend_dir, include_turns=True)
    second = calendar_case_source.load(
        right,
        spend_dir if right_spend_dir is None else right_spend_dir,
        include_turns=True,
    )
    audit.require(
        first.trial_uuid != second.trial_uuid
        and first.result_sha256 != second.result_sha256,
        "duplicate_native_turn_trial",
    )
    audit.require(
        (
            first.world,
            first.arm,
            first.native_task_sha256,
            first.stock_semantics_sha256,
        )
        == (
            second.world,
            second.arm,
            second.native_task_sha256,
            second.stock_semantics_sha256,
        ),
        "unmatched_native_turn_source",
    )
    turns = []
    for phase, a, b in zip(p.PHASES, first.turns, second.turns, strict=True):
        audit.require(
            a.phase == b.phase == phase
            and a.seat == b.seat
            and a.served_model == b.served_model,
            "unmatched_native_turn_identity",
        )
        turns.append(
            {
                "phase": phase,
                "seat": a.seat,
                "same_prompt": a.prompt_sha256 == b.prompt_sha256,
                "same_action": a.action == b.action,
            }
        )
    return {
        "schema": "seatlab.native-calendar-turn-pair/v1",
        "world": first.world,
        "arm": first.arm,
        "source_sha256": first.stock_semantics_sha256,
        "trials": [
            {
                "run": case.native_run,
                "trial_uuid": case.trial_uuid,
                "result_sha256": case.result_sha256,
                "reward": case.native_reward,
            }
            for case in (first, second)
        ],
        "turns": turns,
    }


def main() -> int:
    parser = _Parser(allow_abbrev=False)
    parser.add_argument("left", metavar="LEFT", type=Path)
    parser.add_argument("right", metavar="RIGHT", type=Path)
    parser.add_argument("--spend-dir", metavar="PATH", required=True, type=Path)
    parser.add_argument("--right-spend-dir", metavar="PATH", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.left, args.right, args.spend_dir, args.right_spend_dir)
        sys.stdout.buffer.write(_wire(result))
    except (OSError, ValueError):
        print("Cannot compare native calendar turns.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
