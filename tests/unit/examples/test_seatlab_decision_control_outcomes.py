import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_receipt import control_client as client
from examples.seatlab.decision_receipt import control_inputs as inputs
from examples.seatlab.decision_receipt import control_outcomes as outcomes
from examples.seatlab.decision_receipt import control_seat as control
from examples.seatlab.decision_receipt import decision_verify as verify
from examples.seatlab.decision_receipt.materialize import TASK_NAME
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import Participant, TrialConfig

Json = dict[str, Any]
Edit = Callable[[dict[str, Any]], None]
TASK_PATH = "/staged/private-workspace/ship-decision-receipt"
TASK = {"name": TASK_NAME, "digest": "sha256:" + "a" * 64, "checksum": "b" * 64}
TRIAL = "5b0c3a8e-6f43-4d6e-9a43-2f6d8f3c1a11"
RUN = "0e6f2d4a-9c1b-4c55-8f7e-3a2b1c0d9e8f"
CASES = [(fate, cite) for fate in ("settles", "fails") for cite in (False, True)]
EVENTS = (
    ("start", "2026-11-25T10:00:01Z"),
    ("environment-start", "2026-11-25T10:00:02Z"),
    ("agent-start", "2026-11-25T10:00:03Z"),
    ("agent-end", "2026-11-25T10:02:00Z"),
    ("verification-start", "2026-11-25T10:02:30.250000Z"),
    ("end", "2026-11-25T10:03:01Z"),
)
TICKET_FILES = {
    "config": ("config.json",),
    "result": ("result.json",),
    "lock": ("lock.json",),
    "cut": outcomes.CUT,
    "observation": outcomes.OBSERVATION,
    "reward": outcomes.REWARD,
    "events": outcomes.EVENTS,
}


def ticket(fate: str, cite: bool) -> str:
    return f"dr-{fate}-{'cited' if cite else 'plain'}"


def participants(cite: bool) -> list[Json]:
    raw: list[Json] = [
        {
            "name": outcomes.ORACLE,
            "env": {"SH_POLICY": "original"},
            "seat": "outgoing",
        },
        {
            "import_path": outcomes.CONTROL_IMPORT,
            "kwargs": {"cite": cite},
            "seat": "relief",
        },
    ]
    return [
        Participant.model_validate(member).model_dump(mode="json", exclude_none=True)
        for member in raw
    ]


def request(trials: Path, fate: str, cite: bool) -> Json:
    return {
        "task": {"path": TASK_PATH},
        "trial_name": ticket(fate, cite),
        "trials_dir": str(trials),
        "environment": {
            "type": "docker",
            "delete": True,
            "env": {"SH_FATE": fate, "SH_CHANNEL": "deliver"},
        },
        "participants": participants(cite),
    }


def full_config(case: Json) -> Json:
    return json.loads(TrialConfig.model_validate(copy.deepcopy(case)).model_dump_json())


def written_config(case: Json) -> Json:
    typed = TrialConfig.model_validate(copy.deepcopy(case))
    return json.loads(typed.model_dump_json(indent=4, exclude={"agent"}))


def cut(fate: str, cite: bool) -> Json:
    return {
        "schema": verify.CUT_SCHEMA,
        "run": RUN,
        "fate": fate,
        "channel": "deliver",
        "events": [
            {"seq": 1, "kind": "handoff", "choice": "original"},
            {"seq": 2, "kind": "inbox", "choice": "original"},
            {"seq": 3, "kind": "resolve", "choice": "close"},
        ],
        "replace": None,
        "settled": [],
        "drained": ["original"] if fate == "settles" else [],
        verify.CITATION: "original" if cite else None,
    }


def control_receipt(cite: bool) -> Json:
    record = {
        "schema": control.RECORD_SCHEMA,
        "seat": control.SEAT,
        "cite": cite,
        "client_sha256": control.CLIENT_SHA256,
        "outcome": client.SEALED,
    }
    context = AgentContext(metadata={control.METADATA_KEY: record})
    return context.model_dump(mode="json")


