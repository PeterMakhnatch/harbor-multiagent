import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from examples.seatlab.workbench.__main__ import main as workbench
from tests.unit.examples.test_seatlab_job_projection import (
    SECRET,
    SETTINGS,
    STOCK,
    TEAM,
    WORLD,
    document,
    native_keys,
    only,
    save,
)

ROOT = Path(__file__).resolve().parents[3]
SIDES = ("left", "right")
TARGETS = {
    "participant": ("--participant", "p2"),
    "channel": ("--world-setting", "HELLO_TONE"),
}
GUARD = """
import os, sys
blocked = ('socket.', 'subprocess.', 'os.system', 'os.exec', 'os.posix_spawn',
           'os.fork', 'os.remove', 'os.rename', 'os.mkdir')
write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
def watch(event, args):
    if event.startswith(blocked):
        raise RuntimeError('coordinator attempted an external action')
    if event == 'open' and (set(str(args[1] or '')) & set('wax+') or args[2] & write_flags):
        raise RuntimeError('coordinator attempted a write')
sys.addaudithook(watch)
from examples.seatlab.workbench.__main__ import main
code = main(sys.argv[1:])
raise SystemExit(70 if {'harbor', 'pydantic'} & set(sys.modules) else code)
"""
Json = dict[str, Any]


class Receipt(NamedTuple):
    path: Path
    value: Json
    key: str


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def call(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str]:
    code = workbench([str(arg) for arg in argv])
    out, err = capsys.readouterr()
    assert err == ""
    return code, out


class Workspace:
    def __init__(self, root: Path, mode: str, capsys: pytest.CaptureFixture[str]):
        self.root, self.capsys = root, capsys
        self.exchange = root / "exchange"
        self.exchange.mkdir()
        self.changes: dict[str, Json] = {"left": {}, "right": SETTINGS[mode]}
        self.found, self.reference, self.pin = self.isolate("reference", TARGETS[mode])
        assert self.found["status"] == "isolated"
        failed = self.isolate("failed", ("--participant", "p1"))
        assert failed[0]["status"] == "not_isolated"
        self.failed, self.failed_pin = failed[1:]
        self.receipts = {side: self.export(side, side) for side in SIDES}
        self.paths = {side: self.receipts[side].path for side in SIDES}
        self.keys = {side: self.receipts[side].key for side in SIDES}

    def isolate(self, name: str, target: tuple[str, str]) -> tuple[Json, Path, str]:
        argv: list[object] = ["isolate", "--configs"]
        for side in SIDES:
            task = self.root / "coordinator" / side / "hello-seats"
            if not task.exists():
                shutil.copytree(STOCK, task, symlinks=True)
            trial = {
                "schema_version": 1,
                "task": {"path": str(task)},
                "participants": TEAM,
            }
            trial = {**trial, "environment": WORLD, **self.changes[side]}
            argv += [task, save(task.parent / f"{name}-trial.json", trial)]
        _, out = call(self.capsys, *argv, *target, "--json")
        path = self.exchange / f"{name}.json"
        path.write_bytes(out.encode())
        return json.loads(out), path, digest(out.encode())

    def export(self, side: str, name: str, **changes: Any) -> Receipt:
        task = self.root / f"worker-{side}" / "checkout" / "tasks" / "hello-seats"
        if not task.exists():
            shutil.copytree(STOCK, task, symlinks=True)
        value = {**document(task, f"hello-{side}", **self.changes[side]), **changes}
        job = save(task.parents[1] / f"{name}-job.json", value)
        code, out = call(self.capsys, "worker-plan", "export", job)
        receipt = json.loads(out)
        assert code == 0 and receipt["schema"] == "seatlab.workbench.worker-plan/v1"
        assert receipt["trial_lock_record"] == only(
            native_keys(value), value["n_attempts"]
        )
        path = self.exchange / f"{name}.json"
        path.write_text(out)
        return Receipt(path, receipt, receipt["trial_lock_record"])

    def seal(self) -> None:
        for path in self.root.iterdir():
            if path != self.exchange:
                shutil.rmtree(path)


@pytest.fixture
def ws(
    tmp_path: Path, request: pytest.FixtureRequest, capsys: pytest.CaptureFixture[str]
) -> Iterator[Workspace]:
    yield Workspace(tmp_path.resolve(), getattr(request, "param", "channel"), capsys)
    assert not [
        p for p in tmp_path.rglob("*") if p.name in {"jobs", "trials", "lock.json"}
    ]


