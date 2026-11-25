import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

LOGS = Path("/logs/verifier")
CONTROL = "/run/relay-control/rpc.sock"
LIMIT = 65536
SCHEMA = "harbor.exhibit-relay.observation/v1"


def freeze() -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(CONTROL)
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


def publish(name: str, data: dict[str, Any]) -> None:
    final = LOGS / name
    temp = final.with_suffix(".tmp")
    temp.write_text(json.dumps(data, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(final)


def retain_if_requested(cut: dict[str, Any]) -> None:
    choice = os.environ.get("EXHIBIT_RETAIN_CUT", "0")
    if choice not in ("0", "1"):
        raise ValueError("expected EXHIBIT_RETAIN_CUT 0 or 1")
    if choice == "1":
        path = LOGS / "private-exhibit-cut.json"
        with open(
            os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
        ) as destination:
            json.dump(
                cut, destination, separators=(",", ":"), sort_keys=True, allow_nan=False
            )
            destination.write("\n")


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    if any(
        os.path.lexists(LOGS / name)
        for name in ("reward.json", "observation.json", "private-exhibit-cut.json")
    ):
        print("refusing to overwrite previous verifier evidence", file=sys.stderr)
        return 1
    try:
        sys.path.insert(0, "/app")
        from exhibit_relay.diagnostics import observation as summarize

        cut = freeze()
        observation = summarize(cut)
        retain_if_requested(cut)
        if observation["status"] == "complete":
            reward = observation["reward"]
            if type(reward) is not int or reward not in (0, 1):
                raise ValueError("invalid complete reward")
        elif observation["status"] == "incomplete" and observation["reward"] is None:
            reward = None
        else:
            raise ValueError("invalid grade status")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
        RecursionError,
        OverflowError,
    ):
        publish(
            "observation.json",
            {"schema": SCHEMA, "status": "infrastructure_error", "reward": None},
        )
        print("no grade: freeze or evidence validation failed", file=sys.stderr)
        return 1
    publish("observation.json", observation)
    if reward is None:
        print("no grade: incomplete relay", file=sys.stderr)
        return 1
    publish("reward.json", {"reward": reward})
    print(f"reward={reward}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
