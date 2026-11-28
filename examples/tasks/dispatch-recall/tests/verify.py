import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

CONTROL = "/run/recall-control/rpc.sock"
LOGS = "/logs/verifier"
APP = "/app"
LIMIT = 1024 * 1024
CUT_SCHEMA = "harbor.dispatch-recall.cut/v1"
SCHEMA = "harbor.dispatch-recall.verdict/v1"
PRIVATE_CUT = "private-recall-cut.json"
OUTPUTS = ("reward.json", "verdict.json", "diagnostics.json", PRIVATE_CUT)
COMMIT_INDEX = 2
CLOSE_INDEX = 3


def freeze(path: str) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall(b'{"op":"freeze"}\n')
        with conn.makefile("rb") as reader:
            line = reader.readline(LIMIT + 1)
    if len(line) > LIMIT or not line.endswith(b"\n"):
        raise ValueError("invalid control response framing")
    reply = json.loads(line)
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise ValueError("freeze failed")
    cut = reply.get("data")
    if not isinstance(cut, dict):
        raise TypeError("missing cut")
    return cut


def verdict(cut: dict[str, Any]) -> dict[str, Any]:
    if cut.get("schema") != CUT_SCHEMA:
        raise ValueError("unexpected cut schema")
    actions = cut["actions"]
    if not isinstance(actions, list):
        raise TypeError("actions must be a list")
    reward = cut["reward"]
    if len(actions) <= COMMIT_INDEX:
        if reward is not None:
            raise ValueError("reward without a commit")
        return {
            "schema": SCHEMA,
            "disposition": "missing_commit",
            "reward": 0,
            "closed": False,
        }
    if type(reward) is not int or reward not in (0, 1):
        raise ValueError("committed cut needs an int reward of 0 or 1")
    return {
        "schema": SCHEMA,
        "disposition": "committed",
        "reward": reward,
        "closed": len(actions) > CLOSE_INDEX,
    }


def grade(cut: dict[str, Any], app: str) -> tuple[dict[str, Any], dict[str, Any]]:
    sys.path.insert(0, app)
    from recall_evidence import diagnose

    diagnostics = diagnose(cut)
    outcome = verdict(cut)
    if diagnostics.get("reward") != cut["reward"]:
        raise ValueError("diagnostics disagree with the cut reward")
    return outcome, diagnostics


def encode(data: dict[str, Any]) -> str:
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text + "\n"


def publish(logs: Path, name: str, data: dict[str, Any]) -> None:
    final = logs / name
    temp = final.with_suffix(".tmp")
    temp.write_text(encode(data))
    temp.replace(final)


def retain(logs: Path, cut: dict[str, Any]) -> None:
    choice = os.environ.get("RECALL_RETAIN_CUT", "0")
    if choice not in ("0", "1"):
        raise ValueError("expected RECALL_RETAIN_CUT 0 or 1")
    if choice == "1":
        descriptor = os.open(
            logs / PRIVATE_CUT, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        with open(descriptor, "w") as destination:
            destination.write(encode(cut))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="verify.py")
    root.add_argument("--control", default=CONTROL)
    root.add_argument("--logs", default=LOGS)
    root.add_argument("--app", default=APP)
    root.add_argument("--cut", type=Path)
    return root


def offline(cut_path: Path, app: str) -> int:
    try:
        cut = json.loads(cut_path.read_text())
        if not isinstance(cut, dict):
            raise TypeError("cut must be a JSON object")
        outcome, diagnostics = grade(cut, app)
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        print(f"no grade: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(encode({"diagnostics": diagnostics, "verdict": outcome}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cut is not None:
        return offline(args.cut, args.app)
    logs = Path(args.logs)
    logs.mkdir(parents=True, exist_ok=True)
    if any(os.path.lexists(logs / name) for name in OUTPUTS):
        print("refusing to overwrite previous verifier evidence", file=sys.stderr)
        return 1
    try:
        cut = freeze(args.control)
        retain(logs, cut)
        outcome, diagnostics = grade(cut, args.app)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
        RecursionError,
        OverflowError,
    ):
        failure = {
            "schema": SCHEMA,
            "disposition": "infrastructure_error",
            "reward": None,
            "closed": None,
        }
        publish(logs, "verdict.json", failure)
        print("no grade: freeze or evidence validation failed", file=sys.stderr)
        return 1
    publish(logs, "diagnostics.json", diagnostics)
    publish(logs, "verdict.json", outcome)
    publish(logs, "reward.json", {"reward": outcome["reward"]})
    print(f"reward={outcome['reward']} disposition={outcome['disposition']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