def check(ws: Workspace, **parts: object) -> subprocess.CompletedProcess[str]:
    p: dict[str, object] = {"reference": ws.reference, "pin": ws.pin, **ws.paths}
    p.update({"copies": 2, "concurrency": 2, "max_retries": 0, **parts})
    argv = ["worker-plan", "check", p["reference"], p["left"], p["right"]]
    argv += ["--reference-sha256", p["pin"], "--copies", p["copies"]]
    argv += ["--concurrency", p["concurrency"], "--max-retries", p["max_retries"]]
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-S",
            "-E",
            "-X",
            "utf8",
            "-c",
            GUARD,
            *map(str, argv),
            "--json",
        ],
        cwd=ROOT,
        env={},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def verdict(ws: Workspace, **parts: object) -> Json:
    process = check(ws, **parts)
    found = json.loads(process.stdout)
    assert found["schema"] == "seatlab.workbench.worker-plan-match/v1"
    assert (
        process.returncode
        == {"matches_reference": 0, "different_plan": 1}[found["status"]]
    )
    assert str(ws.root) not in process.stdout
    return found


@pytest.mark.unit
@pytest.mark.parametrize("ws", sorted(SETTINGS), indirect=True)
def test_relocated_workers_match_the_reference_by_their_own_native_keys(
    ws: Workspace,
) -> None:
    for side in SIDES:
        receipt = ws.receipts[side].value
        assert receipt["prepared_input"] == ws.found["inputs"][side]
        assert receipt["trial_lock_record"] != ws.found["trial_lock_records"][side]
        assert (receipt["copies"], receipt["concurrency"], receipt["max_retries"]) == (
            2,
            2,
            0,
        )
    ws.seal()
    found = verdict(ws)
    assert found["status"] == "matches_reference"
    assert found["trial_lock_records"] == ws.keys != ws.found["trial_lock_records"]


def _duplicate_key(ws: Workspace) -> Json:
    forged = {**ws.receipts["right"].value, "trial_lock_record": ws.keys["left"]}
    return {"right": save(ws.exchange / "forged.json", forged)}


DIFFERENT: dict[str, Callable[[Workspace], Json]] = {
    "swapped_arms": lambda ws: {"left": ws.paths["right"], "right": ws.paths["left"]},
    "both_schedules_drift": lambda ws: {
        side: ws.export(side, f"{side}-retry", retry={"max_retries": 1}).path
        for side in SIDES
    },
    "duplicate_worker_key": _duplicate_key,
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(DIFFERENT))
def test_other_receipts_or_schedules_are_a_different_plan(
    ws: Workspace, case: str
) -> None:
    parts = DIFFERENT[case](ws)
    ws.seal()
    found = verdict(ws, **parts)
    assert found["status"] == "different_plan" and found["reasons"]
    assert found["trial_lock_records"] is None


def _private_bool(ws: Workspace) -> Json:
    value = {**ws.receipts["right"].value, "max_retries": False, "job_name": SECRET}
    return {"right": save(ws.exchange / "bad.json", value)}


REFUSED: dict[str, Callable[[Workspace], Json]] = {
    "changed_pin": lambda ws: {"pin": "sha256:" + "0" * 64},
    "non_isolated_reference": lambda ws: {"reference": ws.failed, "pin": ws.failed_pin},
    "private_bool_receipt": _private_bool,
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(REFUSED))
def test_unusable_reference_or_receipt_is_refused_without_a_report(
    ws: Workspace, case: str
) -> None:
    parts = REFUSED[case](ws)
    ws.seal()
    process = check(ws, **parts)
    assert (process.returncode, process.stdout) == (2, "") and process.stderr.strip()
    assert SECRET not in process.stderr and str(ws.root) not in process.stderr


@pytest.mark.unit
def test_well_formed_schedule_claims_are_trusted_not_authenticated(
    ws: Workspace,
) -> None:
    honest = ws.export("right", "slow", n_concurrent_trials=1)
    claim = save(ws.exchange / "claim.json", {**honest.value, "concurrency": 2})
    ws.seal()
    assert verdict(ws, right=honest.path)["status"] == "different_plan"
    assert verdict(ws, right=claim)["status"] == "matches_reference"
