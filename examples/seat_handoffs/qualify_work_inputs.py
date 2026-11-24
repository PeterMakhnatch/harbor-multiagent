import json
import shutil
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .job_members import JOB_LOCK_SCHEMA
from .schedule_exit_clock import STOCK_PAIR, TIMEOUT
from .work_book import SCHEMA as BOOK_SCHEMA
from .work_book import _row, _work, check, load, save
from .work_jobs import (
    BINDING_INVALID,
    BINDING_MISMATCH,
    BINDING_MISSING,
    BOARDS_MS,
    MEASURED,
    PRODUCER_COMMIT,
    PRODUCER_VERSION,
    TRIGGER_ABSENT,
    TRIGGER_UNKNOWN,
    UNSUPPORTED,
    _meter,
    _source,
)
from .work_meter import GUARDS
from .work_report import summarize
from .work_trace import HARNESS, MAX_STEPS, MAX_TOKENS, SCHEMA_VERSION, Receipt

SCHEMA = "harbor.post-return-work-inputs-qualify/v1"
T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
UTC_SUFFIX = "+00:00"
TRIAL = "schedule-007-talk__qWk3Rz"
OTHER_TRIAL = "schedule-007-talk__Zr8mPq"
NATIVE_ID = "5f0c2a9e-7d41-4b8a-9c3e-1a2b3c4d5e6f"
OTHER_ID = "5f0c2a9e-7d41-4b8a-9c3e-1a2b3c4d5e70"
DIGEST = "5e" * 32
PROVIDER = "openai"
MODEL = "gpt-qualify"
CONFIGURED = f"{PROVIDER}/{MODEL}"
FOREIGN_MODEL = f"{PROVIDER}/gpt-foreign"
SECRET = "PRIVATE-WORK-TEXT-7f3a"
SENTINELS = (TRIAL, OTHER_TRIAL, MODEL, CONFIGURED, SECRET, NATIVE_ID, DIGEST)
PLAY_S = (0.0, 1.0, 100.0)
SIGNAL_S = (2.0, 50.0)
PEER_S = (2.0, 90.0)
INITIAL_S = 3.0
STEPS = ((20.25, 10, 2), (70.5, 20, 3))
NATIVE = (40, 7)
ZERO_STEPS = ((20.25, 0, 0), (70.5, 0, 0))
CAP_STEPS = tuple((20.25, 0, 0) for _ in range(MAX_STEPS))
EMPTY = [0, 0, 0]
EXPECTED = {
    "bins": [[1, 10, 2], EMPTY, EMPTY, [1, 20, 3], EMPTY, EMPTY, EMPTY],
    "native": [40, 7],
}
ALT_WORK = {
    "zero_native": {
        "bins": [[1, 0, 0], EMPTY, EMPTY, [1, 0, 0], EMPTY, EMPTY, EMPTY],
        "native": [0, 0],
    },
    "zero_no_steps": {"bins": [EMPTY] * 7, "native": [0, 0]},
    "tail_only": {"bins": [EMPTY] * 7, "native": [40, 7]},
    "step_cap_reached": {
        "bins": [[MAX_STEPS, 0, 0], *([EMPTY] * 6)],
        "native": [40, 7],
    },
}
ZERO_NAMES = frozenset({"zero_native", "zero_no_steps"})
SIGNAL_ROW = ("result", "seat_results", 0)
PEER_ROW = ("result", "seat_results", 1)
PEER_RESULT = (*PEER_ROW, "agent_result")
STEP = ("trajectory", "steps")
FINAL = ("trajectory", "final_metrics")
HARBOR = ("lock", "harbor")
PEER_AGENT = f"{TRIAL}/seats/p2/agent"
PEER_ATIF = f"{PEER_AGENT}/trajectory.json"
SIGNAL_ATIF = f"{TRIAL}/seats/p1/agent/trajectory.json"
SAMPLE = Path(__file__).with_name("samples") / "post-return-completed.json"
MAX_PATH = 8
DROP = object()

Put = tuple[tuple[Any, ...], Any]
Outcome = tuple["Control", str, Receipt | None]
Check = tuple[str, bool]


@dataclass(frozen=True)
class Control:
    name: str
    expect: str
    puts: tuple[Put, ...] = ()
    edit: Callable[[dict[str, Any]], None] | None = None
    disk: Callable[[Path], None] | None = None
    case: tuple[tuple[str, Any], ...] = ()
    signal: str = "p1"
    steps: tuple[tuple[float, int, int], ...] = STEPS
    native: tuple[int, int] = NATIVE
    meter: tuple[int, int] | None = None
    model: str = CONFIGURED
    suffix: str = UTC_SUFFIX


def _stamp(seconds: float, suffix: str) -> str:
    moment = T0 + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + suffix


def _other(seat: str) -> str:
    assert seat in STOCK_PAIR
    return STOCK_PAIR[1 - STOCK_PAIR.index(seat)]


def _sums(steps: tuple[tuple[float, int, int], ...]) -> tuple[int, int]:
    return sum(step[1] for step in steps), sum(step[2] for step in steps)


