import json
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import import_module
from pathlib import Path

from tests.unit.examples.test_exhibit_relay_reference import (
    ROLES,
    TASK,
    TIMEOUT,
    _env,
    _python_bin,
    _verify,
)


def _seat(root, role, bin_dir):
    home = root / role
    home.mkdir()
    app = home / "app"
    shutil.copytree(TASK / "solution", app)
    client = (TASK / "environment" / "seat" / "relay.py").read_text()
    endpoint = root / "s" / role / "rpc.sock"
    (app / "relay.py").write_text(
        client.replace('"/run/relay-seat/rpc.sock"', repr(str(endpoint)))
    )
    reference = app / "reference.py"
    reference.write_text(reference.read_text().replace('"/app', '"' + str(app)))
    return subprocess.Popen(
        [sys.executable, str(reference)],
        cwd=home,
        env=_env(app, bin_dir, home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_roles(root, bin_dir):
    processes = {}
    try:
        for role in ROLES:
            processes[role] = _seat(root, role, bin_dir)
        deadline = time.monotonic() + TIMEOUT
        for role, process in processes.items():
            stdout, stderr = process.communicate(
                timeout=max(0.1, deadline - time.monotonic())
            )
            assert process.returncode == 0, (role, stdout, stderr)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def _check_archive(case, mode, observation, cut):
    assert cut["version"] == 1 and cut["case"] == case and cut["mode"] == mode
    events = cut["events"]
    assert [(e["seq"], e["role"], e["action"]["op"]) for e in events] == [
        (1, "loader", "request"),
        (2, "registrar", "file"),
        (3, "depot", "file"),
        (4, "loader", "commit"),
    ]
    notes = events[1]["action"]["notes"]
    assert len(notes) == 4 and all(note["weight"] > 0 for note in notes)
    assert all("notes" not in event["action"] for event in (events[0], *events[2:]))
    assert observation["schema"] == "harbor.exhibit-relay.observation/v1"
    assert observation["mode"] == mode and observation["status"] == "complete"
    assert observation["handoff"]["stage"] == "done"
    brief = next(b for b in case["briefs"] if b["id"] == case["realized"])
    epoch = brief["epoch"]
    routes = events[3]["action"]["routes"]
    mapping = {
        role: {
            c["key"]: c["value"] for c in case[role] if c["start"] <= epoch <= c["end"]
        }
        for role in ("registrar", "depot")
    }
    correct = sum(
        routes[item] == mapping["depot"][mapping["registrar"][item]]
        for item in brief["items"]
    )
    assert observation["correct_items"] == correct
    assert type(observation["reward"]) is int and observation["reward"] == int(
        correct == 2
    )
    handoff = observation["authored_handoff"]
    assert handoff["schema"] == "harbor.exhibit-relay.authorship/v1"
    for side in ("submitted", "delivered"):
        report = handoff[side]
        masked = side == "delivered" and mode == "author-masked"
        assert report["assertion_count"] == len(notes)
        assert report["correct_pair_count"] == len(notes)
        assert report["unknown_weight_count"] == (len(notes) if masked else 0)
        assert report["accurate_positive_weight_count"] == (0 if masked else len(notes))
        assert report["inaccurate_positive_weight_count"] == 0
    public = json.dumps(observation)
    assert '"notes"' not in public and '"bundles"' not in public
    assert all(json.dumps(key) not in public for note in notes for key in note["keys"])


def test_authored_role_local_native_interface(monkeypatch):
    monkeypatch.syspath_prepend(str(TASK / "environment"))
    from exhibit_relay.cases import make_case
    from exhibit_relay.engine import Session

    Family = import_module("examples.tasks.exhibit-relay.environment.serve").Family

    class ObservedFamily(Family):
        def __init__(self, *args):
            super().__init__(*args)
            self.views = {}

        def _dispatch(self, role, request):
            result = super()._dispatch(role, request)
            if request == {"op": "view"} and result["turn"] == role:
                self.views.setdefault(result["phase"], result)
            return result

    case = make_case(7, "coupling", layout="a")
    arms = []
    for mode in ("authored", "author-masked"):
        with tempfile.TemporaryDirectory(prefix="xa-", dir="/tmp") as directory:
            root = Path(directory)
            bin_dir = _python_bin(root)
            with ObservedFamily(root / "s", root / "c", Session(case, mode)) as family:
                _run_roles(root, bin_dir)
                result, observation, cut = _verify(root, bin_dir)
                assert result.returncode == 0, (result.stdout, result.stderr)
                assert family.frozen
                _check_archive(case, mode, observation, cut)
                arms.append((family.views, observation, cut))
    (left, authored, authored_cut), (right, masked, masked_cut) = arms
    assert left["request"] == right["request"]
    assert left["registrar"] == right["registrar"]
    assert authored_cut["events"][:2] == masked_cut["events"][:2]
    notes = authored_cut["events"][1]["action"]["notes"]
    assert left["depot"]["bundles"] == notes
    assert right["depot"] == {
        **left["depot"],
        "bundles": [{**n, "weight": 0} for n in notes],
    }
    assert authored["case_commitment"] == masked["case_commitment"]
    assert (
        authored["authored_handoff"]["submitted"]
        == masked["authored_handoff"]["submitted"]
    )
