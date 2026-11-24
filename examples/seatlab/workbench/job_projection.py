import copy
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from . import arm_config, arm_runtime, arm_sources, isolate
from .arm_config import MAX_PATH_BYTES, MAX_TRIAL_NAME, _fail, _require
from .seat_stream import MIB, Record
from .worker_handoff import MAX_CONCURRENCY, MAX_COPIES, MAX_RETRIES, RECEIPT_SCHEMA

SCHEMA = "seatlab.workbench.paired-job-plans/v1"
SIDES = ("left", "right")
JOB_FIELDS = frozenset(
    {
        "schema_version",
        "job_name",
        "jobs_dir",
        "n_attempts",
        "n_concurrent_trials",
        "retry",
        "tasks",
        "participants",
        "environment",
    }
)
RETRY_FIELDS = frozenset({"max_retries"})
VOLATILE = ("trial_name", "trials_dir", "job_id")
MIN_SEATS = 2
MAX_PROJECTED_BYTES = 16 * MIB
PLAN_ID = UUID("00000000-0000-4000-8000-000000000000")
KEY = re.compile(r"sha256:[0-9a-f]{64}")
CAVEAT = (
    "Offline plan read from two local job configs through native Harbor "
    "expansion. Concurrency and max_retries are the requested launch limits, "
    "not observed provider concurrency, throttling, retries or timing. This "
    "check ran 0 jobs and 0 trials: it shows 0 actual registrations and 0 "
    "grades, and it does not estimate cost."
)

NATIVE_MISSING = "job projection needs the installed Harbor fork"
UNSUPPORTED = "job config sets a field outside the local job profile"
BAD_BOOKKEEPING = "job config job_name or jobs_dir is not bounded text"
BAD_SCHEDULE = "job config attempts, concurrency or retry are outside the profile"
BAD_TASKS = "job config tasks is not exactly one local task path"
TOO_FEW = "job config participants do not name at least two seats"
JOB_REFUSED = "job config fails native job-config validation"
JOB_CHANGED = "native Harbor records the job config differently"
EXPANSION_REFUSED = "native Harbor cannot expand the job config"
COPIES_DIFFER = "native Harbor expands copies that differ from the standalone trial"
TOO_LARGE = "job config projects more input than the offline bound"
WRONG_SEATS = "job config participants do not match the task's declared seats"
BAD_COMPARE = "job plans cannot be compared with these keys or copies"


@dataclass(frozen=True)
class Projection:
    input: str
    prepared_input: str
    copies: int
    concurrency: int
    max_retries: int


def _native() -> tuple[SimpleNamespace, SimpleNamespace]:
    try:
        from harbor.job_plan import JobPlan
        from harbor.models.job.config import JobConfig

        harbor = arm_config._native()
    except ImportError:
        _fail(NATIVE_MISSING)
    return harbor, SimpleNamespace(JobConfig=JobConfig, JobPlan=JobPlan)


def _count(value: Any, low: int, high: int) -> None:
    _require(type(value) is int and low <= value <= high, BAD_SCHEDULE)


def _schedule(document: dict[str, Any]) -> None:
    _require(set(document) <= JOB_FIELDS, UNSUPPORTED)
    if "job_name" in document:
        arm_config._bounded(document["job_name"], MAX_TRIAL_NAME, BAD_BOOKKEEPING)
    if "jobs_dir" in document:
        arm_config._bounded(document["jobs_dir"], MAX_PATH_BYTES, BAD_BOOKKEEPING)
    if "n_attempts" in document:
        _count(document["n_attempts"], 1, MAX_COPIES)
    if "n_concurrent_trials" in document:
        _count(document["n_concurrent_trials"], 1, MAX_CONCURRENCY)
    if "retry" in document:
        retry = document["retry"]
        _require(type(retry) is dict and set(retry) <= RETRY_FIELDS, BAD_SCHEDULE)
        if "max_retries" in retry:
            _count(retry["max_retries"], 0, MAX_RETRIES)
    tasks = document.get("tasks")
    _require(type(tasks) is list and len(tasks) == 1, BAD_TASKS)


def _trial_document(document: dict[str, Any]) -> dict[str, Any]:
    trial = {"task": document["tasks"][0], "participants": document.get("participants")}
    for field in ("schema_version", "environment"):
        if field in document:
            trial[field] = document[field]
    return trial


def _native_job(native: SimpleNamespace, document: dict[str, Any]) -> Any:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            return native.JobConfig.model_validate(copy.deepcopy(document))
    except (ValueError, TypeError, RecursionError, Warning):
        _fail(JOB_REFUSED)


