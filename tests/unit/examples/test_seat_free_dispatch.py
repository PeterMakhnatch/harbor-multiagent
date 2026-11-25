import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from examples.native_study import record_bytes
from examples.seatlab.exhibit_owner import _arguments
from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp
from examples.seatlab.physical_dispatch import certificate, stage
from examples.seatlab.physical_dispatch.team_audit import inspect_team
from examples.seatlab.physical_dispatch.team_plan import prepare_team
from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TrialLock
from harbor.models.task.task import Task
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import AgentInfo, SeatResult, TimingInfo
from tests.unit.examples.test_exhibit_relay_capture import (
    END,
    START,
    write_native_archive,
)
from tests.unit.examples.test_seat_dispatch_archive import write_verifier
from tests.unit.examples.test_seat_dispatch_receiver import listener, no_connection

SEED = 7
MAP = "swapped"
OWNER = "examples.seatlab.exhibit_owner:RelayOwner"
ROLES = ("loader", "registrar", "depot")
ORDER = (*ROLES, "loader")
PHASES = ("request", "registrar", "depot", "dispatch")
ROLE_PHASES = {
    "loader": ("request", "dispatch"),
    "registrar": ("registrar",),
    "depot": ("depot",),
}
ROLE_TURNS = {
    "loader": ("loader_request", "loader_dispatch"),
    "registrar": ("registrar",),
    "depot": ("depot",),
}
ENDPOINTS = ("loader_request", "loader_dispatch", "registrar", "depot")
OPEN = "compatible_not_entailed"

Metadata = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Scenario:
    prefix: str
    turns: int
    dispatch: str | None
    reward: int | None
    chains: int | None
    functions: tuple[int, str | None] | None
    bijections: tuple[int, str | None] | None


SCENARIOS = {
    "unsupported-success": Scenario("empty", 4, "truth", 1, 0, (64, OPEN), (56, OPEN)),
    "documented-failure": Scenario(
        "chains", 4, "crossed", 0, 2, (1, "contradicted"), (1, "contradicted")
    ),
    "documented-success": Scenario(
        "chains", 4, "truth", 1, 2, (1, "entailed"), (1, "entailed")
    ),
    "post-depot": Scenario("chains", 3, None, None, 2, (1, None), (1, None)),
    "pre-depot": Scenario("chains", 2, None, None, None, None, None),
}


@dataclass(frozen=True)
class Archive:
    scenario: Scenario
    trial: Path
    cut: dict[str, Any]
    metadata: Metadata
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class Prepared:
    task: Path
    plan: Path
    receipt: dict[str, Any]
    supplied: dict[str, Path]
    instructions: dict[str, str]
    archives: dict[str, Archive]


def current(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any]:
    (card,) = [c for c in cards if c["key"] == key and c["start"] <= epoch <= c["end"]]
    return card


