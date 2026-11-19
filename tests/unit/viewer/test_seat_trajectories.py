"""Viewer API for multi-agent (seat) trials: per-seat trajectories and logs."""

import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harbor.models.trial.config import Participant, TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.viewer import server
from harbor.viewer.server import REFUSED_LOG, create_app
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
        # An agent given by import path reports its own name.
        name=participant.name or "custom",
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


SECRET = "host secret"


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory outside the trial holding the files an agent dir holds."""
    link = tmp_path / "symlink-check"
    try:
        link.symlink_to("missing")
    except OSError as error:
        pytest.skip(f"Symlinks are unavailable: {error}")
    link.unlink()
    outside = tmp_path / "outside"
    for relative in (
        "trajectory.json",
        "oracle.txt",
        "setup/stdout.txt",
        "command-0/stdout.txt",
        "analysis.md",
    ):
        file = outside / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"secret": SECRET}))
    return outside


def _replace_with_link(path: Path, target: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target, target_is_directory=target.is_dir())


@pytest.mark.parametrize(
    ("seat", "link", "target"),
    [
        ("p1", "seats/p1/agent/trajectory.json", "trajectory.json"),
        ("p1", "seats/p1/agent", ""),
        (None, "agent/trajectory.json", "trajectory.json"),
    ],
)
def test_a_trajectory_that_links_out_of_the_trial_is_refused(
    client: TestClient,
    seat_trial: Path,
    outside: Path,
    seat: str | None,
    link: str,
    target: str,
) -> None:
    """The file is checked at its final path, below a valid seat root too."""
    _replace_with_link(seat_trial / link, outside / target)

    response = client.get(
        f"{BASE}/trajectory", params={"seat": seat} if seat is not None else {}
    )

    assert response.status_code == 403
    assert SECRET not in response.text


def _agent_logs(client: TestClient, seat: str | None) -> dict:
    response = client.get(
        f"{BASE}/agent-logs", params={"seat": seat} if seat is not None else {}
    )
    assert response.status_code == 200
    assert SECRET not in response.text
    return response.json()


@pytest.mark.parametrize(
    ("link", "target", "refused"),
    [
        ("seats/p2/agent/oracle.txt", "oracle.txt", {"oracle"}),
        ("seats/p2/agent/setup/stdout.txt", "setup/stdout.txt", {"setup"}),
        (
            "seats/p2/agent/command-0/stdout.txt",
            "command-0/stdout.txt",
            {"command-0"},
        ),
        ("seats/p2/agent", "", {"oracle", "setup", "command-0"}),
        ("analysis.md", "analysis.md", {"summary"}),
    ],
)
def test_a_log_that_links_out_of_the_trial_is_refused_alone(
    client: TestClient,
    seat_trial: Path,
    outside: Path,
    link: str,
    target: str,
    refused: set[str],
) -> None:
    """Each log is checked at its final path. A log that links out gets a
    marker, and the other logs still come back."""
    agent = seat_trial / "seats" / "p2" / "agent"
    for relative, text in {
        "oracle.txt": "p2 oracle",
        "setup/stdout.txt": "p2 setup",
        "command-0/stdout.txt": "p2 command",
    }.items():
        (agent / relative).parent.mkdir(parents=True, exist_ok=True)
        (agent / relative).write_text(text)
    (seat_trial / "analysis.md").write_text("trial summary")
    _replace_with_link(seat_trial / link, outside / target)

    logs = _agent_logs(client, "p2")

    entries = {
        "oracle": logs["oracle"],
        "setup": logs["setup"],
        "summary": logs["summary"],
        **{f"command-{c['index']}": c["content"] for c in logs["commands"]},
    }
    expected = {
        "oracle": "p2 oracle",
        "setup": "p2 setup",
        "summary": "trial summary",
        "command-0": "p2 command",
    }
    assert entries == expected | dict.fromkeys(refused, REFUSED_LOG)


def test_one_command_log_that_links_out_does_not_hide_the_next(
    client: TestClient, seat_trial: Path, outside: Path
) -> None:
    agent = seat_trial / "seats" / "p2" / "agent"
    (agent / "command-0").mkdir()
    (agent / "command-0" / "stdout.txt").write_text("ran 0")
    _replace_with_link(agent / "command-1", outside / "command-0")
    (agent / "command-2").mkdir()
    (agent / "command-2" / "stdout.txt").write_text("ran 2")

    logs = _agent_logs(client, "p2")

    assert logs["commands"] == [
        {"index": 0, "content": "ran 0"},
        {"index": 1, "content": REFUSED_LOG},
        {"index": 2, "content": "ran 2"},
    ]


def test_a_root_log_that_links_out_is_refused_alone(
    client: TestClient, seat_trial: Path, outside: Path
) -> None:
    """The root agent/ of an ordinary trial gets the same check."""
    (seat_trial / "agent" / "setup").mkdir()
    (seat_trial / "agent" / "setup" / "stdout.txt").write_text("root setup")
    _replace_with_link(seat_trial / "agent" / "oracle.txt", outside / "oracle.txt")

    logs = _agent_logs(client, None)

    assert (logs["oracle"], logs["setup"]) == (REFUSED_LOG, "root setup")


def test_a_link_inside_the_trial_is_followed(
    client: TestClient, seat_trial: Path, outside: Path
) -> None:
    """As in the file route, the boundary is the trial, not the seat."""
    _replace_with_link(
        seat_trial / "seats" / "p1" / "agent" / "trajectory.json",
        Path("..", "..", "p2", "agent", "trajectory.json"),
    )

    response = client.get(f"{BASE}/trajectory", params={"seat": "p1"})

    assert response.status_code == 200
    assert response.json()["session_id"] == f"{TRIAL}__p2"


# p1, under test, and p2, supporting, with their own reasoning effort.
P1_HIGH = Participant(
    seat="p1",
    name="terminus-2",
    model_name="openrouter/deepseek/deepseek-v4-flash",
    kwargs={"reasoning_effort": "high"},
)
P2_LOW = Participant(
    seat="p2",
    name="claude-code",
    model_name="anthropic/claude-test",
    kwargs={"reasoning_effort": "low"},
)


def test_finished_trial_is_labelled_by_its_first_seat_under_test(
    tmp_path: Path,
) -> None:
    """agent_info names p1, the first seat under test, wherever the config
    lists its participant. The reasoning effort that labels and filters the
    trial is p1's too, not that of p2, the supporting seat listed first."""
    write_seat_trial(tmp_path / JOB, (P2_LOW, P1_HIGH))
    client = TestClient(create_app(tmp_path))

    (summary,) = client.get(f"/api/jobs/{JOB}/trials").json()["items"]
    filters = client.get(f"/api/jobs/{JOB}/tasks/filters").json()
    high = client.get(f"/api/jobs/{JOB}/trials", params={"reasoning_effort": "high"})
    low = client.get(f"/api/jobs/{JOB}/trials", params={"reasoning_effort": "low"})

    assert (
        summary["agent_name"],
        summary["model_provider"],
        summary["model_name"],
        summary["reasoning_effort"],
    ) == ("terminus-2", "openrouter", "deepseek/deepseek-v4-flash", "high")
    assert [option["value"] for option in filters["reasoning_efforts"]] == ["high"]
    assert [item["name"] for item in high.json()["items"]] == [TRIAL]
    assert low.json()["items"] == []


