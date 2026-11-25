import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify the physical consequence of a peer filing, without starting a trial."
    )
    sub = parser.add_subparsers(required=True, dest="command")
    c = sub.add_parser(
        "certify",
        help="enumerate both hidden maps and every legal final dispatch at a natural focal draw",
    )
    c.add_argument(
        "--start",
        type=int,
        default=7,
        help="first public development seed to inspect; search at most 128",
    )
    m = sub.add_parser(
        "materialize",
        help="create a distinct native task outside the checkout; do not start it",
    )
    m.add_argument("out", type=Path)
    inspect = sub.add_parser(
        "check-task",
        help="check an existing materialized task against the pinned stock and overlay",
    )
    inspect.add_argument("task", type=Path)
    p = sub.add_parser(
        "prepare",
        help="write matched reference configs and native locks; do not run them",
    )
    p.add_argument("task", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--seed", required=True, type=int)
    receivers = sub.add_parser(
        "prepare-receivers",
        help="prepare four prospective receiver configs without sending requests",
    )
    receivers.add_argument("task", type=Path)
    receivers.add_argument("out", type=Path)
    receivers.add_argument("--seed", required=True, type=int)
    cells = ("original-other", "original-relevant", "swapped-other", "swapped-relevant")
    for cell in cells:
        receivers.add_argument(
            "--" + cell,
            dest=cell,
            required=True,
            metavar="SOCKET",
            help="an existing private one-turn receiver socket",
        )
    enroll = sub.add_parser(
        "enroll", help="commit to a private balanced cohort; never start a trial"
    )
    enroll.add_argument("out", type=Path)
    enroll.add_argument("--cases", required=True, type=int)
    enroll.add_argument("--development-seed", type=int)
    show = sub.add_parser(
        "check-cohort",
        help="rederive the full private roster and show only its commitment",
    )
    show.add_argument("study", type=Path)
    show.add_argument("--expect", required=True, metavar="COHORT_DIGEST")
    qualify = sub.add_parser(
        "qualify-case",
        help="qualify the exact enrolled dispatch matrix without running a model",
    )
    qualify.add_argument("study", type=Path)
    qualify.add_argument("ticket")
    qualify.add_argument("--expect", required=True, metavar="COHORT_DIGEST")
    bind = sub.add_parser(
        "bind-case", help="prepare the four native cells for one precommitted case"
    )
    bind.add_argument("study", type=Path)
    bind.add_argument("ticket")
    bind.add_argument("task", type=Path)
    bind.add_argument("out", type=Path)
    bind.add_argument("--expect", required=True, metavar="COHORT_DIGEST")
    for cell in cells:
        bind.add_argument("--" + cell, dest=cell, required=True, metavar="SOCKET")
    inspect_trial = sub.add_parser(
        "inspect-trial",
        help="audit an archived receiver outcome in its reserved study slot",
    )
    inspect_trial.add_argument("study", type=Path)
    inspect_trial.add_argument("ticket")
    inspect_trial.add_argument("plan", type=Path)
    inspect_trial.add_argument("cell", choices=cells)
    inspect_trial.add_argument("trial", type=Path)
    inspect_trial.add_argument("--expect", required=True, metavar="COHORT_DIGEST")
    score = sub.add_parser(
        "score-cohort",
        help="audit every reserved slot; bound missing binary outcomes without imputing them",
    )
    score.add_argument("study", type=Path)
    score.add_argument("case_inputs", type=Path)
    score.add_argument("--expect", required=True, metavar="COHORT_DIGEST")
    args = parser.parse_args(argv)
    try:
        if args.command == "certify":
            from .certificate import certify

            result = certify(args.start)
        elif args.command == "enroll":
            from .cohort import enroll as draw_roster

            result = draw_roster(args.out, args.cases, args.development_seed)
        elif args.command == "check-cohort":
            from .cohort import load_design

            _, result = load_design(args.study, args.expect)
        elif args.command == "qualify-case":
            from .cohort import qualify_case

            result = qualify_case(args.study, args.ticket, args.expect)
        elif args.command == "bind-case":
            from .cohort import bind_case

            result = bind_case(
                args.study,
                args.ticket,
                args.expect,
                args.task,
                args.out,
                {cell: getattr(args, cell) for cell in cells},
            )
        elif args.command == "inspect-trial":
            from .cohort import check_slot
            from .cohort_scores import checked_trial

            slot = check_slot(args.study, args.ticket, args.expect, args.plan)
            result = checked_trial(slot, args.plan, args.cell, args.trial)
        elif args.command == "score-cohort":
            from .cohort_scores import score_cohort

            result = score_cohort(args.study, args.case_inputs, args.expect)
        elif args.command == "prepare-receivers":
            from .receiver_plan import prepare_receivers

            result = prepare_receivers(
                args.task,
                args.out,
                args.seed,
                {cell: getattr(args, cell) for cell in cells},
            )
        elif args.command == "prepare":
            from .preflight import prepare

            result = prepare(args.task, args.out, args.seed)
        elif args.command == "check-task":
            from .stage import check_stage

            result = {
                "schema": "seatlab.physical-dispatch.task-check/v1",
                "digest": check_stage(args.task),
                "executions": 0,
            }
        else:
            from .stage import materialize

            result = materialize(args.out)
    except (
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        RecursionError,
        ValueError,
        OSError,
        RuntimeError,
    ) as exc:
        if args.command == "inspect-trial":
            from .cohort_scores import unknown

            print(json.dumps(unknown(exc), sort_keys=True))
        else:
            print(f"physical-dispatch: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
