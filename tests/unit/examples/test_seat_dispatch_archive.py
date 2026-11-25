import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from examples.native_study import record_bytes
from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp
from examples.seatlab.physical_dispatch import certificate, cohort, policies, stage
from examples.seatlab.physical_dispatch.physical_evidence import inspect_trial
from examples.seatlab.physical_dispatch.receiver import DispatchReceiver
from examples.seatlab.physical_dispatch.receiver_plan import CELLS
from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TrialLock
from harbor.models.task.task import Task
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import SeatResult, TimingInfo, TrialResult
from tests.unit.examples.test_exhibit_relay_capture import (
    END,
    START,
    write_native_archive,
)
from tests.unit.examples.test_seat_dispatch_receiver import listener, no_connection

ROLES = ("loader", "registrar", "depot")
TURNS = (*ROLES, "loader")
PHASES = ("request", "registrar", "depot", "dispatch")
ROLE_PHASES = {
    "loader": ("request", "dispatch"),
    "registrar": ("registrar",),
    "depot": ("depot",),
}
OPPOSITE_FILING = {"other": "relevant", "relevant": "other"}
RECEIVER_IDENTITY = ("seatlab-physical-dispatch-receiver", "1.0.0", None)
LOCAL_ARCHIVES = "local-engineering-archives-not-harbor-trials"

Metadata = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Scenario:
    cell: str
    turns: int
    dispatch: str | None
    status: str
    reward: int | None

    @property
    def map_name(self) -> str:
        return self.cell.split("-")[0]

    @property
    def filing(self) -> str:
        return self.cell.split("-")[1]


SCENARIOS = {
    "followed-dispatch": Scenario("original-relevant", 4, "follow", "complete", 1),
    "same-bay-dispatch": Scenario("swapped-other", 4, "same-bay", "complete", 0),
    "before-dispatch": Scenario("swapped-relevant", 3, None, "incomplete", None),
    "after-request": Scenario("original-other", 1, None, "incomplete", None),
}


@dataclass(frozen=True)
class Replay:
    cut: dict[str, Any]
    metadata: Metadata
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class Archive:
    scenario: Scenario
    trial: Path
    replay: Replay


@dataclass(frozen=True)
class Prepared:
    plan: Path
    study: Path
    pin: str
    instructions: dict[str, str]
    cases: dict[str, dict[str, Any]]
    archives: dict[str, Archive]


def dispatch_action(view: dict[str, Any], kind: str | None) -> dict[str, Any]:
    if kind == "follow":
        action = policies.follow(view)
        assert action is not None
        return action
    assert kind == "same-bay"
    return {
        "op": "commit",
        "routes": {item: min(view["bays"]) for item in view["order"]["items"]},
    }


def replay(
    case: dict[str, Any],
    filing: str,
    turns: int,
    dispatch: str | None,
    instructions: dict[str, str],
) -> Replay:
    assert 0 <= turns <= len(TURNS)
    session = certificate.Session(deepcopy(case), "linked")
    metadata: Metadata = {
        role: {
            "role": role,
            "phase": ROLE_PHASES[role][0],
            "accepted_actions": [],
            "accepted_requests": [],
        }
        for role in ROLES
    }
    rows = []
    for step, (role, phase) in enumerate(
        zip(TURNS[:turns], PHASES[:turns], strict=True)
    ):
        owner = metadata[role]
        actions = owner["accepted_actions"]
        view = json.loads(json.dumps(session.view(role), sort_keys=True))
        assert view["phase"] == phase and view["turn"] == role
        stamp = request_stamp(
            encode_request(
                render_turn(instructions[role], view, actions[-1] if actions else None)
            )
        )
        if phase == "request":
            action = policies.nominate(view)
        elif phase == "registrar":
            action = policies.registrar(view)
        elif phase == "depot":
            action = policies.depot(view, filing)
        else:
            action = dispatch_action(view, dispatch)
        session.apply(role, action)
        actions.append(action)
        owner["accepted_requests"].append(stamp)
        done = len(actions)
        owner["phase"] = (
            ROLE_PHASES[role][done] if done < len(ROLE_PHASES[role]) else "finished"
        )
        rows.append({"seq": step + 1, "role": role, "phase": phase, "request": stamp})
    cut = session.snapshot()
    assert len(cut["events"]) == turns
    return Replay(cut, metadata, rows)