def test_a_custom_agent_under_test_keeps_its_import_path_label(
    tmp_path: Path,
) -> None:
    """As for a single agent, an agent given by import path is labelled by
    that path, while its trial runs and once it has a result."""
    custom = Participant(
        seat="p1", import_path="my_agents:Custom", model_name="openai/gpt-test"
    )
    write_seat_trial(tmp_path / JOB, (P2_LOW, custom))
    write_seat_trial(
        tmp_path / JOB, (custom, P2_LOW), name="hello-seats__running", finished=False
    )
    client = TestClient(create_app(tmp_path))

    labels = {
        item["name"]: (item["agent_name"], item["reported_agent_name"])
        for item in client.get(f"/api/jobs/{JOB}/trials").json()["items"]
    }

    assert labels == {
        TRIAL: ("my_agents:Custom", "custom"),
        "hello-seats__running": ("my_agents:Custom", None),
    }


@pytest.mark.parametrize(
    ("route", "name"), [("trajectory", "trajectory.json"), ("agent-logs", "oracle.txt")]
)
def test_the_read_opens_the_checked_path(
    client: TestClient,
    seat_trial: Path,
    outside: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    name: str,
) -> None:
    """The handler opens the final path that _trial_file checked, so the
    requested link can change after the check without redirecting the read.
    The test swaps that link right after the check."""
    agent = seat_trial / "seats" / "p1" / "agent"
    (agent / "checked").write_text(json.dumps({"session_id": "checked"}))
    _replace_with_link(agent / name, Path("checked"))
    check = server._trial_file

    def check_then_swap(trial_dir: Path, path: Path) -> Path | None:
        checked = check(trial_dir, path)
        if path.name == name:
            _replace_with_link(path, outside / name)
        return checked

    monkeypatch.setattr(server, "_trial_file", check_then_swap)

    response = client.get(f"{BASE}/{route}", params={"seat": "p1"})

    assert response.status_code == 200
    assert SECRET not in response.text
    body = response.json()
    read = body if route == "trajectory" else json.loads(body["oracle"])
    assert read == {"session_id": "checked"}


@pytest.mark.parametrize("route", ["trajectory", "agent-logs"])
@pytest.mark.parametrize("params", [{"seat": "p1\x00x"}, {"step": "one\x00x"}])
def test_a_nul_byte_in_seat_or_step_is_refused(
    client: TestClient, route: str, params: dict[str, str]
) -> None:
    """A path cannot hold a NUL byte; Path.resolve raises ValueError on one."""
    response = client.get(f"{BASE}/{route}", params=params)

    assert response.status_code == 400