def _put(doc: Any, path: tuple[Any, ...], value: Any) -> None:
    assert 1 <= len(path) <= MAX_PATH
    node = doc
    for key in path[:-1]:
        node = node[key]
    if value is DROP:
        del node[path[-1]]
    else:
        node[path[-1]] = value


def _seat_row(seat: str, span: tuple[float, float], control: Control) -> dict[str, Any]:
    meter = control.meter if control.meter is not None else _sums(control.steps)
    usage = None
    if control.steps:
        usage = {
            control.model: {"n_input_tokens": meter[0], "n_output_tokens": meter[1]}
        }
    return {
        "seat": seat,
        "under_test": True,
        "exception_info": None,
        "agent_execution": {
            "started_at": _stamp(span[0], control.suffix),
            "finished_at": _stamp(span[1], control.suffix),
        },
        "agent_info": {
            "name": HARNESS[0],
            "version": HARNESS[1],
            "model_info": {"name": MODEL, "provider": PROVIDER},
        },
        "agent_result": {
            "n_input_tokens": control.native[0],
            "n_output_tokens": control.native[1],
            "metadata": {"summarization_count": 0},
            "model_usage": usage,
        },
    }


def _trajectory(
    seat: str,
    steps: tuple[tuple[float, int, int], ...],
    model: str,
    native: tuple[int, int],
    suffix: str,
) -> dict[str, Any]:
    assert len(steps) <= MAX_STEPS + 1
    rows: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "timestamp": _stamp(INITIAL_S, suffix),
            "source": "user",
            "message": SECRET,
        }
    ]
    for index, (end, prompt, completion) in enumerate(steps, start=2):
        rows.append(
            {
                "step_id": index,
                "timestamp": _stamp(end, suffix),
                "source": "agent",
                "model_name": model,
                "message": SECRET,
                "metrics": {"prompt_tokens": prompt, "completion_tokens": completion},
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": f"{TRIAL}__{seat}",
        "agent": {"name": HARNESS[0], "version": HARNESS[1], "model_name": CONFIGURED},
        "steps": rows,
        "final_metrics": {
            "total_prompt_tokens": native[0],
            "total_completion_tokens": native[1],
        },
    }


def _docs(control: Control) -> dict[str, Any]:
    signal = control.signal
    peer = _other(signal)
    spans = {signal: SIGNAL_S, peer: PEER_S}
    suffix = control.suffix
    participants = [
        {
            "seat": seat,
            "name": HARNESS[0],
            "model_name": CONFIGURED,
            "resume_trajectory": False,
            "load_trajectory": None,
        }
        for seat in STOCK_PAIR
    ]
    result = {
        "id": NATIVE_ID,
        "trial_name": TRIAL,
        "task_checksum": DIGEST,
        "exception_info": None,
        "started_at": _stamp(PLAY_S[0], suffix),
        "finished_at": _stamp(PLAY_S[2] + 5, suffix),
        "agent_execution": {
            "started_at": _stamp(PLAY_S[1], suffix),
            "finished_at": _stamp(PLAY_S[2], suffix),
        },
        "config": {
            "trial_name": TRIAL,
            "source_trial": None,
            "participants": participants,
        },
        "seat_results": [_seat_row(seat, spans[seat], control) for seat in STOCK_PAIR],
    }
    harbor = {
        "version": PRODUCER_VERSION,
        "git_commit_hash": PRODUCER_COMMIT,
        "is_editable": False,
    }
    return {
        "result": result,
        "trajectory": _trajectory(
            peer, control.steps, control.model, control.native, suffix
        ),
        "signal": _trajectory(signal, STEPS, CONFIGURED, NATIVE, suffix),
        "lock": {"schema_version": JOB_LOCK_SCHEMA, "harbor": harbor, "trials": []},
    }


def _dump(path: Path, record: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(record, allow_nan=False).encode("utf-8"))


def _atif(trial: Path, seat: str) -> Path:
    return trial / "seats" / seat / "agent" / "trajectory.json"


def _evaluate(root: Path, index: int, control: Control) -> tuple[str, Receipt | None]:
    job = root / f"job-{index:03d}"
    doc = _docs(control)
    for path, value in control.puts:
        _put(doc, path, value)
    if control.edit is not None:
        control.edit(doc)
    trial = job / TRIAL
    _dump(job / "lock.json", doc["lock"])
    _dump(trial / "result.json", doc["result"])
    _dump(_atif(trial, _other(control.signal)), doc["trajectory"])
    _dump(_atif(trial, control.signal), doc["signal"])
    if control.disk is not None:
        control.disk(job)
    case: dict[str, Any] = {
        "trigger": "observed",
        "first_seat": control.signal,
        "timed_out": False,
    }
    case.update(control.case)
    stock = {"native_trial": NATIVE_ID, "task_digest": DIGEST}
    return _meter(case, stock, trial, _source(job))


def _remove(rel: str) -> Callable[[Path], None]:
    def run(job: Path) -> None:
        target = job / rel
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    return run


def _relink(rel: str) -> Callable[[Path], None]:
    def run(job: Path) -> None:
        target = job / rel
        hidden = target.with_name(f"hidden-{target.name}")
        target.rename(hidden)
        target.symlink_to(hidden, target_is_directory=hidden.is_dir())

    return run


def _overwrite(rel: str, data: bytes | None) -> Callable[[Path], None]:
    def run(job: Path) -> None:
        target = job / rel
        body = target.read_bytes()
        target.write_bytes(body[: len(body) // 2] if data is None else data)

    return run


def _copy(source: str, destination: str) -> Callable[[Path], None]:
    def run(job: Path) -> None:
        shutil.copyfile(job / source, job / destination)

    return run


def _rename(source: str, destination: str) -> Callable[[Path], None]:
    def run(job: Path) -> None:
        (job / source).rename(job / destination)

    return run


def _side_files(job: Path) -> None:
    agent = job / PEER_AGENT
    (agent / "episode-0").mkdir()
    (agent / "episode-0" / "debug.json").write_text("{}", encoding="utf-8")
    (agent / "recording.cast").write_text(SECRET, encoding="utf-8")


def _reverse_seats(doc: dict[str, Any]) -> None:
    doc["result"]["seat_results"].reverse()
    doc["result"]["config"]["participants"].reverse()


def _optional_puts() -> tuple[Put, ...]:
    return (
        ((*STEP, 1, "metrics", "cached_tokens"), 4),
        ((*STEP, 1, "metrics", "cost_usd"), 0.001),
        ((*STEP, 2, "reasoning_content"), SECRET),
        ((*STEP, 2, "tool_calls"), []),
        ((*STEP, 2, "observation"), {"results": []}),
        ((*FINAL, "total_cached_tokens"), 4),
        ((*FINAL, "total_cost_usd"), 0.002),
        (("trajectory", "agent", "extra"), {"parser": "json", "temperature": 0.7}),
    )


def _equivalents() -> list[Control]:
    return [
        Control("baseline", MEASURED),
        Control("swapped_signal_seat", MEASURED, signal="p2"),
        Control("zulu_timestamps", MEASURED, suffix="Z"),
        Control("reordered_seats", MEASURED, edit=_reverse_seats),
        Control("optional_atif_fields", MEASURED, puts=_optional_puts()),
        Control("actual_step_model", MEASURED, model=MODEL),
        Control("editable_pin", MEASURED, puts=(((*HARBOR, "is_editable"), True),)),
        Control("agent_side_files", MEASURED, disk=_side_files),
        Control(
            "signal_trajectory_absent", MEASURED, disk=_remove(f"{TRIAL}/seats/p1")
        ),
        Control("zero_native", MEASURED, steps=ZERO_STEPS, native=(0, 0)),
        Control("zero_no_steps", MEASURED, steps=(), native=(0, 0)),
        Control("tail_only", MEASURED, steps=()),
        Control("step_cap_reached", MEASURED, steps=CAP_STEPS),
    ]


def _binding_controls() -> list[Control]:
    timeout = {"exception_type": TIMEOUT}
    late = _stamp(95.0, UTC_SUFFIX)
    return [
        Control(
            "case_first_seat_swapped", BINDING_MISMATCH, case=(("first_seat", "p2"),)
        ),
        Control(
            "signal_seat_errored",
            BINDING_MISMATCH,
            puts=(
                ((*SIGNAL_ROW, "exception_info"), {"exception_type": "RuntimeError"}),
            ),
        ),
        Control(
            "signal_seat_timed_out",
            BINDING_MISMATCH,
            puts=(
                ((*SIGNAL_ROW, "exception_info"), timeout),
                (("result", "exception_info"), timeout),
            ),
            case=(("timed_out", True),),
        ),
        Control(
            "signal_exit_outside_peer",
            BINDING_MISMATCH,
            puts=(((*SIGNAL_ROW, "agent_execution", "finished_at"), late),),
        ),
        Control("trial_symlink", BINDING_MISSING, disk=_relink(TRIAL)),
        Control(
            "result_missing", BINDING_MISSING, disk=_remove(f"{TRIAL}/result.json")
        ),
        Control(
            "result_not_json",
            BINDING_INVALID,
            disk=_overwrite(f"{TRIAL}/result.json", None),
        ),
        Control(
            "result_other_trial_id",
            BINDING_MISMATCH,
            puts=((("result", "id"), OTHER_ID),),
        ),
        Control(
            "result_other_task",
            BINDING_MISMATCH,
            puts=((("result", "task_checksum"), "0" * 64),),
        ),
        Control(
            "result_renamed_trial",
            BINDING_MISMATCH,
            puts=((("result", "trial_name"), OTHER_TRIAL),),
        ),
        Control(
            "config_other_trial",
            "seat_binding_mismatch",
            puts=((("result", "config", "trial_name"), OTHER_TRIAL),),
        ),
        Control(
            "config_source_trial",
            "unsupported_trajectory",
            puts=((("result", "config", "source_trial"), OTHER_TRIAL),),
        ),
        Control(
            "participant_resumed",
            "unsupported_trajectory",
            puts=(
                (("result", "config", "participants", 1, "resume_trajectory"), True),
            ),
        ),
        Control("trigger_absent", TRIGGER_ABSENT, case=(("trigger", "absent"),)),
        Control("trigger_unknown", TRIGGER_UNKNOWN, case=(("trigger", "unknown"),)),
    ]


def _pin_controls() -> list[Control]:
    return [
        Control("lock_missing", UNSUPPORTED, disk=_remove("lock.json")),
        Control("lock_symlink", UNSUPPORTED, disk=_relink("lock.json")),
        Control("lock_not_json", UNSUPPORTED, disk=_overwrite("lock.json", None)),
        Control(
            "lock_other_schema",
            UNSUPPORTED,
            puts=((("lock", "schema_version"), JOB_LOCK_SCHEMA - 1),),
        ),
        Control(
            "lock_other_version", UNSUPPORTED, puts=(((*HARBOR, "version"), "0.25.0"),)
        ),
        Control(
            "lock_other_commit",
            UNSUPPORTED,
            puts=(((*HARBOR, "git_commit_hash"), PRODUCER_COMMIT[::-1]),),
        ),
        Control(
            "lock_editable_unknown",
            UNSUPPORTED,
            puts=(((*HARBOR, "is_editable"), None),),
        ),
        Control("lock_extra_field", UNSUPPORTED, puts=(((*HARBOR, "source"), "git"),)),
    ]


def _atif_controls() -> list[Control]:
    session = ("trajectory", "session_id")
    agent = ("trajectory", "agent")
    return [
        Control(
            "wrong_run_session",
            "seat_binding_mismatch",
            puts=((session, f"{OTHER_TRIAL}__p2"),),
        ),
        Control(
            "session_names_signal_seat",
            "seat_binding_mismatch",
            puts=((session, f"{TRIAL}__p1"),),
        ),
        Control(
            "signal_trajectory_reused",
            "seat_binding_mismatch",
            disk=_copy(SIGNAL_ATIF, PEER_ATIF),
        ),
        Control("atif_missing", "missing_trajectory", disk=_remove(PEER_ATIF)),
        Control("agent_folder_missing", "missing_trajectory", disk=_remove(PEER_AGENT)),
        Control(
            "trajectory_renamed",
            "missing_trajectory",
            disk=_rename(PEER_ATIF, f"{PEER_AGENT}/trajectory.cont-1.json"),
        ),
        Control(
            "second_trajectory",
            "unsupported_trajectory",
            disk=_copy(PEER_ATIF, f"{PEER_AGENT}/trajectory.cont-1.json"),
        ),
        Control("atif_symlink", "invalid_trajectory", disk=_relink(PEER_ATIF)),
        Control("agent_folder_symlink", "invalid_trajectory", disk=_relink(PEER_AGENT)),
        Control(
            "atif_truncated", "invalid_trajectory", disk=_overwrite(PEER_ATIF, None)
        ),
        Control("atif_array", "invalid_trajectory", disk=_overwrite(PEER_ATIF, b"[]")),
        Control(
            "wrong_agent_harness",
            "unsupported_trajectory",
            puts=(((*agent, "name"), "claude-code"),),
        ),
        Control(
            "wrong_agent_model",
            "seat_binding_mismatch",
            puts=(((*agent, "model_name"), FOREIGN_MODEL),),
        ),
        Control(
            "wrong_seat_model",
            "seat_binding_mismatch",
            puts=(((*PEER_ROW, "agent_info", "model_info", "name"), "gpt-foreign"),),
        ),
        Control("wrong_step_model", "seat_binding_mismatch", model=FOREIGN_MODEL),
        Control(
            "mixed_step_models",
            "unsupported_trajectory",
            puts=(((*STEP, 2, "model_name"), MODEL),),
        ),
        Control(
            "summarized_peer",
            "unsupported_trajectory",
            puts=(((*PEER_RESULT, "metadata", "summarization_count"), 1),),
        ),
        Control(
            "trajectory_unknown_field",
            "unsupported_trajectory",
            puts=((("trajectory", "notes"), SECRET),),
        ),
        Control(
            "trajectory_old_schema",
            "unsupported_trajectory",
            puts=((("trajectory", "schema_version"), "ATIF-v1.7"),),
        ),
        Control(
            "step_unknown_field",
            "unsupported_trajectory",
            puts=(((*STEP, 1, "extra"), 1),),
        ),
        Control(
            "step_id_gap", "invalid_trajectory", puts=(((*STEP, 2, "step_id"), 4),)
        ),
        Control(
            "step_cap_exceeded",
            "invalid_trajectory",
            steps=(*CAP_STEPS, (STEPS[0][0], 0, 0)),
        ),
    ]


def _usage_controls() -> list[Control]:
    blank = {"n_input_tokens": 0, "n_output_tokens": 0}
    return [
        Control(
            "native_usage_missing",
            "usage_missing",
            puts=(((*PEER_RESULT, "n_input_tokens"), DROP),),
        ),
        Control("native_result_missing", "usage_missing", puts=((PEER_RESULT, None),)),
        Control(
            "native_bool_count",
            "usage_missing",
            puts=(((*PEER_RESULT, "n_output_tokens"), True),),
        ),
        Control(
            "step_usage_missing",
            "usage_missing",
            puts=(((*STEP, 2, "metrics", "prompt_tokens"), DROP),),
        ),
        Control(
            "step_metrics_null", "usage_missing", puts=(((*STEP, 2, "metrics"), None),)
        ),
        Control(
            "step_bool_tokens",
            "invalid_trajectory",
            puts=(((*STEP, 1, "metrics", "completion_tokens"), True),),
        ),
        Control(
            "step_tokens_over_cap",
            "invalid_trajectory",
            puts=(((*STEP, 1, "metrics", "prompt_tokens"), MAX_TOKENS + 1),),
        ),
        Control("final_metrics_null", "usage_missing", puts=((FINAL, None),)),
        Control(
            "final_total_missing",
            "usage_missing",
            puts=(((*FINAL, "total_prompt_tokens"), DROP),),
        ),
        Control(
            "final_disagrees",
            "usage_inconsistent",
            puts=(((*FINAL, "total_prompt_tokens"), NATIVE[0] + 1),),
        ),
        Control(
            "model_usage_missing",
            "usage_missing",
            puts=(((*PEER_RESULT, "model_usage"), None),),
        ),
        Control(
            "model_usage_extra_model",
            "usage_inconsistent",
            puts=(((*PEER_RESULT, "model_usage", FOREIGN_MODEL), blank),),
        ),
        Control("model_usage_changed", "usage_inconsistent", meter=(31, 5)),
        Control(
            "step_dropped", "usage_inconsistent", steps=STEPS[:1], meter=_sums(STEPS)
        ),
        Control(
            "step_added",
            "usage_inconsistent",
            steps=(*STEPS, (80.0, 1, 1)),
            meter=_sums(STEPS),
        ),
        Control("negative_residue", "usage_inconsistent", native=(25, 4)),
    ]


def _clock_controls() -> list[Control]:
    moment = T0 + timedelta(seconds=STEPS[0][0])
    offset = moment.astimezone(timezone(timedelta(hours=1))).isoformat()
    stamp = (*STEP, 1, "timestamp")
    return [
        Control("steps_out_of_order", "clock_unusable", steps=(STEPS[1], STEPS[0])),
        Control(
            "initial_before_peer",
            "clock_unusable",
            puts=(((*STEP, 0, "timestamp"), _stamp(1.0, UTC_SUFFIX)),),
        ),
        Control("step_after_peer", "clock_unusable", steps=(STEPS[0], (95.0, 20, 3))),
        Control(
            "naive_timestamp",
            "clock_unusable",
            puts=((stamp, _stamp(STEPS[0][0], "")),),
        ),
        Control("offset_timestamp", "clock_unusable", puts=((stamp, offset),)),
        Control("unparsable_timestamp", "clock_unusable", puts=((stamp, "yesterday"),)),
    ]


def _controls() -> list[Control]:
    controls = [
        *_equivalents(),
        *_binding_controls(),
        *_pin_controls(),
        *_atif_controls(),
        *_usage_controls(),
        *_clock_controls(),
    ]
    assert controls[0].name == "baseline"
    assert len({control.name for control in controls}) == len(controls)
    return controls


def _as_expected(
    control: Control, status: str, receipt: Receipt | None, baseline: Receipt | None
) -> bool:
    if control.expect != MEASURED:
        return status == control.expect and receipt is None
    if status != MEASURED or not isinstance(receipt, Receipt):
        return False
    if (
        control.name != "baseline"
        and control.name not in ALT_WORK
        and receipt != baseline
    ):
        return False
    return _work(receipt) == ALT_WORK.get(control.name, EXPECTED)


def _reason(error: BaseException) -> str:
    return f"{type(error).__name__}={str(error)[:96]}"


def _named(name: str, passed: bool, detail: str) -> Check:
    return (name, True) if passed else (f"{name}:{detail}", False)


def _attempt(
    root: Path, index: int, control: Control, baseline: Receipt | None
) -> Outcome | str:
    try:
        status, receipt = _evaluate(root, index, control)
    except Exception as error:
        return _reason(error)
    if status != control.expect:
        return f"got={status}"
    try:
        passed = _as_expected(control, status, receipt, baseline)
    except Exception as error:
        return _reason(error)
    return (control, status, receipt) if passed else "receipt_or_work_differs"


def _trace(root: Path, controls: list[Control]) -> tuple[list[Outcome], list[str]]:
    outcomes: list[Outcome] = []
    failed: list[str] = []
    baseline: Receipt | None = None
    for index, control in enumerate(controls):
        outcome = _attempt(root, index, control, baseline)
        if isinstance(outcome, str):
            failed.append(f"{control.name}:{outcome}")
            continue
        if control.name == "baseline":
            baseline = outcome[2]
        outcomes.append(outcome)
    assert len(outcomes) + len(failed) == len(controls)
    return outcomes, failed


def _public_row(
    control: Control, status: str, receipt: Receipt | None
) -> dict[str, Any]:
    trigger = dict(control.case).get("trigger", "observed")
    final = 1
    if trigger == "observed":
        grade, later = [final, final], [0, 1]
    elif trigger == "absent":
        grade, later = [final, final], [0, 0]
    else:
        grade, later = [0, 1], [0, 1]
    case = {
        "condition": "talk",
        "timed_out": False,
        "native_grade": final,
        "trigger": trigger,
        "first_seat": control.signal if trigger == "observed" else None,
        "grade": grade,
        "later_writes": later,
        "meter_status": status,
        "receipt": receipt,
    }
    return _row(case, dict(case))


def _book(outcomes: list[Outcome]) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = {1: [], 2: [], 3: []}
    for control, status, receipt in outcomes:
        job = 1 if status != MEASURED else 3 if control.name in ZERO_NAMES else 2
        groups[job].append(_public_row(control, status, receipt))
    jobs = [
        {
            "job": job,
            "slots": len(rows),
            "missing_slots": 0,
            "ambiguous_slots": 0,
            "statuses": {"usable": len(rows)} if rows else {},
            "rows": rows,
        }
        for job, rows in groups.items()
    ]
    return {
        "schema": BOOK_SCHEMA,
        "producer": PRODUCER_COMMIT,
        "include_timeouts": False,
        "guards_ms": list(GUARDS),
        "board_ms": list(BOARDS_MS),
        "jobs": jobs,
    }


def _summary_checks(
    book: dict[str, Any], guard: int, board: int | None, expect: dict[str, Any]
) -> list[Check]:
    tag = f"summary_g{guard}_b{board}"
    views = [
        summarize(book, guard=guard, board_ms=board, jobs=jobs)["groups"]["all"]
        for jobs in (None, frozenset({1}), frozenset({3}))
    ]
    whole, gap, zero = views
    total = expect["missing"] + expect["measured"]
    whole_work, zero_work = (
        whole["work_on_measured_teams"],
        zero["work_on_measured_teams"],
    )
    shown = [
        f"teams={view['teams']},successes={view['native_successes']},"
        f"measured={view['measured_teams']},work={view['work_on_measured_teams'] is not None}"
        for view in views
    ]
    return [
        _named(
            f"{tag}_all",
            whole["teams"] == total
            and whole["native_successes"] == total
            and whole["meter_statuses"] == expect["labels"]
            and whole["measured_teams"] == expect["measured"]
            and whole_work is not None
            and whole_work["prompt"]["native"] == expect["prompt"],
            shown[0],
        ),
        _named(
            f"{tag}_missing_not_zero",
            gap["teams"] == expect["missing"]
            and gap["native_successes"] == expect["missing"]
            and gap["measured_teams"] == 0
            and gap["work_on_measured_teams"] is None,
            shown[1],
        ),
        _named(
            f"{tag}_zero_measured",
            zero["teams"] == expect["zero"]
            and zero["measured_teams"] == expect["zero"]
            and zero_work is not None
            and zero_work["prompt"]["native"] == 0
            and zero_work["completion"]["bounds"] == [0, 0],
            shown[2],
        ),
    ]


def _book_checks(
    root: Path, outcomes: list[Outcome]
) -> tuple[list[Check], dict[str, Any]]:
    root.mkdir()
    book = _book(outcomes)
    path = root / "book.json"
    save(path, book)
    data = path.read_bytes()
    loaded = load(path)
    missing, _, zero = (job["rows"] for job in loaded["jobs"])
    rows = [row for job in loaded["jobs"] for row in job["rows"]]
    measured = [receipt for _, status, receipt in outcomes if status == MEASURED]
    expect = {
        "labels": Counter(status for _, status, _ in outcomes),
        "missing": len(outcomes) - len(measured),
        "measured": len(measured),
        "zero": sum(control.name in ZERO_NAMES for control, _, _ in outcomes),
        "prompt": sum(receipt.native[0] for receipt in measured if receipt is not None),
    }
    checks: list[Check] = [
        (
            "book_has_no_private_text",
            not any(text.encode() in data for text in SENTINELS),
        ),
        ("book_round_trip", loaded == book),
        ("book_grades_survive", bool(rows) and all(row["final"] == 1 for row in rows)),
        (
            "book_missing_without_work",
            bool(missing)
            and all(
                row["meter"] != MEASURED and row["work"] is None for row in missing
            ),
        ),
        (
            "book_zero_is_measured",
            len(zero) == len(ZERO_NAMES)
            and all(
                row["meter"] == MEASURED
                and row["work"] is not None
                and row["work"]["native"] == [0, 0]
                for row in zero
            ),
        ),
    ]
    for guard in GUARDS:
        for board in BOARDS_MS:
            checks.extend(_summary_checks(loaded, guard, board, expect))
    return checks, loaded


def _target(book: dict[str, Any]) -> tuple[int, int, int]:
    for job_index, job in enumerate(book["jobs"]):
        for row_index, row in enumerate(job["rows"]):
            if row["meter"] != MEASURED or row["trigger"] != "observed":
                continue
            for cell_index, cell in enumerate(row["work"]["bins"]):
                if cell[0] > 0 and cell[1] > 0:
                    return job_index, row_index, cell_index
    raise ValueError("public book has no measured row with placed prompt work")


def _work_mutations(book: dict[str, Any]) -> list[tuple[str, tuple[Put, ...]]]:
    job, index, cell_index = _target(book)
    row = ("jobs", job, "rows", index)
    work = (*row, "work")
    cell = (*work, "bins", cell_index)
    neighbour = (*work, "bins", (cell_index + 1) % 7, 0)
    native = book["jobs"][job]["rows"][index]["work"]["native"]
    placed = sum(bin_[1] for bin_ in book["jobs"][job]["rows"][index]["work"]["bins"])
    return [
        ("bool_step_count", (((*cell, 0), True),)),
        ("negative_tokens", (((*cell, 1), -1),)),
        ("float_native", (((*work, "native", 1), float(native[1])),)),
        ("native_over_cap", (((*work, "native", 0), MAX_TOKENS + 1),)),
        ("bin_steps_over_cap", (((*cell, 0), MAX_STEPS + 1),)),
        ("total_steps_over_cap", (((*cell, 0), MAX_STEPS), (neighbour, MAX_STEPS))),
        ("tokens_without_steps", (((*cell, 0), 0),)),
        ("clipped_totals", (((*work, "native", 0), placed - 1),)),
        ("measured_without_work", ((work, None),)),
        ("work_without_measurement", (((*row, "meter"), "usage_missing"),)),
    ]


def _target_final(book: dict[str, Any]) -> int:
    job, index, _ = _target(book)
    final = book["jobs"][job]["rows"][index]["final"]
    assert final in (0, 1)
    return final


def _absent_puts(book: dict[str, Any], grade: int) -> tuple[Put, ...]:
    job, index, _ = _target(book)
    row = ("jobs", job, "rows", index)
    return (
        ((*row, "trigger"), "absent"),
        ((*row, "first"), None),
        ((*row, "meter"), TRIGGER_ABSENT),
        ((*row, "work"), None),
        *(((*row, "boards", board, "grade"), [grade, grade]) for board in (0, 1)),
        *(((*row, "boards", board, "later_writes"), [0, 0]) for board in (0, 1)),
    )


def _row_mutations(book: dict[str, Any]) -> list[tuple[str, tuple[Put, ...]]]:
    job, index, _ = _target(book)
    row = ("jobs", job, "rows", index)
    final = _target_final(book)
    unobserved = (
        ((*row, "first"), None),
        *(((*row, "boards", board, "grade"), [final, final]) for board in (0, 1)),
        *(((*row, "boards", board, "later_writes"), [0, 0]) for board in (0, 1)),
    )
    return [
        ("bool_final", (((*row, "final"), True),)),
        ("unknown_meter", (((*row, "meter"), "zero_work"),)),
        ("unknown_condition", (((*row, "condition"), "chat"),)),
        ("unknown_row_field", (((*row, "seat"), "p2"),)),
        ("measured_absent_trigger", (((*row, "trigger"), "absent"), *unobserved)),
        ("measured_unknown_trigger", (((*row, "trigger"), "unknown"), *unobserved)),
        ("absent_altered_grade", _absent_puts(book, 1 - final)),
        (
            "fresh_writes_wider",
            (
                ((*row, "boards", 0, "later_writes"), [0, 1]),
                ((*row, "boards", 1, "later_writes"), [0, 2]),
            ),
        ),
        (
            "timeout_outside_population",
            ((("include_timeouts",), False), ((*row, "timed_out"), True)),
        ),
        (
            "fresh_grade_wider",
            (
                ((*row, "boards", 0, "grade"), [1, 1]),
                ((*row, "boards", 1, "grade"), [0, 1]),
            ),
        ),
        ("later_writes_inverted", (((*row, "boards", 0, "later_writes"), [1, 0]),)),
        ("dropped_row", ((row, DROP),)),
        ("slots_inflated", ((("jobs", job, "slots"), book["jobs"][job]["slots"] + 1),)),
        ("unknown_job_field", ((("jobs", job, "note"), "x"),)),
        ("missing_job", ((("jobs",), []),)),
        ("job_gap", ((("jobs", 0, "job"), 2),)),
        ("unknown_producer", ((("producer",), "0" * 40),)),
        ("unknown_schema", ((("schema",), BOOK_SCHEMA.replace("/v1", "/v2")),)),
        ("unknown_top_field", ((("note",), "x"),)),
        ("guards_changed", ((("guards_ms",), [0, 1000]),)),
        ("board_changed", ((("board_ms",), [None, 500]),)),
        ("int_include_timeouts", ((("include_timeouts",), 0),)),
    ]


def _refusal(call: Callable[[], Any]) -> str:
    try:
        call()
    except ValueError:
        return ""
    except Exception as error:
        return _reason(error)
    return "accepted"


def _rejections(root: Path, base: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    mutations = [*_work_mutations(base), *_row_mutations(base)]
    for index, (name, puts) in enumerate(mutations):
        book = deepcopy(base)
        for path, value in puts:
            _put(book, path, value)
        target = root / f"mutated-{index:03d}.json"
        target.write_text(json.dumps(book), encoding="utf-8")
        checked = _refusal(lambda: check(book))
        loaded = _refusal(lambda: load(target))
        detail = f"check={checked or 'refused'},load={loaded or 'refused'}"
        checks.append(
            _named(f"public_reject_{name}", not checked and not loaded, detail)
        )
    text = json.dumps(base)
    raw = (
        ("duplicate_key", '{"schema":' + json.dumps(base["schema"]) + "," + text[1:]),
        ("nan_literal", '{"nan":NaN,' + text[1:]),
        ("not_object", f"[{text}]"),
        ("missing_file", None),
    )
    for name, body in raw:
        target = root / f"raw-{name}.json"
        if body is not None:
            target.write_text(body, encoding="utf-8")
        loaded = _refusal(lambda: load(target))
        checks.append(_named(f"public_reject_{name}", not loaded, f"load={loaded}"))
    return checks


def _summaries(book: dict[str, Any]) -> list[dict[str, Any]]:
    return [summarize(book, guard=g, board_ms=b) for g in GUARDS for b in BOARDS_MS]


def _reloaded(root: Path, name: str, book: dict[str, Any]) -> dict[str, Any]:
    target = root / f"alternate-{name}.json"
    target.write_text(json.dumps(book), encoding="utf-8")
    return load(target)


def _no_teams(root: Path, base: dict[str, Any]) -> bool:
    book = deepcopy(base)
    book["jobs"] = [
        {
            "job": 1,
            "slots": 4,
            "missing_slots": 1,
            "ambiguous_slots": 1,
            "statuses": {"seat_timeout": 2},
            "rows": [],
        }
    ]
    report = summarize(_reloaded(root, "no_teams", book), guard=0, board_ms=None)
    whole = report["groups"]["all"]
    return (
        whole["teams"] == 0
        and whole["measured_teams"] == 0
        and whole["work_on_measured_teams"] is None
        and report["selected_slots"] == 4
        and report["stock_statuses"] == {"seat_timeout": 2}
    )


def _alternates(root: Path, base: dict[str, Any]) -> list[Check]:
    reference = _summaries(base)
    reversed_book = deepcopy(base)
    for job in reversed_book["jobs"]:
        job["rows"].reverse()
    absent_book = deepcopy(base)
    for path, value in _absent_puts(base, _target_final(base)):
        _put(absent_book, path, value)
    absent_reference = _summaries(absent_book)
    trials: list[tuple[str, Callable[[], bool]]] = [
        ("unchanged", lambda: _summaries(_reloaded(root, "same", base)) == reference),
        (
            "rows_reversed",
            lambda: _summaries(_reloaded(root, "reversed", reversed_book)) == reference,
        ),
        ("no_usable_teams", lambda: _no_teams(root, base)),
        (
            "absent_row",
            lambda: (
                _summaries(_reloaded(root, "absent", absent_book)) == absent_reference
            ),
        ),
    ]
    checks: list[Check] = []
    for name, trial in trials:
        try:
            accepted, detail = trial(), "rejected"
        except Exception as error:
            accepted, detail = False, _reason(error)
        checks.append(_named(f"public_accept_{name}", accepted, detail))
    return checks


def _public(
    root: Path, synthetic: dict[str, Any] | None
) -> tuple[bool, list[Check], list[Check]]:
    root.mkdir()
    present = SAMPLE.is_file()
    base = load(SAMPLE) if present else synthetic
    if base is None:
        raise ValueError("no public book to mutate")
    return present, _rejections(root, base), _alternates(root, base)


def main() -> int:
    controls = _controls()
    with TemporaryDirectory(prefix="post-return-inputs-") as scratch:
        root = Path(scratch)
        (root / "trace").mkdir()
        outcomes, failed = _trace(root / "trace", controls)
        try:
            book_checks, synthetic = _book_checks(root / "book", outcomes)
        except Exception as error:
            book_checks, synthetic = [(f"book_stage:{_reason(error)}", False)], None
        try:
            present, rejected, accepted = _public(root / "public", synthetic)
        except Exception as error:
            present, rejected, accepted = (
                False,
                [(f"public_stage:{_reason(error)}", False)],
                [],
            )
    checks = [*book_checks, *rejected, *accepted]
    trace_passed = len(outcomes)
    failed.extend(name for name, passed in checks if not passed)
    statuses = Counter(status for _, status, _ in outcomes)
    ok = not failed and bool(outcomes) and bool(rejected) and bool(accepted)
    report = {
        "schema": SCHEMA,
        "trace_controls": [trace_passed, len(controls)],
        "meter_statuses": dict(sorted(statuses.items())),
        "measured_zero": sum(control.name in ZERO_NAMES for control, _, _ in outcomes),
        "book_checks": [sum(passed for _, passed in book_checks), len(book_checks)],
        "public_sample": present,
        "public_rejections": [sum(passed for _, passed in rejected), len(rejected)],
        "public_alternates": [sum(passed for _, passed in accepted), len(accepted)],
        "failed": failed,
        "ok": ok,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
