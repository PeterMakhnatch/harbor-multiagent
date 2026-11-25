import json
import sys
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from examples.native_study import record_bytes
from examples.seatlab.exhibit_bind import bind
from examples.seatlab.exhibit_capture import capture
from examples.seatlab.exhibit_enroll import TASK, TASK_MODULE, enroll
from examples.seatlab.exhibit_plan import load_plan
from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TrialLock
from harbor.models.task.task import Task
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import AgentInfo, SeatResult, TimingInfo
from harbor.models.verifier.result import VerifierResult
from harbor.tasks.client import TaskDownloadResult
from harbor.trial.seats import SeatsTrial

TICKET = "pair-0001"
ARM = "bundled"
ROLES = ("loader", "registrar", "depot", "loader")
START = datetime(2000, 1, 1, tzinfo=UTC)
END = START + timedelta(seconds=1)


@pytest.fixture
def prepared(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    plan = tmp_path.resolve() / "development-plan"
    enrolled = enroll(plan, 4, development_seed=1729)
    assert enrolled["status"] == "development_only"
    assert enrolled["enrollable"] is False
    assert enrolled["executions"] == 0
    socket_dir = tmp_path_factory.mktemp("s")
    socket_dir.chmod(0o700)
    endpoint = socket_dir / "s"
    bound = tmp_path.resolve() / "synthetic-input"
    receipt = bind(plan, TICKET, ARM, endpoint, bound)
    assert receipt["executions"] == 0
    assert receipt["provider_authenticated"] is False
    assert not endpoint.exists()
    config = TrialConfig.model_validate_json((bound / "config.json").read_bytes())
    lock = TrialLock.model_validate_json((bound / "lock.json").read_bytes())
    assert config.verifier.env == {"EXHIBIT_RETAIN_CUT": "1"}
    _, schedule, _ = load_plan(plan)
    case = next(row for row in schedule["cases"] if row["id"] == TICKET)
    return plan, config, lock, case


def simulate(
    case, *, turns, wrong_world=False, empty_registrar=False, empty_depot=False
):
    make_case = import_module(f"{TASK_MODULE}.cases").make_case
    Session = import_module(f"{TASK_MODULE}.engine").Session
    observe = import_module(f"{TASK_MODULE}.diagnostics").observation
    act = import_module("examples.tasks.exhibit-relay.solution.policies").act
    layout = case["layout"]
    if wrong_world:
        layout = "b" if layout == "a" else "a"
    session = Session(make_case(case["seed"], "coupling", layout=layout), ARM)
    depot_actions = []
    requests = []
    for role in ROLES[:turns]:
        view = session.view(role)
        action = act(view, "portfolio")
        if role == "registrar" and empty_registrar:
            action = {"op": "file", "ids": []}
        if role == "depot" and empty_depot:
            action = {"op": "file", "ids": []}
        session.apply(role, action)
        if role == "depot":
            from examples.seatlab.exhibit_wire import (
                encode_request,
                render_turn,
                request_stamp,
            )

            instruction = (TASK / "instructions/depot.md").read_text()
            view_sent = json.loads(json.dumps(view, sort_keys=True))
            requests.append(
                request_stamp(encode_request(render_turn(instruction, view_sent, None)))
            )
            depot_actions.append(action)
    cut = session.snapshot()
    return cut, observe(cut), depot_actions, requests


def synthetic_seats(depot_actions, requests):
    timing = TimingInfo(started_at=START, finished_at=END)
    return [
        SeatResult(
            seat=role,
            under_test=True,
            agent_info=AgentInfo(
                name="seatlab-exhibit-relay-owner" if role == "depot" else "oracle",
                version="1.0.0",
                model_info=None,
            ),
            agent_result=AgentContext(
                metadata={
                    "role": role,
                    "accepted_actions": depot_actions,
                    "accepted_requests": requests,
                    "phase": "finished" if depot_actions else "depot",
                }
            )
            if role == "depot"
            else AgentContext(),
            agent_execution=timing,
            execution_disposition="returned"
            if depot_actions or role != "depot"
            else "cancelled",
        )
        for role in ROLES[:3]
    ]


def write_native_archive(
    config: TrialConfig,
    lock: TrialLock,
    seats: list[SeatResult],
    observation: dict[str, Any],
    paths: TrialPaths,
    started: datetime,
    finished: datetime,
) -> None:
    assert config.task.path is not None
    assert not config.task.is_git_task()
    assert config.source_trial is None
    assert started <= finished
    primary = next(seat for seat in seats if seat.under_test)
    assert primary.agent_info is not None

    trial: Any = object.__new__(SeatsTrial)
    trial.config = config
    trial.task = Task(config.task.path)
    trial.paths = paths
    trial.seats = [SimpleNamespace(result=seat) for seat in seats]
    trial.agent = SimpleNamespace(to_agent_info=lambda: primary.agent_info)
    trial._id = UUID(int=0)
    trial._now = lambda: started
    trial._task_download_result = TaskDownloadResult(
        path=trial.task.task_dir, download_time_sec=0.0, cached=False
    )

    trial._init_result()
    written_lock = trial._write_trial_lock()
    assert written_lock.model_dump(mode="json") == lock.model_dump(mode="json")
    assert TrialLock.model_validate_json(paths.lock_path.read_bytes()).model_dump(
        mode="json"
    ) == lock.model_dump(mode="json")

    reward = (
        {"reward": observation["reward"]}
        if observation["status"] == "complete"
        else None
    )
    result = trial.result
    result.finished_at = finished
    result.verifier = TimingInfo(started_at=started, finished_at=finished)
    result.verifier_result = VerifierResult(rewards=reward)
    paths.result_path.write_text(result.model_dump_json(indent=4))

    assert not hasattr(trial, "agent_environment")
    assert not hasattr(trial.agent, "run")
    assert result.environment_setup is None
    assert result.agent_execution is None


def materialize(
    prepared,
    *,
    turns=4,
    wrong_world=False,
    empty_registrar=False,
    retain=True,
    unmatched_request=False,
    empty_depot=False,
):
    plan, config, lock, case = prepared
    cut, observation, depot_actions, requests = simulate(
        case,
        turns=turns,
        wrong_world=wrong_world,
        empty_registrar=empty_registrar,
        empty_depot=empty_depot,
    )
    trial = config.trials_dir / config.trial_name
    paths = TrialPaths(trial)
    paths.verifier_dir.mkdir(parents=True)
    paths.verifier_dir.joinpath("observation.json").write_bytes(
        record_bytes(observation)
    )
    if retain:
        cut_path = paths.verifier_dir / "private-exhibit-cut.json"
        cut_path.write_bytes(record_bytes(cut))
        cut_path.chmod(0o600)
    reward = None
    if observation["status"] == "complete":
        reward = {"reward": observation["reward"]}
        paths.reward_json_path.write_bytes(record_bytes(reward))
    if unmatched_request:
        requests = [{"bytes": 17, "sha256": "0" * 64}]
    seats = synthetic_seats(depot_actions, requests)
    write_native_archive(config, lock, seats, observation, paths, START, END)
    leaf = plan / "outcomes" / TICKET / ARM
    return plan, trial, leaf, observation


def test_capture_admits_reference_once(prepared):
    plan, trial, leaf, observation = materialize(prepared)
    assert observation["status"] == "complete"
    report = capture(plan, TICKET, ARM, trial)
    assert report["status"] == "admitted"
    assert report["provider_authenticated"] is False
    assert report["operator_authenticated"] is False
    assert report["enrollment_status"] == "development_only"
    assert {p.name for p in leaf.iterdir()} == {
        "receipt.json",
        "observation.json",
        "reward.json",
    }
    assert json.loads((leaf / "observation.json").read_bytes()) == observation
    before = {p.name: p.read_bytes() for p in leaf.iterdir()}
    assert capture(plan, TICKET, ARM, trial)["status"] != "admitted"
    assert {p.name: p.read_bytes() for p in leaf.iterdir()} == before
    assert (trial / "verifier" / "private-exhibit-cut.json").is_file()


@pytest.mark.parametrize(
    "options",
    [
        {"turns": 2},
        {"wrong_world": True},
        {"retain": False},
        {"empty_registrar": True},
        {"unmatched_request": True},
    ],
    ids=[
        "before-depot",
        "different-world",
        "missing-cut",
        "noncanonical-prefix",
        "not-the-declared-depot-packet",
    ],
)
def test_capture_rejects_unqualified_development_fixture(prepared, options):
    plan, trial, leaf, _ = materialize(prepared, **options)
    report = capture(plan, TICKET, ARM, trial)
    assert report["status"] != "admitted"
    assert report["provider_authenticated"] is False
    assert report["operator_authenticated"] is False
    assert not leaf.exists()


def test_closed_filing_report_measures_coverage_not_scripted_reward(prepared):
    from examples.seatlab.exhibit_filing import report

    plan, trial, leaf, observation = materialize(prepared, turns=3)
    assert observation["reward"] is None
    assert capture(plan, TICKET, ARM, trial)["fixed_prefix_verified"] is True
    summary = report(plan)
    assert (
        not summary["physical_reward_reported"]
        and summary["empirical_causal_verdict"] is None
    )
    assert summary["pair_count"] == 4
    assert summary["filing_counts"][ARM]["nonempty_filing"] == 1
    assert summary["metrics"]["C"]["arms"][ARM]["observed_mean"] == "2/5"
    assert summary["metrics"]["C"]["difference_bounds"] == ["-3/10", "2/5"]
    assert summary["metrics"]["P"]["difference_bounds"] == ["-3/8", "1/2"]
    assert summary["bootstrap"]["status"] == "not_computed"
    (leaf / "receipt.json").unlink()
    unknown = report(plan)
    assert unknown["filing_counts"][ARM]["invalid_or_missing_evidence"] == 1
    assert unknown["metrics"]["C"]["difference_bounds"] == ["-2/5", "2/5"]


def test_percentiles_resample_pairs_within_each_layout():
    from fractions import Fraction

    from examples.seatlab.exhibit_filing import _bootstrap
    from examples.seatlab.exhibit_pairs import Outcome

    low = Outcome("nonempty_filing", C=Fraction(1, 10))
    high = Outcome("nonempty_filing", C=Fraction(2, 5))
    balanced = {"a": [(low, high)] * 50, "b": [(high, high)] * 50}
    result = _bootstrap(balanced)
    assert result["status"] == "computed" and result["percentile_interval_95"] == [
        "3/20",
        "3/20",
    ]
    balanced["b"][0] = (Outcome("missing"), high)
    assert _bootstrap(balanced)["status"] == "not_computed"


def test_accepted_empty_filing_has_zero_coverage_not_a_missing_observation(prepared):
    from examples.seatlab.exhibit_filing import report

    plan, trial, _, observation = materialize(prepared, turns=3, empty_depot=True)
    assert observation["handoff"]["depot_filed"] == 0 and observation["reward"] is None
    assert capture(plan, TICKET, ARM, trial)["status"] == "admitted"
    result = report(plan)
    assert result["filing_counts"][ARM]["empty_filing"] == 1
    assert result["filing_counts"][ARM]["no_outcome_leaf"] == 3
    assert result["metrics"]["C"]["arms"][ARM]["observed_mean"] == "0"
    assert result["metrics"]["C"]["difference_bounds"] == ["-2/5", "3/10"]