def seat(role: str, runner: str, context: Json | None = None) -> Json:
    return {
        "seat": role,
        "under_test": True,
        "agent_info": {"name": runner, "version": "1.0.0", "model_info": None},
        "agent_result": context,
        "exception_info": None,
        "execution_disposition": "returned",
    }


def lock(case: Json) -> Json:
    config = written_config(case)
    return {
        "schema_version": 2,
        "task": {
            "name": os.path.basename(TASK_PATH),
            "type": "local",
            "digest": TASK["digest"],
            "path": TASK_PATH,
            "version": None,
        },
        "install_only": False,
        "timeout_multiplier": 1.0,
        "verifier": {"disable": False},
        "environment": copy.deepcopy(config["environment"]),
        "participants": copy.deepcopy(config["participants"]),
        "source_trial": None,
    }


def pin(case: Json) -> str:
    return inputs.lock_digest(lock(case))


def journal(events: list[Json]) -> str:
    lines = (json.dumps(event, separators=(",", ":")) + "\n" for event in events)
    return "".join(lines)


def records(case: Json, fate: str, cite: bool, frozen: Json) -> dict[str, Any]:
    observation = verify.evaluate(copy.deepcopy(frozen))
    reward = observation["reward"]
    config = written_config(case)
    return {
        "config": config,
        "result": {
            "schema_version": 1,
            "id": TRIAL,
            "task_name": TASK_NAME,
            "trial_name": ticket(fate, cite),
            "trial_uri": (Path(case["trials_dir"]) / case["trial_name"]).as_uri(),
            "task_id": {"path": TASK_PATH},
            "task_checksum": TASK["checksum"],
            "config": full_config(case),
            "agent_info": {"name": outcomes.ORACLE, "version": "1", "model_info": None},
            "verifier_result": {"rewards": {"reward": reward}},
            "exception_info": None,
            "started_at": "2026-11-25T10:00:00Z",
            "finished_at": "2026-11-25T10:03:00Z",
            "seat_results": [
                seat("outgoing", outcomes.ORACLE),
                seat("relief", outcomes.CONTROL, control_receipt(cite)),
            ],
            "reward_valid": True,
        },
        "lock": lock(case),
        "cut": frozen,
        "observation": observation,
        "reward": {"reward": reward},
        "events": [{"event": name, "timestamp": at} for name, at in EVENTS],
    }


def stage(
    tmp_path: Path,
    fate: str = "settles",
    cite: bool = True,
    edit: Edit | None = None,
    frozen: Json | None = None,
    skip: tuple[str, ...] = (),
) -> tuple[Path, Json]:
    trials = tmp_path / "trials"
    case = request(trials, fate, cite)
    files = records(case, fate, cite, cut(fate, cite) if frozen is None else frozen)
    if edit is not None:
        edit(files)
    folder = trials / case["trial_name"]
    (folder / "verifier").mkdir(parents=True)
    for name, parts in TICKET_FILES.items():
        if name not in skip:
            text = journal(files[name]) if name == "events" else json.dumps(files[name])
            folder.joinpath(*parts).write_text(text, "utf-8")
    return folder, case


def inspect(
    folder: Path,
    case: Json,
    fate: str = "settles",
    cite: bool = True,
    *,
    lock_sha256: Any = None,
    client_sha256: Any = control.CLIENT_SHA256,
):
    return outcomes.inspect_case(
        folder,
        case,
        dict(TASK),
        fate=fate,
        cite=cite,
        lock_sha256=pin(case) if lock_sha256 is None else lock_sha256,
        client_sha256=client_sha256,
    )


def ungraded(result: outcomes.CaseResult, status: str) -> None:
    assert result.status == status and result.reason
    assert result.world is None and result.grade is None and result.citation is None
    assert result.action is None and result.outcome is None
    assert TASK_PATH not in result.reason and "/" not in result.reason