def _faithful_job(job: Any, document: dict[str, Any]) -> None:
    retry = document.get("retry", {})
    _require(job.n_attempts == document.get("n_attempts", 1), JOB_CHANGED)
    _require(job.retry.max_retries == retry.get("max_retries", 0), JOB_CHANGED)
    concurrency = document.get("n_concurrent_trials", job.n_concurrent_trials)
    _require(job.n_concurrent_trials == concurrency, JOB_CHANGED)
    _require(type(job.n_concurrent_trials) is int, JOB_CHANGED)
    _require(1 <= job.n_concurrent_trials <= MAX_CONCURRENCY, JOB_CHANGED)
    if "job_name" in document:
        _require(job.job_name == document["job_name"], JOB_CHANGED)
    if "jobs_dir" in document:
        _require(str(job.jobs_dir) == document["jobs_dir"], JOB_CHANGED)
    _require(not (job.datasets or job.source_jobs or job.metrics), JOB_CHANGED)
    _require(not job.install_only and job.user_agent is None, JOB_CHANGED)
    _require(len(job.agents) == 1 and len(job.tasks) == 1, JOB_CHANGED)
    _require(str(job.tasks[0].path) == document["tasks"][0]["path"], JOB_CHANGED)


def _expand(native: SimpleNamespace, job: Any) -> list[Any]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            tasks = [task.model_copy(deep=True) for task in job.tasks]
            trials = native.JobPlan.build_trial_configs(job, tasks, job_id=PLAN_ID)
    except (ValueError, TypeError, RecursionError, Warning):
        _fail(EXPANSION_REFUSED)
    _require(type(trials) is list and len(trials) == job.n_attempts, JOB_CHANGED)
    return trials


def _identity(config: Any) -> bytes:
    dumped = arm_config._dump(config)
    for key in VOLATILE:
        dumped.pop(key, None)
    try:
        return arm_config._plain(dumped)
    except (ValueError, TypeError, RecursionError):
        _fail(JOB_CHANGED)


def project(path: Path) -> Projection:
    harbor, native = _native()
    stamp, document = arm_config._load(path)
    _schedule(document)
    trial = _trial_document(document)
    text = arm_config._profile(trial, harbor)
    _require(len(trial["participants"]) >= MIN_SEATS, TOO_FEW)
    spelled = arm_config._task_dir(Path(text), text)
    task_dir, tree = arm_sources.bounded_tree(spelled)
    _require(task_dir == spelled, arm_config.WRONG_TASK)
    standalone = arm_config._native_config(harbor, trial)
    arm_config._faithful(standalone, trial)
    job = _native_job(native, document)
    _faithful_job(job, document)
    identity = _identity(standalone)
    _require(job.n_attempts * len(identity) <= MAX_PROJECTED_BYTES, TOO_LARGE)
    trials = _expand(native, job)
    first = _identity(trials[0])
    _require(first == identity, COPIES_DIFFER)
    _require(all(_identity(copy_) == first for copy_ in trials[1:]), COPIES_DIFFER)
    record = arm_config._lock(harbor, trials[0], task_dir)
    runtime = arm_runtime.inspect_record(record)
    source = arm_sources.inspect_source(task_dir, record["task"])
    _require(runtime.seats == source.seats, WRONG_SEATS)
    arm_config._settled(path, stamp, text, task_dir, tree)
    prepared = isolate.prepared_input(isolate.Arm(source, runtime))
    assert _key(runtime.record_key) and _key(prepared)
    return Projection(
        input=runtime.record_key,
        prepared_input=prepared,
        copies=len(trials),
        concurrency=job.n_concurrent_trials,
        max_retries=job.retry.max_retries,
    )


def _key(value: Any) -> bool:
    return type(value) is str and KEY.fullmatch(value) is not None


def export(path: Path) -> Record:
    plan = project(path)
    return {
        "schema": RECEIPT_SCHEMA,
        "prepared_input": plan.prepared_input,
        "trial_lock_record": plan.input,
        "copies": plan.copies,
        "concurrency": plan.concurrency,
        "max_retries": plan.max_retries,
    }


def compare(
    left: Projection, right: Projection, keys: tuple[str, str], copies: int
) -> Record:
    _require(isinstance(left, Projection), BAD_COMPARE)
    _require(isinstance(right, Projection), BAD_COMPARE)
    _require(type(keys) is tuple and len(keys) == 2, BAD_COMPARE)
    _require(keys[0] != keys[1] and all(_key(key) for key in keys), BAD_COMPARE)
    _require(type(copies) is int and 1 <= copies <= MAX_COPIES, BAD_COMPARE)
    reasons: list[str] = []
    arms: Record = {}
    for side, plan, key in zip(SIDES, (left, right), keys, strict=True):
        if plan.input != key:
            reasons.append(f"{side} job projects an input other than its assigned key")
        if plan.copies != copies:
            reasons.append(f"{side} job does not expand to the requested copies")
        arms[side] = {
            "expected_input": key,
            "projected_input": plan.input,
            "copies": plan.copies,
            "concurrency": plan.concurrency,
            "max_retries": plan.max_retries,
        }
    if left.concurrency != right.concurrency:
        reasons.append("the two jobs request different launch concurrency")
    if left.max_retries != right.max_retries:
        reasons.append("the two jobs request different retry limits")
    return {
        "schema": SCHEMA,
        "status": "different_plan" if reasons else "matches_plan",
        "copies_per_arm": copies,
        "reasons": reasons,
        "arms": arms,
        "caveat": CAVEAT,
    }