def write_verifier(trial: Path, cut: dict[str, Any]) -> dict[str, Any]:
    paths = TrialPaths(trial)
    observation = certificate.observation(cut)
    paths.verifier_dir.mkdir(parents=True, exist_ok=True)
    (paths.verifier_dir / "observation.json").write_bytes(record_bytes(observation))
    cut_path = paths.verifier_dir / "private-exhibit-cut.json"
    cut_path.write_bytes(record_bytes(cut))
    cut_path.chmod(0o600)
    paths.reward_json_path.unlink(missing_ok=True)
    if observation["status"] == "complete":
        paths.reward_json_path.write_bytes(
            record_bytes({"reward": observation["reward"]})
        )
    return observation


def seat_results(
    config: TrialConfig, metadata: Metadata, logs: Path
) -> list[SeatResult]:
    seats = []
    for member in config.participants:
        owner = metadata[member.seat]
        info = DispatchReceiver(logs, **member.kwargs).to_agent_info()
        assert (info.name, info.version, info.model_info) == RECEIVER_IDENTITY
        seats.append(
            SeatResult(
                seat=member.seat,
                under_test=True,
                agent_info=info,
                agent_result=AgentContext(metadata=deepcopy(owner)),
                agent_execution=TimingInfo(started_at=START, finished_at=END),
                execution_disposition="returned"
                if owner["phase"] == "finished"
                else "cancelled",
            )
        )
    assert tuple(seat.seat for seat in seats) == ROLES
    return seats


def materialize(
    plan: Path,
    archive_root: Path,
    name: str,
    scenario: Scenario,
    cases: dict[str, dict[str, Any]],
    instructions: dict[str, str],
) -> Archive:
    config = TrialConfig.model_validate_json(
        (plan / f"{scenario.cell}.config.json").read_bytes()
    )
    lock = TrialLock.model_validate_json(
        (plan / f"{scenario.cell}.lock.json").read_bytes()
    )
    assert config.verifier.env == {"EXHIBIT_RETAIN_CUT": "1"}
    assert "EXHIBIT_RETAIN_CUT" not in config.environment.env
    played = replay(
        cases[scenario.map_name],
        scenario.filing,
        scenario.turns,
        scenario.dispatch,
        instructions,
    )
    paths = TrialPaths(config.trials_dir / config.trial_name)
    observation = write_verifier(paths.trial_dir, played.cut)
    assert (observation["status"], observation["reward"]) == (
        scenario.status,
        scenario.reward,
    )
    seats = seat_results(config, played.metadata, archive_root / "receiver-logs")
    write_native_archive(config, lock, seats, observation, paths, START, END)
    fixture_result = TrialResult.model_validate_json(paths.result_path.read_bytes())
    fixture_result.id = UUID(int=1 + CELLS.index(scenario.cell))
    paths.result_path.write_text(fixture_result.model_dump_json(), encoding="utf-8")
    return Archive(scenario, paths.trial_dir, played)


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Prepared]:
    with ExitStack() as stack:
        patch = stack.enter_context(pytest.MonkeyPatch.context())
        patch.setattr(sys, "dont_write_bytecode", True)
        base = tmp_path_factory.mktemp("seat-dispatch-archive").resolve()
        task = base / "task"
        stage.materialize(task)
        sockets_dir = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(prefix="dispatch-", dir="/tmp")
            )
        ).resolve()
        endpoints = {cell: str(sockets_dir / cell) for cell in CELLS}
        sockets = [listener(stack, Path(path)) for path in endpoints.values()]
        study = base / "study"
        receipt = cohort.enroll(study, 4, 7)
        pin = receipt["cohort_digest"]
        design, _ = cohort.load_design(study, pin)
        plan = base / LOCAL_ARCHIVES / "case-0001"
        plan.parent.mkdir(mode=0o700)
        bound = cohort.bind_case(study, "case-0001", pin, task, plan, endpoints)
        assert bound["executions"] == 0 and bound["sampling"] == "development_only"
        with pytest.raises(ValueError):
            cohort.check_slot(study, "case-0002", pin, plan)
        native_task = Task(task)
        instructions = {role: native_task.seat_instruction(role) for role in ROLES}
        _, cases = certificate.native_pair(design["cases"][0]["seed"])
        archive_root = base / "engineering-receiver-logs"
        archives = {
            name: materialize(plan, archive_root, name, scenario, cases, instructions)
            for name, scenario in SCENARIOS.items()
        }
        yield Prepared(plan, study, pin, instructions, cases, archives)
        for sock in sockets:
            no_connection(sock)


def copy_archive(archive: Archive, tmp_path: Path) -> Path:
    trial = tmp_path.resolve() / "relocated"
    shutil.copytree(archive.trial, trial)
    return trial