def free_action(
    scenario: Scenario, phase: str, view: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    order = next(b for b in case["briefs"] if b["id"] == case["realized"])
    epoch = order["epoch"]
    crates = {i: current(case["registrar"], i, epoch)["value"] for i in order["items"]}
    bays = {i: current(case["depot"], c, epoch)["value"] for i, c in crates.items()}
    if phase == "dispatch":
        left, right = sorted(bays)
        if scenario.dispatch == "truth":
            return {"routes": {right: bays[right], left: bays[left]}, "op": "commit"}
        assert scenario.dispatch == "crossed"
        return {"routes": {right: bays[left], left: bays[right]}, "op": "commit"}
    if scenario.prefix == "empty":
        listing = "slips" if phase == "request" else "ids"
        return {listing: [], "op": "request" if phase == "request" else "file"}
    assert scenario.prefix == "chains"
    if phase == "request":
        other = max(b["id"] for b in view["briefs"] if b["id"] != order["id"])
        return {"slips": sorted((order["id"], other), reverse=True), "op": "request"}
    keys = list(order["items"] if phase == "registrar" else crates.values())
    extra = next(card["id"] for card in view["cards"] if card["key"] not in keys)
    ids = [current(view["cards"], key, epoch)["id"] for key in keys] + [extra]
    return {"ids": sorted(ids, reverse=True), "op": "file"}


def replay(
    case: dict[str, Any], scenario: Scenario, instructions: dict[str, str]
) -> tuple[dict[str, Any], Metadata, list[dict[str, Any]]]:
    session = certificate.Session(deepcopy(case), "linked")
    metadata: Metadata = {
        role: {
            "role": role,
            "accepted_actions": [],
            "accepted_requests": [],
            "phase": ROLE_PHASES[role][0],
        }
        for role in ROLES
    }
    rows = []
    turns = zip(ORDER[: scenario.turns], PHASES[: scenario.turns], strict=True)
    for seq, (role, phase) in enumerate(turns, start=1):
        owner = metadata[role]
        actions = owner["accepted_actions"]
        view = certificate.cli_view(session.view(role))
        assert (view["phase"], view["turn"]) == (phase, role)
        previous = deepcopy(actions[-1]) if actions else None
        stamp = request_stamp(
            encode_request(render_turn(instructions[role], view, previous))
        )
        action = free_action(scenario, phase, view, case)
        _arguments(action, view, phase)
        session.apply(role, deepcopy(action))
        accepted = session.snapshot()["events"][-1]["action"]
        assert json.dumps(accepted) != json.dumps(action)
        actions.append(action)
        owner["accepted_requests"].append(stamp)
        done = len(actions)
        phases = ROLE_PHASES[role]
        owner["phase"] = phases[done] if done < len(phases) else "finished"
        rows.append({"seq": seq, "role": role, "phase": phase, "request": stamp})
    return session.snapshot(), metadata, rows


def materialize(
    plan: Path,
    archives: Path,
    name: str,
    case: dict[str, Any],
    instructions: dict[str, str],
    owners: dict[str, AgentInfo],
) -> Archive:
    scenario = SCENARIOS[name]
    config = TrialConfig.model_validate_json((plan / "config.json").read_bytes())
    lock = TrialLock.model_validate_json((plan / "lock.json").read_bytes())
    cut, metadata, rows = replay(case, scenario, instructions)
    paths = TrialPaths(config.trials_dir / config.trial_name)
    observation = write_verifier(paths.trial_dir, cut)
    status = "complete" if scenario.turns == len(ORDER) else "incomplete"
    assert (observation["status"], observation["reward"]) == (status, scenario.reward)
    seats = [
        SeatResult(
            seat=role,
            under_test=True,
            agent_info=owners[role],
            agent_result=AgentContext(metadata=deepcopy(metadata[role])),
            agent_execution=TimingInfo(started_at=START, finished_at=END),
            execution_disposition="returned"
            if metadata[role]["phase"] == "finished"
            else "cancelled",
        )
        for role in ROLES
    ]
    write_native_archive(config, lock, seats, observation, paths, START, END)
    trial = archives / name / config.trial_name
    trial.parent.mkdir(parents=True)
    shutil.move(paths.trial_dir, trial)
    paths.trial_dir.parent.rmdir()
    return Archive(scenario, trial, cut, metadata, rows)


def owner_identities(config: TrialConfig, logs: Path) -> dict[str, AgentInfo]:
    identities = {}
    for member in config.participants:
        module, attribute = (member.import_path or "").split(":")
        owner = getattr(import_module(module), attribute)(logs, **member.kwargs)
        identities[member.seat] = owner.to_agent_info()
    return identities


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Prepared]:
    with ExitStack() as stack:
        patch = stack.enter_context(pytest.MonkeyPatch.context())
        patch.setattr(sys, "dont_write_bytecode", True)
        base = tmp_path_factory.mktemp("seat-free-dispatch").resolve()
        task = base / "task"
        stage.materialize(task)
        owners = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="ft-", dir="/tmp"))
        ).resolve()
        assert stat.S_IMODE(owners.stat().st_mode) == 0o700
        supplied = {turn: owners / f"o{i}" for i, turn in enumerate(ENDPOINTS)}
        plan = base / "free-team-inputs"
        receipt = prepare_team(task, plan, SEED, MAP, supplied)
        assert os.listdir(owners) == []
        config = TrialConfig.model_validate_json((plan / "config.json").read_bytes())
        logs = base / "owner-logs"
        logs.mkdir()
        with ExitStack() as live:
            sockets = [listener(live, path) for path in supplied.values()]
            identities = owner_identities(config, logs)
            for sock in sockets:
                no_connection(sock)
        for path in supplied.values():
            path.unlink()
        native_task = Task(task)
        instructions = {role: native_task.seat_instruction(role) for role in ROLES}
        _, cases = certificate.native_pair(SEED)
        archives = {
            name: materialize(
                plan, base / "archives", name, cases[MAP], instructions, identities
            )
            for name in SCENARIOS
        }
        yield Prepared(task, plan, receipt, supplied, instructions, archives)


