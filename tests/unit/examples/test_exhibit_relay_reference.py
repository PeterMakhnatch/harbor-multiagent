import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import import_module
from pathlib import Path
from typing import Any

TASK = Path(__file__).resolve().parents[3] / "examples" / "tasks" / "exhibit-relay"
ROLES = ("loader", "registrar", "depot")
SEED = 1729
TIMEOUT = 20


def _env(pythonpath: Path, bin_dir: Path, home: Path) -> dict[str, str]:
    return {
        "PATH": str(bin_dir) + os.pathsep + os.defpath,
        "HOME": str(home),
        "PYTHONPATH": str(pythonpath),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _python_bin(root: Path) -> Path:
    directory = root / "bin"
    directory.mkdir()
    (directory / "python3").symlink_to(sys.executable)
    return directory


def _seat(root: Path, role: str, bin_dir: Path) -> subprocess.Popen[str]:
    home = root / role
    home.mkdir()
    solution = home / "solution"
    shutil.copytree(TASK / "solution", solution)
    mount = home / "app"
    mount.mkdir()
    shutil.copyfile(
        TASK / "environment" / "seat" / "relay.py", mount / "_seat_relay.py"
    )
    endpoint = root / "s" / role / "rpc.sock"
    (mount / "relay.py").write_text(
        "import _seat_relay\n"
        f"_seat_relay.SOCKET = {str(endpoint)!r}\n"
        "from _seat_relay import rpc, wait\n"
    )
    return subprocess.Popen(
        ["/bin/sh", str(solution / "solve.sh")],
        cwd=home,
        env=_env(mount, bin_dir, home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_reference(root: Path, bin_dir: Path) -> None:
    processes = {}
    try:
        for role in ROLES:
            processes[role] = _seat(root, role, bin_dir)
        deadline = time.monotonic() + TIMEOUT
        for role, process in processes.items():
            stdout, stderr = process.communicate(
                timeout=max(0.1, deadline - time.monotonic())
            )
            assert process.returncode == 0, (role, process.returncode, stdout, stderr)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def _verify(
    root: Path, bin_dir: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], dict[str, Any]]:
    home = root / "v"
    home.mkdir()
    logs = home / "logs"
    verifier = TASK / "tests" / "verify.py"
    endpoint = root / "c" / "rpc.sock"
    script = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('native_verify', {str(verifier)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        f"module.CONTROL = {str(endpoint)!r}\n"
        f"module.LOGS = Path({str(logs)!r})\n"
        "sys.exit(module.main())\n"
    )
    env = _env(TASK / "environment", bin_dir, home)
    env["EXHIBIT_RETAIN_CUT"] = "1"
    result = subprocess.run(
        [str(bin_dir / "python3"), "-c", script],
        cwd=home,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    observation = json.loads((logs / "observation.json").read_text())
    cut = json.loads((logs / "private-exhibit-cut.json").read_text())
    reward_file = logs / "reward.json"
    if observation["status"] == "complete":
        assert json.loads(reward_file.read_text()) == {"reward": observation["reward"]}
    else:
        assert not reward_file.exists()
    return result, observation, cut


def _assert_open(
    case: dict[str, Any], observation: dict[str, Any], cut: dict[str, Any]
) -> None:
    assert cut["case"] == case
    assert cut["mode"] == "open"
    assert observation["status"] == "complete"
    assert type(observation["reward"]) is int and observation["reward"] == 1
    for name in (
        "correct_items",
        "registrar_supported",
        "depot_supported",
        "chains_supported",
        "dispatch_supported",
        "total_items",
    ):
        assert observation[name] == 2
    events = cut["events"]
    assert [(e["seq"], e["role"], e["action"]["op"]) for e in events] == [
        (1, "loader", "request"),
        (2, "registrar", "file"),
        (3, "depot", "file"),
        (4, "loader", "commit"),
    ]
    assert events[0]["action"]["slips"] == sorted(b["id"] for b in case["briefs"])
    for index, role in ((1, "registrar"), (2, "depot")):
        assert events[index]["action"]["ids"] == sorted(c["id"] for c in case[role])
    realized = next(b for b in case["briefs"] if b["id"] == case["realized"])
    epoch = realized["epoch"]
    mappings = {
        role: {
            c["key"]: c["value"] for c in case[role] if c["start"] <= epoch <= c["end"]
        }
        for role in ("registrar", "depot")
    }
    assert events[3]["action"]["routes"] == {
        exhibit: mappings["depot"][mappings["registrar"][exhibit]]
        for exhibit in realized["items"]
    }
    assert observation["handoff"]["stage"] == "done"


def test_open_role_local_reference(monkeypatch):
    monkeypatch.syspath_prepend(str(TASK / "environment"))
    from exhibit_relay.cases import make_case
    from exhibit_relay.engine import Session

    Family = import_module("examples.tasks.exhibit-relay.environment.serve").Family

    case = make_case(SEED, "fork")
    with tempfile.TemporaryDirectory(prefix="xr-", dir="/tmp") as directory:
        root = Path(directory)
        bin_dir = _python_bin(root)
        with Family(root / "s", root / "c", Session(case, "open")) as family:
            _run_reference(root, bin_dir)
            result, observation, cut = _verify(root, bin_dir)
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert family.frozen
            _assert_open(case, observation, cut)


def test_untouched_reference_host_is_incomplete(monkeypatch):
    monkeypatch.syspath_prepend(str(TASK / "environment"))
    from exhibit_relay.cases import make_case
    from exhibit_relay.engine import Session

    Family = import_module("examples.tasks.exhibit-relay.environment.serve").Family

    case = make_case(SEED, "fork")
    with tempfile.TemporaryDirectory(prefix="xr-", dir="/tmp") as directory:
        root = Path(directory)
        bin_dir = _python_bin(root)
        with Family(root / "s", root / "c", Session(case, "open")) as family:
            result, observation, cut = _verify(root, bin_dir)
            assert result.returncode == 1, (result.stdout, result.stderr)
            assert "no grade: incomplete relay" in result.stderr
            assert family.frozen
            assert cut == {"version": 1, "case": case, "mode": "open", "events": []}
            assert observation["status"] == "incomplete"
            assert observation["reward"] is None
            assert observation["correct_items"] is None
            assert observation["handoff"]["stage"] == "request"
