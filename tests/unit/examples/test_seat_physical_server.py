import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

from examples.seatlab.physical_dispatch import policies, stage, worlds

_ROLES = ("loader", "registrar", "depot")
_BOOT = """
import sys
from pathlib import Path
import serve

root = Path(sys.argv[1])
paths = {"/run/relay": root / "relay", "/run/relay-control": root / "control"}
serve.Path = paths.__getitem__
enter = serve.Family.__enter__

def ready(self):
    result = enter(self)
    print("ready", flush=True)
    return result

serve.Family.__enter__ = ready
serve.main()
"""


def _native():
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        package = "examples.tasks.exhibit-relay.environment.exhibit_relay"
        return (
            import_module(f"{package}.cases").make_case,
            import_module(f"{package}.grading").grade,
        )
    finally:
        sys.dont_write_bytecode = previous


@contextmanager
def _server(task: Path, seed: int, map_name: str):
    with tempfile.TemporaryDirectory(prefix="ep-", dir="/tmp") as name:
        root = Path(name)
        env = dict(os.environ)
        env.update(
            EXHIBIT_SEED=str(seed),
            EXHIBIT_PROFILE="fork",
            EXHIBIT_MODE="linked",
            EXHIBIT_LAYOUT="draw",
            EXHIBIT_BAY_MAP=map_name,
        )
        with subprocess.Popen(
            [sys.executable, "-B", "-c", _BOOT, name],
            cwd=task / "environment",
            env=env,
            stdout=subprocess.PIPE,
        ) as process:
            try:
                assert process.stdout is not None
                fd = process.stdout.fileno()
                readable, _, _ = select.select([fd], [], [], 10)
                assert readable, "staged server did not signal readiness"
                assert os.read(fd, 64) == b"ready\n", "staged server failed to start"
                yield root
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def _rpc(root: Path, owner: str, action: dict) -> dict:
    path = root / "control" if owner == "control" else root / "relay" / owner
    deadline = time.monotonic() + 5
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path / "rpc.sock"))
        connection.sendall(json.dumps(action).encode() + b"\n")
        body = bytearray()
        for _ in range(256):
            remaining = deadline - time.monotonic()
            assert remaining > 0, "RPC reply timed out"
            connection.settimeout(remaining)
            chunk = connection.recv(4096)
            assert chunk, "RPC closed before newline"
            body.extend(chunk)
            assert len(body) <= 256 * 1024, "RPC reply exceeds limit"
            if b"\n" in chunk:
                assert body.endswith(b"\n")
                reply = json.loads(body)
                assert reply["ok"] is True, reply
                return reply["data"]
    raise AssertionError("RPC reply exceeds read limit")


def _views(root: Path) -> dict:
    return {role: _rpc(root, role, {"op": "view"}) for role in _ROLES}


def _play(task: Path, seed: int, map_name: str, policy: str) -> dict:
    with _server(task, seed, map_name) as root:
        initial = _views(root)
        nomination = policies.nominate(initial["loader"])
        _rpc(root, "loader", nomination)
        registration = policies.registrar(_rpc(root, "registrar", {"op": "view"}))
        _rpc(root, "registrar", registration)
        filing = policies.depot(_rpc(root, "depot", {"op": "view"}), policy)
        _rpc(root, "depot", filing)
        dispatch = _views(root)
        loader = dispatch["loader"]
        action = policies.follow(loader)
        if policy == "other":
            assert action is None
            action = {
                "op": "commit",
                "routes": dict(
                    zip(
                        sorted(loader["order"]["items"]),
                        sorted(loader["bays"])[:2],
                        strict=True,
                    )
                ),
            }
        else:
            assert action is not None
        _rpc(root, "loader", action)
        final = _views(root)
        cut = _rpc(root, "control", {"op": "freeze"})
        assert _rpc(root, "control", {"op": "freeze"}) == cut
        assert _views(root) == final
        assert cut["mode"] == "linked"
        assert cut["events"] == [
            {"seq": index, "role": role, "action": submitted}
            for index, (role, submitted) in enumerate(
                zip(
                    ("loader", "registrar", "depot", "loader"),
                    (nomination, registration, filing, action),
                    strict=True,
                ),
                1,
            )
        ]
        return {
            "initial": initial,
            "dispatch": dispatch,
            "final": final,
            "cut": cut,
            "action": action,
        }


def _compare_frames(original: dict, swapped: dict, policy: str) -> None:
    for phase in ("initial", "dispatch", "final"):
        left, right = original[phase], swapped[phase]
        assert left["registrar"] == right["registrar"]
        assert left["depot"] != right["depot"]
        assert {k: v for k, v in left["depot"].items() if k != "cards"} == {
            k: v for k, v in right["depot"].items() if k != "cards"
        }
        if phase == "initial" or policy == "other":
            assert left["loader"] == right["loader"]
        else:
            assert left["loader"] != right["loader"]
            assert {k: v for k, v in left["loader"].items() if k != "depot_file"} == {
                k: v for k, v in right["loader"].items() if k != "depot_file"
            }


def test_physical_server_native_dispatch() -> None:
    with tempfile.TemporaryDirectory(prefix="physical-stage-") as name:
        task = Path(name).resolve() / "task"
        receipt = stage.materialize(task)
        assert stage.check_stage(task) == receipt["stage_digest"]
        make_case, grade = _native()
        for seed in range(129):
            case = make_case(seed, "fork", layout="draw")
            if case["realized"] == min(brief["id"] for brief in case["briefs"]):
                break
        else:
            raise AssertionError("no naturally focal development draw")
        runs = {
            (policy, map_name): _play(task, seed, map_name, policy)
            for policy in ("relevant", "other")
            for map_name in ("original", "swapped")
        }
        for policy in ("relevant", "other"):
            _compare_frames(runs[policy, "original"], runs[policy, "swapped"], policy)
            assert (
                runs[policy, "original"]["cut"]["events"][:3]
                == runs[policy, "swapped"]["cut"]["events"][:3]
            )
        original = runs["relevant", "original"]["action"]["routes"]
        swapped = runs["relevant", "swapped"]["action"]["routes"]
        assert original.keys() == swapped.keys()
        assert set(original.values()) == set(swapped.values())
        assert all(original[key] != swapped[key] for key in original)
        for (policy, map_name), run in runs.items():
            cut = run["cut"]
            assert cut["case"] == worlds.select(case, map_name)
            assert cut["case"]["briefs"] == case["briefs"]
            assert cut["case"]["realized"] == case["realized"]
            result = grade(cut)
            truth = runs["relevant", map_name]["action"]["routes"]
            correct = sum(
                bay == truth[item] for item, bay in run["action"]["routes"].items()
            )
            assert result["status"] == "complete"
            assert result["correct_items"] == correct
            assert result["reward"] == int(correct == 2)
            assert result["registrar_supported"] == 2
            assert result["depot_supported"] == (2 if policy == "relevant" else 0)
            assert result["chains_supported"] == (2 if policy == "relevant" else 0)
            if policy == "relevant":
                assert result["reward"] == 1
                assert result["dispatch_supported"] == 2
            else:
                assert result["dispatch_supported"] == 0
        assert runs["other", "original"]["action"] == runs["other", "swapped"]["action"]