def expect(plan: Path) -> str:
    return "sha256:" + hashlib.sha256((plan / "receipt.json").read_bytes()).hexdigest()


def relocate(archive: Archive, tmp_path: Path) -> Path:
    trial = tmp_path.resolve() / "relocated" / archive.trial.name
    shutil.copytree(archive.trial, trial)
    return trial


def test_prepare_binds_four_owner_turns_for_a_nonfocal_draw(
    prepared: Prepared,
) -> None:
    base, cases = certificate.native_pair(SEED)
    assert base["realized"] != min(brief["id"] for brief in base["briefs"])
    plan, receipt = prepared.plan, prepared.receipt
    assert sorted(entry.name for entry in plan.iterdir()) == [
        "config.json",
        "lock.json",
        "receipt.json",
    ]
    assert all(stat.S_IMODE(entry.stat().st_mode) == 0o600 for entry in plan.iterdir())
    assert receipt == json.loads((plan / "receipt.json").read_bytes())
    assert (receipt["seed"], receipt["map"], receipt["executions"]) == (SEED, MAP, 0)
    assert receipt["task_name"] == "harbor/exhibit-relay-physical"
    assert receipt["native_lock_covers_roles"] is True
    assert receipt["role_instruction_digests"]
    commitment = certificate.case_commitment(cases[MAP])
    assert receipt["case_commitment"] == commitment
    assert commitment != certificate.case_commitment(cases["original"])
    config = TrialConfig.model_validate_json((plan / "config.json").read_bytes())
    lock = TrialLock.model_validate_json((plan / "lock.json").read_bytes())
    assert Path(str(config.task.path)) == prepared.task
    assert config.trials_dir == plan / "trials"
    assert config.environment.env == {
        "EXHIBIT_SEED": str(SEED),
        "EXHIBIT_PROFILE": "fork",
        "EXHIBIT_MODE": "linked",
        "EXHIBIT_LAYOUT": "draw",
        "EXHIBIT_BAY_MAP": MAP,
    }
    assert config.verifier.env == {"EXHIBIT_RETAIN_CUT": "1"}
    bound = {
        member.seat: (member.import_path, member.name, member.model_name, member.kwargs)
        for member in config.participants
    }
    assert list(bound) == list(ROLES)
    assert bound == {
        role: (
            OWNER,
            None,
            None,
            {
                "role": role,
                "owner_sockets": [str(prepared.supplied[t]) for t in ROLE_TURNS[role]],
            },
        )
        for role in ROLES
    }
    sockets = [path for m in config.participants for path in m.kwargs["owner_sockets"]]
    assert len(set(sockets)) == len(ENDPOINTS)
    assert lock.participants == config.participants
    assert lock.environment.env == config.environment.env
    assert lock.verifier.env == config.verifier.env
    assert all(not os.path.lexists(path) for path in prepared.supplied.values())