@pytest.mark.parametrize(("fate", "cite"), CASES)
def test_four_controls_verify_with_fate_grade_and_requested_citation(
    tmp_path: Path, fate: str, cite: bool
) -> None:
    folder, case = stage(tmp_path, fate, cite)
    result = inspect(folder, case, fate, cite)
    assert result == outcomes.CaseResult(
        status="verified",
        trial=TRIAL,
        world=RUN,
        citation="original" if cite else None,
        citation_status="cited_at_decision" if cite else "returned_not_cited",
        grade=1 if fate == "settles" else 0,
        action="close",
        outcome="exactly_one" if fate == "settles" else "omission",
        reason=None,
    )


def test_pairs_share_grade_and_outcome_within_each_fate() -> None:
    for fate in ("settles", "fails"):
        plain, cited = (outcomes.expected(fate, cite) for cite in (False, True))
        assert (plain["reward"], plain["outcome"]) == (
            cited["reward"],
            cited["outcome"],
        )
        assert plain[verify.CITATION] is None and cited[verify.CITATION] == "original"
    assert outcomes.expected("settles", True)["reward"] == 1
    assert outcomes.expected("fails", True)["reward"] == 0


def test_inputs_are_not_mutated(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    task = dict(TASK)
    before = copy.deepcopy((case, task))
    outcomes.inspect_case(
        folder,
        case,
        task,
        fate="settles",
        cite=True,
        lock_sha256=pin(case),
        client_sha256=control.CLIENT_SHA256,
    )
    assert (case, task) == before


def test_absent_and_empty_folders_are_missing(tmp_path: Path) -> None:
    case = request(tmp_path / "trials", "fails", False)
    folder = tmp_path / "trials" / case["trial_name"]
    ungraded(inspect(folder, case, "fails", False), "missing")
    folder.mkdir(parents=True)
    ungraded(inspect(folder, case, "fails", False), "missing")


def test_started_trial_without_result_is_incomplete(tmp_path: Path) -> None:
    folder, case = stage(
        tmp_path, skip=("result", "lock", "cut", "observation", "reward")
    )
    result = inspect(folder, case)
    ungraded(result, "incomplete")
    assert result.trial is None


def unfinished(files: dict[str, Any]) -> None:
    files["result"]["finished_at"] = None
    files["result"]["reward_valid"] = False
    files["result"]["verifier_result"] = None


def no_reward(files: dict[str, Any]) -> None:
    files["result"]["reward_valid"] = False
    files["result"]["verifier_result"] = None


def unended(files: dict[str, Any]) -> None:
    files["events"].pop()


@pytest.mark.parametrize(
    ("edit", "skip"),
    [
        (unfinished, ()),
        (no_reward, ()),
        (None, ("lock",)),
        (None, ("cut",)),
        (None, ("observation",)),
        (None, ("reward",)),
        (None, ("events",)),
        (unended, ()),
    ],
)
def test_partial_trials_are_incomplete_and_keep_the_trial(
    tmp_path: Path, edit: Edit | None, skip: tuple[str, ...]
) -> None:
    folder, case = stage(tmp_path, edit=edit, skip=skip)
    result = inspect(folder, case)
    ungraded(result, "incomplete")
    assert result.trial == TRIAL


def set_path(path: tuple[str, ...], value: Any) -> Edit:
    def edit(files: dict[str, Any]) -> None:
        target = files
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return edit


def swap_team(files: dict[str, Any]) -> None:
    files["config"]["participants"].reverse()


def tamper_observation(files: dict[str, Any]) -> None:
    files["observation"]["outcome"] = "duplicate"


def written_with_agent(files: dict[str, Any]) -> None:
    files["config"] = copy.deepcopy(files["result"]["config"])


def result_without_agent(files: dict[str, Any]) -> None:
    files["result"]["config"] = copy.deepcopy(files["config"])


def changed_default(files: dict[str, Any]) -> None:
    files["config"]["timeout_multiplier"] = 2.0


def other_world(files: dict[str, Any]) -> None:
    files["config"]["environment"]["env"]["SH_FATE"] = "fails"


@pytest.mark.parametrize(
    "edit",
    [
        swap_team,
        tamper_observation,
        other_world,
        written_with_agent,
        result_without_agent,
        changed_default,
        set_path(("reward", "reward"), 0),
        set_path(("result", "verifier_result", "rewards", "reward"), True),
        set_path(("result", "verifier_result", "rewards", "reward"), 0.5),
        set_path(("result", "task_checksum"), "c" * 64),
        set_path(("result", "task_name"), "harbor/ship-inflight"),
        set_path(("result", "id"), TRIAL.upper()),
        set_path(("result", "task_id"), {"path": TASK_PATH + "-copy"}),
        set_path(("config", "trials_dir"), "/elsewhere/trials"),
        set_path(("lock", "schema_version"), 1),
        set_path(("lock", "task", "digest"), "sha256:" + "d" * 64),
        set_path(("lock", "source_trial"), {"action": "regrade"}),
        set_path(("result", "reward_valid"), "true"),
    ],
)
def test_contradicting_files_are_conflicts_without_grade(
    tmp_path: Path, edit: Edit
) -> None:
    folder, case = stage(tmp_path, edit=edit)
    ungraded(inspect(folder, case), "conflict")


def test_replayed_world_of_another_fate_is_a_conflict(tmp_path: Path) -> None:
    folder, case = stage(tmp_path, "settles", True, frozen=cut("fails", True))
    ungraded(inspect(folder, case, "settles", True), "conflict")


def test_v1_cut_is_refused_not_inferred(tmp_path: Path) -> None:
    legacy = cut("settles", False)
    legacy.pop(verify.CITATION)
    legacy["schema"] = verify.inflight.CUT_SCHEMA

    def edit(files: dict[str, Any]) -> None:
        files["observation"] = verify.inflight.evaluate(copy.deepcopy(legacy))

    folder, case = stage(
        tmp_path, "settles", False, edit=edit, frozen=cut("settles", False)
    )
    folder.joinpath(*outcomes.CUT).write_text(json.dumps(legacy), "utf-8")
    ungraded(inspect(folder, case, "settles", False), "conflict")


def test_v1_artifacts_beside_the_decision_cut_are_conflicts(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    (folder / "verifier" / verify.inflight.CUT_NAME).write_text("{}", "utf-8")
    ungraded(inspect(folder, case), "conflict")


def test_v1_artifacts_alone_are_never_graded(tmp_path: Path) -> None:
    folder, case = stage(tmp_path, skip=("cut", "observation"))
    (folder / "verifier" / verify.inflight.CUT_NAME).write_text("{}", "utf-8")
    ungraded(inspect(folder, case), "conflict")


def test_duplicate_keys_and_links_are_conflicts(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    reward = folder.joinpath(*outcomes.REWARD)
    reward.write_text('{"reward": 1, "reward": 1}', "utf-8")
    ungraded(inspect(folder, case), "conflict")
    reward.unlink()
    target = tmp_path / "reward.json"
    target.write_text('{"reward": 1}', "utf-8")
    reward.symlink_to(target)
    ungraded(inspect(folder, case), "conflict")


def test_linked_trial_folder_is_a_conflict(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    moved = tmp_path / "moved"
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    ungraded(inspect(folder, case), "conflict")


def seat_edit(index: int, field: str, value: Any) -> Edit:
    def edit(files: dict[str, Any]) -> None:
        files["result"]["seat_results"][index][field] = value

    return edit


def model_seat(files: dict[str, Any]) -> None:
    files["result"]["seat_results"][1]["agent_info"]["model_info"] = {"name": "m"}


def trial_error(files: dict[str, Any]) -> None:
    files["result"]["exception_info"] = {"exception_type": "RuntimeError"}


def unfinished_error(files: dict[str, Any]) -> None:
    trial_error(files)
    files["result"]["finished_at"] = None


@pytest.mark.parametrize(
    "edit",
    [
        model_seat,
        trial_error,
        unfinished_error,
        seat_edit(1, "agent_info", {"name": outcomes.ORACLE, "model_info": None}),
        seat_edit(0, "under_test", False),
        seat_edit(1, "execution_disposition", None),
        seat_edit(1, "execution_disposition", "raised"),
        seat_edit(0, "exception_info", {"exception_type": "TimeoutError"}),
        seat_edit(1, "agent_result", None),
        seat_edit(1, "agent_result", AgentContext().model_dump(mode="json")),
        seat_edit(1, "agent_result", AgentContext(metadata={}).model_dump(mode="json")),
    ],
)
def test_unqualified_seats_are_separate_from_verified(
    tmp_path: Path, edit: Edit
) -> None:
    folder, case = stage(tmp_path, edit=edit)
    result = inspect(folder, case)
    ungraded(result, "seat_unqualified")
    assert result.trial == TRIAL


def test_missing_requested_citation_is_a_mismatch(tmp_path: Path) -> None:
    folder, case = stage(tmp_path, "fails", True, frozen=cut("fails", False))
    result = inspect(folder, case, "fails", True)
    assert result.status == "mismatch" and result.citation is None
    assert (result.grade, result.outcome, result.action) == (0, "omission", "close")
    assert result.world == RUN and result.trial == TRIAL
    assert result.reason == "observed cited_at_decision,citation_status differ"


def test_unrequested_citation_is_a_mismatch(tmp_path: Path) -> None:
    folder, case = stage(tmp_path, "settles", False, frozen=cut("settles", True))
    result = inspect(folder, case, "settles", False)
    assert result.status == "mismatch" and result.citation == "original"
    assert result.grade == 1


def test_wrong_business_action_is_a_mismatch(tmp_path: Path) -> None:
    frozen = cut("settles", True)
    frozen["events"][2]["choice"] = "fresh"
    frozen["settled"] = ["fresh"]
    frozen["drained"] = ["fresh", "original"]
    folder, case = stage(tmp_path, "settles", True, frozen=frozen)
    result = inspect(folder, case, "settles", True)
    assert result.status == "mismatch" and result.action == "fresh"
    assert (result.grade, result.outcome) == (0, "duplicate")


def test_decision_before_the_inbox_is_a_mismatch(tmp_path: Path) -> None:
    frozen = cut("settles", False)
    frozen["events"] = [
        {"seq": 1, "kind": "handoff", "choice": "original"},
        {"seq": 2, "kind": "resolve", "choice": "close"},
        {"seq": 3, "kind": "inbox", "choice": "original"},
    ]
    folder, case = stage(tmp_path, "settles", False, frozen=frozen)
    result = inspect(folder, case, "settles", False)
    assert result.status == "mismatch" and result.grade == 1
    assert "events" in (result.reason or "") and "citation_status" in (
        result.reason or ""
    )


def test_draft_handoff_is_a_mismatch(tmp_path: Path) -> None:
    frozen = cut("fails", False)
    frozen["events"][0]["choice"] = "draft"
    frozen["events"][1]["choice"] = "draft"
    folder, case = stage(tmp_path, "fails", False, frozen=frozen)
    result = inspect(folder, case, "fails", False)
    assert result.status == "mismatch" and "outgoing_handoff" in (result.reason or "")


def relief_kwargs(case: Json) -> None:
    case["participants"][1]["kwargs"] = {"cite": False}


def model_relief(case: Json) -> None:
    case["participants"][1]["model_name"] = "provider/model"


def withheld(case: Json) -> None:
    case["environment"]["env"]["SH_CHANNEL"] = "withhold"


def reordered(case: Json) -> None:
    case["participants"].reverse()


@pytest.mark.parametrize("edit", [relief_kwargs, model_relief, withheld, reordered])
def test_requests_outside_the_control_contract_are_usage_errors(
    tmp_path: Path, edit: Callable[[Json], None]
) -> None:
    folder, case = stage(tmp_path)
    edit(case)
    with pytest.raises(ValueError):
        inspect(folder, case)


def test_wrong_folder_fate_or_task_are_usage_errors(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    with pytest.raises(ValueError):
        inspect(folder.parent / "dr-settles-plain", case)
    with pytest.raises(ValueError):
        inspect(folder, case, fate="fails")
    with pytest.raises(ValueError):
        outcomes.inspect_case(
            folder,
            case,
            {**TASK, "name": "harbor/ship-inflight"},
            fate="settles",
            cite=True,
            lock_sha256=pin(case),
            client_sha256=control.CLIENT_SHA256,
        )


def test_receipt_contract_matches_the_scripted_seat() -> None:
    assert outcomes.RECEIPT_KEY == control.METADATA_KEY
    assert outcomes.RECEIPT_SCHEMA == control.RECORD_SCHEMA
    assert outcomes.CLIENT_PIN.fullmatch(control.CLIENT_SHA256) is not None
    assert outcomes.CONTROL == control.AGENT_NAME
    assert outcomes.ROLES[1] == control.SEAT
    for cite in (False, True):
        context = control_receipt(cite)
        held = outcomes.receipt(cite, control.CLIENT_SHA256)
        assert context["metadata"] == {control.METADATA_KEY: held}


def test_held_pins_must_be_well_formed(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    lock_pin = pin(case)
    bad_locks: list[Any] = [
        0,
        "",
        lock_pin.upper(),
        lock_pin[len("sha256:") :],
        lock_pin + "\n",
    ]
    for bad in bad_locks:
        with pytest.raises(ValueError, match="held lock pin is malformed"):
            inspect(folder, case, lock_sha256=bad)
    source = control.CLIENT_SHA256
    bad_clients: list[Any] = [
        None,
        "",
        source.upper(),
        "sha256:" + source,
        source[:-1],
        source + "\n",
        source.encode("ascii"),
    ]
    for bad in bad_clients:
        with pytest.raises(ValueError, match="held client pin is malformed"):
            inspect(folder, case, client_sha256=bad)
    unpinned: Any = outcomes.inspect_case
    with pytest.raises(TypeError):
        unpinned(
            folder, case, dict(TASK), fate="settles", cite=True, lock_sha256=lock_pin
        )
    with pytest.raises(TypeError):
        unpinned(
            folder, case, dict(TASK), fate="settles", cite=True, client_sha256=source
        )


def reversed_keys(record: Json) -> Json:
    return {key: record[key] for key in reversed(list(record))}


def test_lock_key_order_and_layout_do_not_move_the_pin(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    path = folder / "lock.json"
    path.write_text(json.dumps(lock(case), indent=4), "utf-8")
    assert inspect(folder, case).status == "verified"
    path.write_text(json.dumps(lock(case), indent=2, sort_keys=True), "utf-8")
    assert inspect(folder, case).status == "verified"
    shuffled = reversed_keys(lock(case))
    shuffled["task"] = reversed_keys(shuffled["task"])
    shuffled["environment"] = reversed_keys(shuffled["environment"])
    assert list(shuffled) != list(lock(case))
    path.write_text(json.dumps(shuffled), "utf-8")
    assert inspect(folder, case).status == "verified"


def unrelated_knob(files: dict[str, Any]) -> None:
    files["lock"]["verifier"]["disable"] = True


def added_lock_field(files: dict[str, Any]) -> None:
    files["lock"]["agent_timeout_multiplier"] = 2.0


@pytest.mark.parametrize(
    "edit",
    [
        unrelated_knob,
        added_lock_field,
        set_path(("lock", "timeout_multiplier"), 2.0),
        set_path(("lock", "timeout_multiplier"), 1),
        set_path(("lock", "task", "version"), "2"),
    ],
)
def test_lock_drift_outside_world_and_team_is_a_conflict(
    tmp_path: Path, edit: Edit
) -> None:
    folder, case = stage(tmp_path, edit=edit)
    written = json.loads(folder.joinpath("lock.json").read_text("utf-8"))
    assert written["environment"] == lock(case)["environment"]
    assert written["participants"] == lock(case)["participants"]
    result = inspect(folder, case)
    ungraded(result, "conflict")
    assert result.trial == TRIAL
    assert result.reason == "lock.json is not the enrolled lock"


def test_another_well_formed_pin_is_a_conflict(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    result = inspect(folder, case, lock_sha256="sha256:" + "0" * 64)
    ungraded(result, "conflict")
    assert result.reason == "lock.json is not the enrolled lock"


def trial_uri(edit: Callable[[str], str]) -> Edit:
    def change(files: dict[str, Any]) -> None:
        files["result"]["trial_uri"] = edit(files["result"]["trial_uri"])

    return change


@pytest.mark.parametrize(
    "edit",
    [
        trial_uri(lambda uri: uri.rsplit("/", 1)[0] + "/dr-settles-plain"),
        trial_uri(lambda uri: uri.rsplit("/", 1)[0]),
        trial_uri(lambda uri: uri + "/"),
        trial_uri(lambda uri: uri.removeprefix("file://")),
        set_path(("result", "trial_uri"), None),
    ],
)
def test_result_outside_the_private_trial_folder_is_a_conflict(
    tmp_path: Path, edit: Edit
) -> None:
    folder, case = stage(tmp_path, edit=edit)
    result = inspect(folder, case)
    ungraded(result, "conflict")
    assert result.reason == "result.json trial is not the private workspace folder"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("finished_at", "yesterday"),
        ("finished_at", "2026-11-25T10:03:00"),
        ("finished_at", "2026-11-25T09:59:59Z"),
        ("finished_at", "2026-11-25T10:03:00Z" + " " * 64),
        ("finished_at", "2026-11-25T10:03:00\u00a0Z"),
        ("finished_at", 1764064980),
        ("started_at", None),
        ("started_at", "2026-11-25T10:00:00"),
        ("started_at", "2026-11-25T11:00:00+00:30"),
    ],
)
def test_malformed_or_reversed_timestamps_cannot_grade(
    tmp_path: Path, field: str, value: Any
) -> None:
    folder, case = stage(tmp_path, edit=set_path(("result", field), value))
    result = inspect(folder, case)
    ungraded(result, "conflict")
    assert result.trial == TRIAL


def journal_edit(change: Callable[[list[Json]], None]) -> Edit:
    def edit(files: dict[str, Any]) -> None:
        change(files["events"])

    return edit


def set_event(index: int, field: str, value: Any) -> Edit:
    def change(events: list[Json]) -> None:
        events[index][field] = value

    return journal_edit(change)


@pytest.mark.parametrize(
    "edit",
    [
        journal_edit(lambda events: events.extend(copy.deepcopy(events))),
        journal_edit(lambda events: events.insert(1, copy.deepcopy(events[0]))),
        journal_edit(
            lambda events: events.insert(
                5, {"event": "cancel", "timestamp": "2026-11-25T10:02:40Z"}
            )
        ),
        journal_edit(
            lambda events: events.append(
                {"event": "start", "timestamp": "2026-11-25T10:04:00Z"}
            )
        ),
        journal_edit(lambda events: events.reverse()),
        set_event(0, "timestamp", "2026-11-25T09:59:59Z"),
        set_event(5, "timestamp", "2026-11-25T10:02:59Z"),
        set_event(2, "seat", "relief"),
    ],
)
def test_reused_or_inconsistent_trial_events_cannot_grade(
    tmp_path: Path, edit: Edit
) -> None:
    folder, case = stage(tmp_path, edit=edit)
    result = inspect(folder, case)
    ungraded(result, "conflict")
    assert result.trial == TRIAL
    assert result.reason == "trial events are not one closed attempt"


def test_trial_events_link_is_a_conflict(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    path = folder.joinpath(*outcomes.EVENTS)
    moved = tmp_path / "events.jsonl"
    path.rename(moved)
    path.symlink_to(moved)
    ungraded(inspect(folder, case), "conflict")


def test_duplicated_finish_or_relief_ownership_cannot_grade(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    path = folder / "result.json"
    text = path.read_text("utf-8")
    finish = '"finished_at": "2026-11-25T10:03:00Z"'
    assert text.count(finish) == 1
    path.write_text(text.replace(finish, finish + ", " + finish), "utf-8")
    ungraded(inspect(folder, case), "conflict")

    def two_reliefs(files: dict[str, Any]) -> None:
        rows = files["result"]["seat_results"]
        rows[0] = copy.deepcopy(rows[1])

    folder, case = stage(tmp_path / "second", edit=two_reliefs)
    result = inspect(folder, case)
    ungraded(result, "conflict")
    assert result.reason == "native seat result role differs"


def receipt_edit(field: str, value: Any) -> Edit:
    def edit(files: dict[str, Any]) -> None:
        context = files["result"]["seat_results"][1]["agent_result"]
        context["metadata"][control.METADATA_KEY][field] = value

    return edit


def extra_metadata(files: dict[str, Any]) -> None:
    files["result"]["seat_results"][1]["agent_result"]["metadata"]["note"] = "x"


def receipt_on_outgoing_only(files: dict[str, Any]) -> None:
    rows = files["result"]["seat_results"]
    rows[0]["agent_result"], rows[1]["agent_result"] = rows[1]["agent_result"], None


@pytest.mark.parametrize(
    "edit",
    [
        receipt_edit("cite", False),
        receipt_edit("cite", 1),
        receipt_edit("outcome", "resolve_refused"),
        receipt_edit("outcome", None),
        receipt_edit("client_sha256", "0" * 64),
        receipt_edit("seat", "outgoing"),
        receipt_edit("schema", "harbor.ship-decision-receipt.control/v2"),
        receipt_edit("extra", True),
        extra_metadata,
    ],
)
def test_contradicting_control_receipt_is_a_conflict(
    tmp_path: Path, edit: Edit
) -> None:
    folder, case = stage(tmp_path, edit=edit)
    result = inspect(folder, case)
    ungraded(result, "conflict")
    assert result.trial == TRIAL
    assert control.CLIENT_SHA256 not in (result.reason or "")


def test_another_well_formed_client_pin_is_a_conflict(tmp_path: Path) -> None:
    folder, case = stage(tmp_path)
    other = "0" * 64
    assert other != control.CLIENT_SHA256
    result = inspect(folder, case, client_sha256=other)
    ungraded(result, "conflict")
    assert result.trial == TRIAL
    assert (
        result.reason == "relief seat receipt is not the sealed control for this cite"
    )


def test_receipt_held_by_the_outgoing_seat_is_unqualified(tmp_path: Path) -> None:
    folder, case = stage(tmp_path, edit=receipt_on_outgoing_only)
    ungraded(inspect(folder, case), "seat_unqualified")


def test_valid_receipt_keeps_a_contradictory_world_decision(tmp_path: Path) -> None:
    folder, case = stage(tmp_path, "settles", False, frozen=cut("settles", True))
    result = inspect(folder, case, "settles", False)
    assert result.status == "mismatch" and result.citation == "original"