def _swap_after_check(
    monkeypatch: pytest.MonkeyPatch, tail: str, swap: Callable[[], None]
) -> None:
    """Run ``swap`` once, right after _trial_file checks the first path that
    ends with ``tail``."""
    check = server._trial_file
    swapped: list[Path] = []

    def check_then_swap(trial_dir: Path, path: Path) -> Path | None:
        checked = check(trial_dir, path)
        if not swapped and path.match(tail):
            swapped.append(path)
            swap()
        return checked

    monkeypatch.setattr(server, "_trial_file", check_then_swap)


@pytest.mark.parametrize(
    "case",
    [
        "checked-file-becomes-a-link",
        "target-of-the-requested-link-becomes-a-link",
        "seat-agent-dir-becomes-a-link",
        "command-dir-becomes-a-link",
        "log-file-becomes-a-link",
    ],
)
def test_a_swap_after_the_check_is_refused(
    client: TestClient,
    seat_trial: Path,
    outside: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """The handler opens each part of the checked path without following a
    link. A file or folder on that path that becomes a link to outside the
    trial after the check, as it can in a live agent/ mount, fails the open."""
    root_agent = seat_trial / "agent"
    p1_agent = seat_trial / "seats" / "p1" / "agent"
    (root_agent / "command-0").mkdir()
    (root_agent / "command-0" / "stdout.txt").write_text("ran")
    (root_agent / "oracle.txt").write_text("oracle")
    (root_agent / "trajectory.json").write_text(json.dumps({"session_id": "root"}))
    (p1_agent / "checked").write_text(json.dumps({"session_id": "checked"}))
    _replace_with_link(p1_agent / "trajectory.json", Path("checked"))
    route, seat, tail, swapped, target, entry = {
        "checked-file-becomes-a-link": (
            "trajectory",
            None,
            "trajectory.json",
            root_agent / "trajectory.json",
            outside / "trajectory.json",
            None,
        ),
        "target-of-the-requested-link-becomes-a-link": (
            "trajectory",
            "p1",
            "trajectory.json",
            p1_agent / "checked",
            outside / "trajectory.json",
            None,
        ),
        "seat-agent-dir-becomes-a-link": (
            "trajectory",
            "p1",
            "trajectory.json",
            p1_agent,
            outside,
            None,
        ),
        "command-dir-becomes-a-link": (
            "agent-logs",
            None,
            "command-0/stdout.txt",
            root_agent / "command-0",
            outside / "command-0",
            "command-0",
        ),
        "log-file-becomes-a-link": (
            "agent-logs",
            None,
            "oracle.txt",
            root_agent / "oracle.txt",
            outside / "oracle.txt",
            "oracle",
        ),
    }[case]
    _swap_after_check(monkeypatch, tail, lambda: _replace_with_link(swapped, target))

    response = client.get(
        f"{BASE}/{route}", params={"seat": seat} if seat is not None else {}
    )

    assert SECRET not in response.text
    if entry is None:
        assert response.status_code == 403
    else:
        logs = response.json()
        read = {
            "oracle": logs["oracle"],
            **{f"command-{c['index']}": c["content"] for c in logs["commands"]},
        }
        assert read[entry] == REFUSED_LOG


def _open_fifo_writer(fifo: Path) -> None:
    try:
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    except OSError:
        # No reader is blocked on the FIFO.
        pass


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
@pytest.mark.parametrize(
    ("route", "seat", "relative", "entry"),
    [
        ("trajectory", "p1", "seats/p1/agent/trajectory.json", None),
        ("agent-logs", "p2", "seats/p2/agent/oracle.txt", "oracle"),
    ],
)
def test_a_fifo_does_not_block_the_request(
    client: TestClient,
    seat_trial: Path,
    route: str,
    seat: str,
    relative: str,
    entry: str | None,
) -> None:
    """A FIFO is not a regular file: it is refused at once. A read that blocked
    on it would wait for a writer; the timer opens one after 5 s, so a
    regression fails here instead of hanging."""
    fifo = seat_trial / relative
    fifo.unlink(missing_ok=True)
    os.mkfifo(fifo)
    timer = threading.Timer(5, _open_fifo_writer, args=(fifo,))
    timer.start()
    started = time.monotonic()
    try:
        response = client.get(f"{BASE}/{route}", params={"seat": seat})
    finally:
        timer.cancel()

    assert time.monotonic() - started < 5, "the read waited for a FIFO writer"
    if entry is None:
        assert response.status_code == 403
    else:
        assert response.json()[entry] == REFUSED_LOG


def test_without_no_follow_support_the_file_is_read_by_name(
    client: TestClient, seat_trial: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows has no O_NOFOLLOW or dir_fd for os.open; there the handlers
    read the checked path by name."""
    monkeypatch.setattr(server, "_NO_FOLLOW_OPEN", False)
    (seat_trial / "seats" / "p2" / "agent" / "oracle.txt").write_text("p2 posted\n")

    trajectory = client.get(f"{BASE}/trajectory", params={"seat": "p1"})
    logs = client.get(f"{BASE}/agent-logs", params={"seat": "p2"})

    assert trajectory.json()["session_id"] == f"{TRIAL}__p1"
    assert logs.json()["oracle"] == "p2 posted\n"