def test_prepare_refuses_live_or_aliased_owners_without_contact(
    prepared: Prepared, tmp_path: Path
) -> None:
    with (
        tempfile.TemporaryDirectory(prefix="ft-", dir="/tmp") as raw,
        ExitStack() as stack,
    ):
        owners = Path(raw).resolve()
        supplied = {turn: owners / f"o{i}" for i, turn in enumerate(ENDPOINTS)}
        sock = listener(stack, supplied["loader_dispatch"])
        out = tmp_path.resolve() / "refused"
        with pytest.raises(ValueError):
            prepare_team(prepared.task, out, SEED, MAP, supplied)
        aliased = {**supplied, "loader_dispatch": supplied["registrar"]}
        with pytest.raises(ValueError):
            prepare_team(prepared.task, out, SEED, MAP, aliased)
        no_connection(sock)
        assert not out.exists()
        assert os.listdir(owners) == ["o1"]


@pytest.mark.parametrize("name", tuple(SCENARIOS))
def test_free_team_archive_reports_native_outcome_and_route_support(
    prepared: Prepared, name: str
) -> None:
    archive = prepared.archives[name]
    scenario = archive.scenario
    report = inspect_team(prepared.plan, archive.trial, expect=expect(prepared.plan))
    assert report["provider_authenticated"] is False
    complete = scenario.turns == len(ORDER)
    assert report["status"] == ("complete" if complete else "incomplete")
    assert report["native_reward"] == scenario.reward
    if complete:
        assert type(report["native_reward"]) is int
    assert report["accepted_turns"] == archive.rows
    route = report["route_evidence"]
    if scenario.chains is None:
        assert route is None
        return
    assert route["delivered_chain_items"] == scenario.chains
    assert route["legal_actions"] == 64
    assert scenario.functions is not None and scenario.bijections is not None
    for model, (count, choice) in (
        ("card_functions", scenario.functions),
        ("generator_bijections", scenario.bijections),
    ):
        assert route[model] == {"compatible_routes": count, "choice": choice}


def test_final_request_rendered_from_normalized_history_is_rejected(
    prepared: Prepared, tmp_path: Path
) -> None:
    archive = prepared.archives["documented-success"]
    session = certificate.Session(deepcopy(archive.cut["case"]), "linked")
    for event in archive.cut["events"][:3]:
        session.apply(event["role"], event["action"])
    view = certificate.cli_view(session.view("loader"))
    loader = archive.metadata["loader"]
    instruction = prepared.instructions["loader"]
    raw = loader["accepted_actions"][0]
    normalized = archive.cut["events"][0]["action"]
    assert raw == {**normalized, "slips": normalized["slips"][::-1]}
    recorded = loader["accepted_requests"][1]
    assert recorded == request_stamp(
        encode_request(render_turn(instruction, view, raw))
    )
    stamp = request_stamp(encode_request(render_turn(instruction, view, normalized)))
    assert stamp != recorded
    trial = relocate(archive, tmp_path)
    result = TrialPaths(trial).result_path
    document = json.loads(result.read_bytes())
    owner = next(s for s in document["seat_results"] if s["seat"] == "loader")
    owner["agent_result"]["metadata"]["accepted_requests"][1] = stamp
    result.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        inspect_team(prepared.plan, trial, expect=expect(prepared.plan))


def test_wrong_receipt_digest_is_rejected(prepared: Prepared) -> None:
    trial = prepared.archives["unsupported-success"].trial
    lock_digest = hashlib.sha256((prepared.plan / "lock.json").read_bytes())
    for wrong in ("sha256:" + "0" * 64, "sha256:" + lock_digest.hexdigest()):
        with pytest.raises(ValueError):
            inspect_team(prepared.plan, trial, expect=wrong)


@pytest.mark.parametrize("name", ("pre-depot", "post-depot"))
def test_incomplete_native_sample_cannot_gain_a_grade(
    prepared: Prepared, tmp_path: Path, name: str
) -> None:
    trial = relocate(prepared.archives[name], tmp_path)
    TrialPaths(trial).reward_json_path.write_bytes(record_bytes({"reward": 0}))
    with pytest.raises(ValueError):
        inspect_team(prepared.plan, trial, expect=expect(prepared.plan))
