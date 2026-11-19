"""Viewer API for multi-agent (seat) trials: per-seat trajectories and logs."""

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harbor.models.trial.config import Participant, TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.viewer.server import create_app
from harbor.viewer.trial_utils import model_info_from_model_name

JOB = "seat-job"
TRIAL = "hello-seats__seats"
BASE = f"/api/jobs/{JOB}/trials/{TRIAL}"
# As in examples/tasks/hello-seats, p1 is under test and p2 supports it.
UNDER_TEST = {"p1": True, "p2": False}
P1 = Participant(
    seat="p1", name="terminus-2", model_name="openrouter/deepseek/deepseek-v4-flash"
)
P2 = Participant(seat="p2", name="claude-code", model_name="anthropic/claude-test")


def _agent_info(participant: Participant) -> AgentInfo:
    return AgentInfo(
        name=participant.name or "",
        version="1.0",
        model_info=model_info_from_model_name(participant.model_name),
    )


def write_seat_trial(
    job_dir: Path,
    participants: Sequence[Participant] = (P1, P2),
    *,
    name: str = TRIAL,
    finished: bool = True,
) -> Path:
    """A seat trial as SeatsTrial leaves it: config.json without ``agent``,
    each seat's trajectory in seats/<seat>/agent/ and an empty root agent/.
    A finished trial also has result.json, whose seat_results are in the
    task's seat order and whose agent_info is p1's."""
    config = TrialConfig(
        task=TaskConfig(path=Path("/tmp/hello-seats")),
        trial_name=name,
        participants=list(participants),
    )
    trial_dir = job_dir / name
    (trial_dir / "agent").mkdir(parents=True)
    (trial_dir / "config.json").write_text(config.model_dump_json(exclude={"agent"}))
    for participant in participants:
        agent_dir = trial_dir / "seats" / participant.seat / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "trajectory.json").write_text(
            json.dumps({"session_id": f"{name}__{participant.seat}", "steps": []})
        )
    if finished:
        by_seat = {participant.seat: participant for participant in participants}
        result = TrialResult(
            task_name="hello-seats",
            trial_name=name,
            trial_uri=trial_dir.resolve().as_uri(),
            task_id=config.task.get_task_id(),
            task_checksum="deadbeef",
            config=config,
            agent_info=_agent_info(by_seat["p1"]),
            seat_results=[
                SeatResult(
                    seat=seat,
                    under_test=under_test,
                    agent_info=_agent_info(by_seat[seat]),
                )
                for seat, under_test in UNDER_TEST.items()
            ],
            verifier_result=VerifierResult(rewards={"reward": 1}),
            started_at=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 17, 10, 5, tzinfo=timezone.utc),
        )
        (trial_dir / "result.json").write_text(result.model_dump_json())
    return trial_dir


@pytest.fixture
def seat_trial(tmp_path: Path) -> Path:
    return write_seat_trial(tmp_path / JOB)


@pytest.fixture
def client(tmp_path: Path, seat_trial: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_trial_result_names_the_seats(client: TestClient) -> None:
    response = client.get(BASE)

    assert response.status_code == 200
    assert [
        (seat["seat"], seat["under_test"]) for seat in response.json()["seat_results"]
    ] == [("p1", True), ("p2", False)]


@pytest.mark.parametrize("seat", ["p1", "p2"])
def test_trajectory_for_a_seat(client: TestClient, seat: str) -> None:
    response = client.get(f"{BASE}/trajectory", params={"seat": seat})

    assert response.status_code == 200
    assert response.json()["session_id"] == f"{TRIAL}__{seat}"


def test_trajectory_without_seat_reads_the_root_agent(client: TestClient) -> None:
    # A seat trial's root agent/ is empty, as before this parameter existed.
    response = client.get(f"{BASE}/trajectory")

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"seat": "../agent"}, 400),
        ({"seat": ".."}, 400),
        ({"seat": "p1/agent"}, 400),
        ({"seat": "p9"}, 404),
        ({"seat": "p1", "step": "one"}, 400),
    ],
)
def test_trajectory_refuses_a_bad_seat(
    client: TestClient, params: dict[str, str], status: int
) -> None:
    response = client.get(f"{BASE}/trajectory", params=params)

    assert response.status_code == status


def test_agent_logs_for_a_seat(client: TestClient, seat_trial: Path) -> None:
    (seat_trial / "seats" / "p2" / "agent" / "oracle.txt").write_text("p2 posted\n")

    seat_logs = client.get(f"{BASE}/agent-logs", params={"seat": "p2"})
    root_logs = client.get(f"{BASE}/agent-logs")

    assert seat_logs.status_code == 200
    assert seat_logs.json()["oracle"] == "p2 posted\n"
    assert root_logs.json()["oracle"] is None


def test_in_progress_seat_trial_is_labelled_by_its_participants(
    tmp_path: Path, seat_trial: Path
) -> None:
    """Before result.json, a seat trial's config has participants and the
    default agent (oracle), which never runs."""
    write_seat_trial(tmp_path / JOB, name="hello-seats__running", finished=False)
    client = TestClient(create_app(tmp_path))

    trial = client.get(f"/api/jobs/{JOB}/trials/hello-seats__running").json()
    listed = {
        item["name"]: item
        for item in client.get(f"/api/jobs/{JOB}/trials").json()["items"]
    }

    assert trial["agent_info"]["name"] == "terminus-2"
    assert trial["agent_info"]["model_info"] == {
        "name": "deepseek/deepseek-v4-flash",
        "provider": "openrouter",
    }
    running_summary = listed["hello-seats__running"]
    assert (
        running_summary["agent_name"],
        running_summary["model_provider"],
        running_summary["model_name"],
    ) == ("terminus-2", "openrouter", "deepseek/deepseek-v4-flash")
