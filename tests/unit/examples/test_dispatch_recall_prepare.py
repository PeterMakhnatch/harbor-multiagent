import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from examples.native_study import native, normalize, record_bytes
from examples.seatlab.dispatch_recall import participant
from examples.seatlab.dispatch_recall.design import design
from examples.seatlab.dispatch_recall.prepare import (
    CONFIG,
    FILES,
    LOCK,
    ORACLE_ROSTER,
    RECEIPT,
    TASK_DIR,
    VERIFIER_ENV,
    prepare,
    records,
    team_kind,
    world_env,
)
from harbor.models.job.lock import TrialLock
from harbor.models.trial.config import TrialConfig

TASK = Path(__file__).resolve().parents[3] / "examples" / "tasks" / "dispatch-recall"
COMPOSE = "environment/docker-compose.yaml"


@pytest.fixture
def task(tmp_path: Path) -> Path:
    copy = tmp_path / "task" / TASK_DIR
    shutil.copytree(TASK, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return copy


@pytest.fixture
def plan() -> dict[str, Any]:
    return design([11, 12], 5)


@pytest.fixture
def endpoints() -> Iterator[dict[str, str]]:
    owner = Path(tempfile.mkdtemp(prefix="dr.", dir="/tmp")).resolve()
    try:
        yield {turn: str(owner / turn) for turn in participant.TURNS}
    finally:
        shutil.rmtree(owner, ignore_errors=True)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _adapter_roster(endpoints: dict[str, str]) -> dict[str, Any]:
    return {"participants": participant.roster(native(), endpoints)}


def test_scripted_control_writes_private_native_records(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    job = plan["jobs"][3]
    out = tmp_path / "prepared"
    pin = prepare(plan, job["job_id"], task, ORACLE_ROSTER, out)

    assert sorted(os.listdir(out)) == sorted(FILES)
    assert _mode(out) == 0o700
    assert all(_mode(out / name) == 0o600 for name in FILES)
    stored = {name: json.loads((out / name).read_text()) for name in FILES}
    config, receipt = stored[CONFIG], stored[RECEIPT]
    native_config = TrialConfig.model_validate(config)
    assert native_config.trial_name == job["job_id"]
    assert native_config.trials_dir == out.resolve() / "trials"
    assert config["environment"]["env"] == world_env(job["session"])
    assert config["verifier"] == {"env": VERIFIER_ENV}
    assert [m["seat"] for m in config["participants"]] == ["loader", "depot"]
    assert receipt["team"]["kind"] == "scripted_control"
    assert receipt["team"]["scripted"] is True
    assert receipt["team"]["free_team_eligible"] is False
    assert receipt["condition"] == job["session"]
    assert receipt["plan_sha256"] == hashlib.sha256(record_bytes(plan)).hexdigest()
    assert receipt["executions"] == 0 and receipt["model_calls"] == 0
    assert (
        pin["receipt_sha256"]
        == hashlib.sha256((out / RECEIPT).read_bytes()).hexdigest()
    )
    assert "condition" not in pin and "world_env" not in pin
    assert (
        TrialLock.model_validate(stored[LOCK]).participants
        == native_config.participants
    )

    again = records(
        plan,
        job["job_id"],
        task,
        {"participants": config["participants"]},
        out,
        live_endpoints=True,
    )
    assert all(record_bytes(again[name]) == (out / name).read_bytes() for name in FILES)


def test_existing_output_is_never_overwritten(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    prepare(plan, plan["jobs"][0]["job_id"], task, ORACLE_ROSTER, out)
    before = {name: (out / name).read_bytes() for name in FILES}
    with pytest.raises(ValueError, match="already exists"):
        prepare(plan, plan["jobs"][1]["job_id"], task, ORACLE_ROSTER, out)
    assert {name: (out / name).read_bytes() for name in FILES} == before


def test_host_adapter_accepts_absent_canonical_sockets(
    task: Path, plan: dict[str, Any], tmp_path: Path, endpoints: dict[str, str]
) -> None:
    out = tmp_path / "prepared"
    pin = prepare(
        plan, plan["jobs"][1]["job_id"], task, _adapter_roster(endpoints), out
    )
    receipt = json.loads((out / RECEIPT).read_text())
    assert pin["team_kind"] == "host_adapter" and pin["free_team_eligible"] is True
    assert receipt["team"]["scripted"] is False
    assert receipt["team"]["owner_endpoints"] == endpoints
    assert not any(os.path.lexists(path) for path in endpoints.values())

    config = json.loads((out / CONFIG).read_text())
    shutil.rmtree(Path(endpoints["stage"]).parent)
    roster = {"participants": config["participants"]}
    job_id = plan["jobs"][1]["job_id"]
    with pytest.raises(ValueError, match="owner endpoint"):
        records(plan, job_id, task, roster, out, live_endpoints=True)
    again = records(plan, job_id, task, roster, out, live_endpoints=False)
    assert all(record_bytes(again[name]) == (out / name).read_bytes() for name in FILES)


def _installed(seat: str) -> dict[str, Any]:
    return {"seat": seat, "name": "claude-code", "model_name": "anthropic/model"}


def _oracle(seat: str) -> dict[str, Any]:
    return {"seat": seat, "name": "oracle"}


@pytest.mark.parametrize(
    ("participants", "message"),
    [
        ([_installed("loader"), _installed("depot")], "offline"),
        ([_oracle("depot"), _oracle("loader")], "loader then depot"),
        ([_oracle("loader")], "participants"),
        ([{"seat": "loader"}, _oracle("depot")], "names no agent"),
        ([{**_oracle("loader"), "env": {"A": "1"}}, _oracle("depot")], "unsupported"),
        (
            [
                {**_oracle("loader"), "kwargs": {"note": "RECALL_SEED"}},
                _oracle("depot"),
            ],
            "world setting",
        ),
        (
            [{**_oracle("loader"), "kwargs": {"api_key": "x"}}, _oracle("depot")],
            "credential",
        ),
        ([{**_oracle("loader"), "model_name": "a/b"}, _oracle("depot")], "offline"),
    ],
)
def test_unsupported_rosters_are_refused(
    task: Path,
    plan: dict[str, Any],
    tmp_path: Path,
    participants: list[dict[str, Any]],
    message: str,
) -> None:
    out = tmp_path / "prepared"
    with pytest.raises(ValueError, match=message):
        prepare(
            plan, plan["jobs"][0]["job_id"], task, {"participants": participants}, out
        )
    assert not os.path.lexists(out)


def test_mixed_and_misbound_adapter_teams_are_refused(
    task: Path, plan: dict[str, Any], tmp_path: Path, endpoints: dict[str, str]
) -> None:
    loader, depot = _adapter_roster(endpoints)["participants"]
    job_id = plan["jobs"][0]["job_id"]
    with pytest.raises(ValueError, match="offline"):
        prepare(
            plan,
            job_id,
            task,
            {"participants": [loader, _oracle("depot")]},
            tmp_path / "a",
        )
    inside = {**endpoints, "report": str(tmp_path.resolve() / "b" / "report")}
    with pytest.raises(ValueError, match="report owner endpoint"):
        prepare(plan, job_id, task, _adapter_roster(inside), tmp_path / "b")
    swapped = {**depot, "kwargs": {**depot["kwargs"], "role": "loader"}}
    with pytest.raises(ValueError):
        prepare(plan, job_id, task, {"participants": [loader, swapped]}, tmp_path / "c")
    assert not any(os.path.lexists(tmp_path / name) for name in "abc")


def test_unknown_job_and_altered_plan_are_refused(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="no such assignment"):
        prepare(plan, "dr-" + "0" * 24, task, ORACLE_ROSTER, tmp_path / "a")
    altered = json.loads(json.dumps(plan))
    altered["jobs"][0]["session"]["timing"] = "immediate"
    with pytest.raises(ValueError, match="re-derived"):
        prepare(
            altered, altered["jobs"][0]["job_id"], task, ORACLE_ROSTER, tmp_path / "b"
        )


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


@pytest.mark.parametrize(
    ("relative", "old", "new", "message"),
    [
        (
            COMPOSE,
            "  loader:\n    <<: *seat\n",
            "  loader:\n    <<: *seat\n    environment: {SEAT: loader}\n",
            "service environment",
        ),
        (
            COMPOSE,
            "  network_mode: none\n  depends_on:",
            "  depends_on:",
            "not offline",
        ),
        (
            COMPOSE,
            '"loader-rpc:/run/recall-seat:ro"',
            '"loader-rpc:/run/recall-seat"',
            "writable",
        ),
        (
            "task.toml",
            'RECALL_TIMING = "immediate"\n',
            'RECALL_TIMING = "immediate"\nRECALL_EXTRA = "1"\n',
            "world settings",
        ),
        (
            "task.toml",
            "[agents.depot]",
            "[agents.depot]\nunder_test = false",
            "not under test",
        ),
    ],
)
def test_task_drift_is_refused(
    task: Path,
    plan: dict[str, Any],
    tmp_path: Path,
    relative: str,
    old: str,
    new: str,
    message: str,
) -> None:
    _edit(task / relative, old, new)
    with pytest.raises(ValueError, match=message):
        prepare(plan, plan["jobs"][0]["job_id"], task, ORACLE_ROSTER, tmp_path / "out")
    assert not os.path.lexists(tmp_path / "out")


def test_task_bytecode_is_refused(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    cache = task / "environment" / "__pycache__"
    cache.mkdir()
    (cache / "recall_world.cpython-313.pyc").write_bytes(b"\0")
    with pytest.raises(ValueError, match="bytecode"):
        prepare(plan, plan["jobs"][0]["job_id"], task, ORACLE_ROSTER, tmp_path / "out")


def test_team_kind_labels_scripted_controls() -> None:
    harbor = native()
    oracle = [normalize(harbor, _oracle(s)) for s in ("loader", "depot")]
    nop = [normalize(harbor, {"seat": s, "name": "nop"}) for s in ("loader", "depot")]
    assert team_kind(oracle) == "scripted_control"
    assert team_kind(nop) == "scripted_control"
    other = [
        {
            **{k: v for k, v in m.items() if k != "name"},
            "import_path": "examples.other:Agent",
        }
        for m in oracle
    ]
    with pytest.raises(ValueError, match="offline"):
        team_kind(other)