def rewrite_result(trial: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = TrialPaths(trial).result_path
    raw = json.loads(path.read_bytes())
    change(raw)
    path.write_text(json.dumps(raw))


def loader_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    loader = next(seat for seat in raw["seat_results"] if seat["seat"] == "loader")
    return loader["agent_result"]["metadata"]


@pytest.mark.parametrize("name", tuple(SCENARIOS))
def test_inspection_reports_native_physical_dispatch(
    prepared: Prepared, name: str
) -> None:
    archive = prepared.archives[name]
    scenario = archive.scenario
    report = inspect_trial(prepared.plan, scenario.cell, archive.trial)
    assert report["native_trial_id"] == str(UUID(int=1 + CELLS.index(scenario.cell)))
    assert report["recorded_exception_count"] == 0
    assert report["status"] == scenario.status
    if scenario.reward is None:
        assert report["native_reward"] is None
    else:
        assert type(report["native_reward"]) is int
        assert report["native_reward"] == scenario.reward
    assert report["final_request_matched"] is (scenario.turns == len(TURNS))
    assert report["accepted_turns"] == archive.replay.rows
    assert [row["phase"] for row in report["accepted_turns"]] == list(
        PHASES[: scenario.turns]
    )


@pytest.mark.parametrize(
    "substitute", ("no-prior-action", "depot-prior-action", "opposite-filing-view")
)
def test_substituted_final_request_is_rejected(
    prepared: Prepared, tmp_path: Path, substitute: str
) -> None:
    archive = prepared.archives["followed-dispatch"]
    scenario = archive.scenario
    case = prepared.cases[scenario.map_name]
    instruction = prepared.instructions["loader"]
    prefix, view = certificate._prefix(deepcopy(case), scenario.filing)
    if substitute == "no-prior-action":
        packet = encode_request(render_turn(instruction, view, None))
    elif substitute == "depot-prior-action":
        packet = encode_request(render_turn(instruction, view, prefix[2]))
    else:
        other_prefix, other_view = certificate._prefix(
            deepcopy(case), OPPOSITE_FILING[scenario.filing]
        )
        packet = encode_request(render_turn(instruction, other_view, other_prefix[0]))
    stamp = request_stamp(packet)
    assert stamp != archive.replay.metadata["loader"]["accepted_requests"][1]
    trial = copy_archive(archive, tmp_path)

    def change(raw: dict[str, Any]) -> None:
        loader_metadata(raw)["accepted_requests"][1] = stamp

    rewrite_result(trial, change)
    with pytest.raises(ValueError, match="request stamp"):
        inspect_trial(prepared.plan, scenario.cell, trial)


def test_zero_cannot_be_attached_to_an_incomplete_cut(
    prepared: Prepared, tmp_path: Path
) -> None:
    archive = prepared.archives["before-dispatch"]
    trial = copy_archive(archive, tmp_path)
    TrialPaths(trial).reward_json_path.write_bytes(record_bytes({"reward": 0}))
    with pytest.raises(ValueError):
        inspect_trial(prepared.plan, archive.scenario.cell, trial)


def test_wrong_map_cut_and_wrong_cell_cannot_reuse_a_reward(
    prepared: Prepared, tmp_path: Path
) -> None:
    archive = prepared.archives["followed-dispatch"]
    other = prepared.archives["before-dispatch"]
    trial = copy_archive(archive, tmp_path)
    write_verifier(trial, other.replay.cut)
    with pytest.raises(ValueError):
        inspect_trial(prepared.plan, archive.scenario.cell, trial)
    with pytest.raises(ValueError):
        inspect_trial(prepared.plan, other.scenario.cell, archive.trial)


def test_cohort_summary_keeps_unobserved_cases_and_bounds_their_effect(
    prepared: Prepared,
) -> None:
    from examples.seatlab.physical_dispatch.cohort_scores import score_cohort

    result = score_cohort(prepared.study, prepared.plan.parent, prepared.pin)
    assert result["sampling"] == "development_only"
    assert (
        result["complete_cells"] == 2
        and result["planned_cells"] == 16
        and result["complete_cases"] == 0
    )
    assert result["status_counts"] == {
        "complete": 2,
        "missing_artifact": 12,
        "incomplete": 2,
        "inconsistent_or_unreadable": 0,
    }
    assert (
        result["mean_contrast_bounds"] == ["-3/4", "1"]
        and result["mean_contrast"] is None
    )
    assert result["complete_case_mean_contrast"] is None
    assert result["duplicated_trial_ids"] == 0
    assert result["cells_with_reused_trial_ids"] == 0
    assert result["recorded_order_counts"] == {
        "not_comparable": 3,
        "partial_consistent": 0,
        "complete_consistent": 0,
        "conflict": 1,
    }
