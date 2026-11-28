import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import plan, results


def _team(raw: str) -> tuple[str, Path]:
    name, separator, path = raw.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=ROSTER.json")
    return name, Path(path)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        description="Prepare or inspect a native Joint Reopen partner study. Does not launch trials."
    )
    commands = cli.add_subparsers(dest="command", required=True)
    reg = commands.add_parser("register")
    reg.add_argument("--start", type=int, required=True)
    reg.add_argument("--out", type=Path, required=True)
    modes = reg.add_mutually_exclusive_group(required=True)
    modes.add_argument("--control", choices=("same", "different"))
    modes.add_argument(
        "--team", action="append", type=_team, metavar="NAME=ROSTER.json"
    )
    for name in ("audit", "score", "rehearse"):
        read = commands.add_parser(name)
        read.add_argument("--study", type=Path, required=True)
        read.add_argument("--pin", required=True)
        if name == "score":
            read.add_argument("--trials", type=Path, action="append", required=True)
    return cli


def _receipt(manifest: dict[str, Any], reg: dict[str, Any], pin: str) -> dict[str, Any]:
    return {
        "schema": "seatlab.partner-native-admission/v1",
        "names": manifest["names"],
        "mode": manifest["mode"],
        "study_sha256": pin,
        "registration_sha256": manifest["registration_sha256"],
        "pairings": len(reg["teams"]),
        "registered_native_trials": len(reg["cells"]),
        "launched_native_trials_by_command": 0,
    }


def command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "register":
        manifest, reg = plan.build(args.start, args.team, args.control)
        pin = plan.save(args.out, manifest, reg)
        return 0, _receipt(manifest, reg, pin)
    manifest, reg = plan.load(args.study, args.pin)
    if args.command == "audit":
        return 0, _receipt(manifest, reg, args.pin)
    if args.command == "rehearse":
        from . import rehearse

        check = rehearse.run(manifest, reg)
        return int(not check["matched_prediction"]), check
    report = results.summarize(manifest, reg, args.trials)
    return int(not report["complete"]), report


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        code, receipt = command(args)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        print(
            f"{args.command}: invalid study, roster, result source or destination ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    except ImportError:
        print(
            "Install the fork's normal development environment to register native participants.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
