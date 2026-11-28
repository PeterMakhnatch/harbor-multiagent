import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ...native_study import as_record, record_bytes, write_exclusive
from ..decision_boundary.inputs import read_input
from ..exhibit_pairs import reject_constant, unique_object

TASK = Path(__file__).resolve().parents[2] / "tasks" / "dispatch-recall"


def read_object(path: Path, expected: str | None = None) -> dict[str, Any]:
    if str(path) == "-":
        raise ValueError("supply a bounded regular file, not standard input")
    data = read_input(str(path))
    if expected is not None and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"{path.name} does not match the saved SHA-256 digest")
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    return as_record(value, path.name)


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check and prepare a dispatch-recall study.", allow_abbrev=False
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "certify", help="check a controlled decision without a model"
    )
    check.add_argument("--seed", type=int, default=7)
    demo = commands.add_parser(
        "rehearse",
        help="exercise both scripted roles and the verifier on local sockets",
    )
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--transition", choices=("stay", "move"), required=True)
    demo.add_argument(
        "--timing", choices=("immediate", "deferred", "withheld"), required=True
    )
    demo.add_argument(
        "--cut-out", type=Path, help="write the private frozen cut to a new file"
    )
    explain = commands.add_parser(
        "diagnose", help="explain one private native verifier cut"
    )
    explain.add_argument("cut", type=Path)
    schedule = commands.add_parser(
        "design", help="write a private two-seat assignment; start nothing"
    )
    schedule.add_argument("--seeds", type=int, nargs="+", required=True)
    schedule.add_argument("--order-seed", type=int, required=True)
    schedule.add_argument("--out", type=Path, required=True)
    setup = commands.add_parser(
        "prepare", help="bind one assignment to a native Harbor config"
    )
    setup.add_argument("plan", type=Path)
    setup.add_argument("job_id")
    setup.add_argument("--task", type=Path, default=TASK)
    team = setup.add_mutually_exclusive_group(required=True)
    team.add_argument("--roster", type=Path)
    team.add_argument(
        "--owners", type=Path, help="private directory for one distinct socket per turn"
    )
    setup.add_argument("--out", type=Path, required=True)
    seal = commands.add_parser(
        "seal", help="pin every assigned job before any trial starts"
    )
    seal.add_argument("plan", type=Path)
    seal.add_argument("--root", type=Path, required=True)
    seal.add_argument("--out", type=Path, required=True)
    inspect = commands.add_parser(
        "collect", help="check one prepared native trial against its receipt"
    )
    inspect.add_argument("plan", type=Path)
    inspect.add_argument("job_id")
    inspect.add_argument("prepared", type=Path)
    inspect.add_argument(
        "--expect", required=True, help="receipt digest saved before the trial"
    )
    study = commands.add_parser(
        "assess", help="report both native team reward and actual commitments"
    )
    study.add_argument("plan", type=Path)
    source = study.add_mutually_exclusive_group()
    source.add_argument(
        "--registry", type=Path, help="private manifest sealed before the first run"
    )
    study.add_argument(
        "--root", type=Path, help="directory containing all prepared jobs"
    )
    study.add_argument(
        "--expect", help="registry SHA-256 digest saved before the first run"
    )
    source.add_argument(
        "--cut",
        nargs=2,
        action="append",
        default=[],
        metavar=("JOB_ID", "CUT"),
        help="unverified cut replay only; never native team evidence",
    )
    return parser


def prepare_command(args: argparse.Namespace) -> dict[str, Any]:
    from .prepare import native, prepare

    if args.owners is None:
        team = read_object(args.roster)
    else:
        from .participant import TURNS, roster

        owner_dir = args.owners.resolve(strict=True)
        endpoints = {
            turn: str(owner_dir / f"{args.job_id}-{turn}.sock") for turn in TURNS
        }
        team = {"participants": roster(native(), endpoints)}
    return prepare(read_object(args.plan), args.job_id, args.task, team, args.out)


def seal_command(args: argparse.Namespace) -> dict[str, Any]:
    from .registry import seal

    if args.out.resolve().is_relative_to(args.root.resolve(strict=True)):
        raise ValueError("save the registry outside the prepared job root")
    manifest = seal(read_object(args.plan), args.root)
    write_exclusive(args.out, manifest, 0o600)
    return {
        "path": str(args.out),
        "sha256": hashlib.sha256(record_bytes(manifest)).hexdigest(),
        "assigned": manifest["planned_jobs"],
        "started": 0,
    }


def assess_command(args: argparse.Namespace) -> dict[str, Any]:
    from .design import assess

    plan = read_object(args.plan)
    if args.registry is not None:
        if args.root is None or args.expect is None:
            raise ValueError(
                "--registry requires both --root and the previously saved --expect digest"
            )
        from .collect import collect, outcomes
        from .registry import checked_entries

        manifest = read_object(args.registry, args.expect)
        entries = checked_entries(plan, args.root, manifest)
        collected = [collect(plan, job_id, path, pin) for job_id, path, pin in entries]
        cuts = outcomes(plan, collected)
        return assess(plan, cuts) | {
            "evidence_source": "sealed_native_archives",
            "archive_statuses": dict(Counter(row["status"] for row in collected)),
            "recorded_exceptions_by_job": {
                row["job_id"]: row["record"]["exceptions"]
                for row in collected
                if row["record"]["exceptions"]
            },
            "provider_authenticated": False,
        }
    if args.root is not None or args.expect is not None:
        raise ValueError("--root and --expect apply only to --registry")
    cuts = {}
    for job_id, path in args.cut:
        if job_id in cuts:
            raise ValueError("multiple cuts for one assigned team")
        cuts[job_id] = read_object(Path(path))
    evaluation = assess(plan, cuts)
    if cuts:
        evaluation["replay_verdict"] = evaluation["verdict"]
        del evaluation["comparison_verdict"]
        evaluation["verdict"] = "unverified_replay_only"
    return evaluation | {
        "evidence_source": "unverified_cut_replay" if cuts else "no_evidence",
        "provider_authenticated": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "certify":
        from .certificate import certify

        return certify(args.seed)
    if args.command == "rehearse":
        from .rehearse import rehearse

        result = rehearse(args.seed, args.transition, args.timing)
        cut = result.pop("cut")
        if args.cut_out is not None:
            write_exclusive(args.cut_out, cut, 0o600)
        return result
    if args.command == "diagnose":
        from .native import diagnose

        return diagnose(read_object(args.cut))
    if args.command == "design":
        from .design import design

        plan = design(args.seeds, args.order_seed)
        write_exclusive(args.out, plan, 0o600)
        return {
            "path": str(args.out),
            "sha256": hashlib.sha256(record_bytes(plan)).hexdigest(),
            "assigned": len(plan["jobs"]),
            "started": 0,
        }
    if args.command == "prepare":
        return prepare_command(args)
    if args.command == "seal":
        return seal_command(args)
    if args.command == "collect":
        from .collect import collect

        result = collect(
            read_object(args.plan), args.job_id, args.prepared, args.expect
        )
        return {key: value for key, value in result.items() if key != "cut"}
    return assess_command(args)


def main(argv: list[str] | None = None) -> int:
    args = arguments().parse_args(argv)
    try:
        output = run(args)
    except (
        ValueError,
        TypeError,
        OSError,
        KeyError,
        RecursionError,
        RuntimeError,
        ImportError,
    ) as error:
        print(f"dispatch-recall: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True, indent=2, allow_nan=False))
    return 1 if output.get("status") == "rejected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
