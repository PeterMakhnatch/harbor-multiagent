import json
import shutil
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from harbor.models.job.config import RetryConfig
from harbor.models.job.lock import HarborLockInfo, JobLock, TrialLock
from tests.unit.examples import test_seatlab_paired_jobs as paired
from tests.unit.examples.test_seatlab_cohort_start_contract import STARTED, tally
from tests.unit.examples.test_seatlab_input_keeper import linkat
from tests.unit.examples.test_seatlab_job_projection import (
    SETTINGS,
    STOCK,
    document,
    native_keys,
    only,
    save,
)
from tests.unit.examples.test_seatlab_paired_jobs import (
    SIDES,
    Arm,
    invoke,
    pair,
    refused,
    report,
    standard,
)

COMMITS = (
    "3f9a1c7e5b2d4f6a8c0e1b3d5f7a9c2e4b6d8f0a",
    "b8d0f2a4c6e81a3c5e7f9b1d3f5a7c9e2b4d6f8a",
)
SELECTOR = "PrivateSelectorError4e91"
SELECTORS = ("AlphaRetryError", "BetaRetryError", "GammaRetryError")
STOPS = ("DeltaStopError", "EpsilonStopError", "ZetaStopError")
WHEEL = HarborLockInfo(version="0.1.0", is_editable=False)
PRIVATE = (*COMMITS, *(c[:12] for c in COMMITS), SELECTOR, *SELECTORS, *STOPS)
REGISTRATION_FIELDS = (
    "concurrency",
    "max_retries",
    "retry_policy",
    "harbor_version",
    "editable_install",
    "harbor_commit",
)
Json = dict[str, Any]


class Header(NamedTuple):
    concurrency: int = 2
    retry: RetryConfig = RetryConfig()
    harbor: HarborLockInfo = HarborLockInfo(version="0.1.0")
    reversed_selectors: bool = False


BASE = Header()
ORDERED = RetryConfig(
    max_retries=1, include_exceptions=set(SELECTORS), exclude_exceptions=set(STOPS)
)


def enrolling(monkeypatch: pytest.MonkeyPatch, left: Header, right: Header) -> None:
    headers = {"left-arm": left, "right-arm": right}

    def enroll(job: Path, locks: list[TrialLock]) -> None:
        header = headers[job.name]
        lock = JobLock(
            created_at=STARTED,
            harbor=header.harbor,
            n_concurrent_trials=header.concurrency,
            retry=header.retry,
            trials=locks,
        )
        saved = json.loads(lock.model_dump_json(exclude_none=True))
        assert saved["schema_version"] == 3
        if header.reversed_selectors:
            for field in ("include_exceptions", "exclude_exceptions"):
                assert len(saved["retry"][field]) > 1
                saved["retry"][field].reverse()
        (job / "lock.json").write_text(json.dumps(saved, indent=4))

    monkeypatch.setattr(paired, "enroll", enroll)


async def jobs(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    left: Header = BASE,
    right: Header = BASE,
    copies: int = 2,
) -> tuple[Arm, Arm]:
    assert 1 <= copies <= 4
    enrolling(monkeypatch, left, right)
    built = await pair(
        root,
        left_rewards=(0,) * copies,
        right_rewards=(1,) + (0,) * (copies - 1),
    )
    for arm, header in zip(built, (left, right), strict=True):
        found = json.loads((arm.job / "lock.json").read_bytes())
        assert found["n_concurrent_trials"] == header.concurrency
    return built


def scheduled(
    left: Arm, right: Arm, copies: int, schedule: tuple[str, str] | None
) -> list[str]:
    argv = standard(left, right, copies=copies)
    return argv if schedule is None else [*argv, "--schedule", *schedule]


def leak_free(text: str) -> None:
    assert not [value for value in PRIVATE if value in text]


def registered(arm: Arm) -> Json:
    saved = json.loads((arm.job / "lock.json").read_bytes())
    return {
        "concurrency": saved["n_concurrent_trials"],
        "max_retries": saved["retry"]["max_retries"],
    }


def registration(found: Json, schedule: tuple[str, str] | None, *arms: Arm) -> Json:
    shown = found["registration"]
    assert shown["basis"] == "native_job_lock_claims"
    assert shown["registered"] == dict(zip(SIDES, map(registered, arms), strict=True))
    if schedule is None:
        assert (shown["schedule"], shown["expected"]) == ("not_requested", None)
    else:
        expected = {"concurrency": int(schedule[0]), "max_retries": int(schedule[1])}
        assert shown["expected"] == expected
        assert shown["schedule"] in ("matches", "differs")
    assert set(shown["fields"]) == set(REGISTRATION_FIELDS)
    assert set(shown["fields"].values()) <= {"same", "different", "unrecorded"}
    return shown


def source(found: Json) -> str:
    return found["registration"]["fields"]["harbor_commit"]


def checked(
    capsys: pytest.CaptureFixture[str],
    left: Arm,
    right: Arm,
    *,
    copies: int = 2,
    schedule: tuple[str, str] | None = None,
) -> tuple[int, Json]:
    argv = scheduled(left, right, copies, schedule)
    code, found = report(capsys, argv, left, right)
    leak_free(json.dumps(found))
    registration(found, schedule, left, right)
    assert invoke(argv) == code
    out, err = capsys.readouterr()
    assert err == "" and out.strip()
    paired.clean(out, left, right)
    leak_free(out)
    return code, found


def graded(code: int, found: Json, copies: int) -> None:
    assert (code, found["status"], found["copies_per_arm"]) == (0, "covered", copies)
    shown = found["registration"]
    assert shown["pair"] != "inconsistent"
    assert "different" not in shown["fields"].values()
    assert shown["schedule"] in ("not_requested", "matches")
    expected = {
        "left": tally(zero=copies),
        "right": tally(zero=copies - 1, one=1),
    }
    for side in SIDES:
        arm = found["arms"][side]
        assert (arm["registered"], arm["retained"], arm["attributed"]) == (
            copies,
            copies,
            copies,
        )
        assert arm["grades"] == expected[side]


def contradicted(code: int, found: Json) -> None:
    assert (code, found["status"]) == (1, "inconsistent")
    assert found["reasons"]
    for side in SIDES:
        assert found["arms"][side]["grades"] is None
        assert found["arms"][side]["attributed"] is None


@linkat
@pytest.mark.asyncio
async def test_matching_registered_schedule_covers_one_copy_per_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    single = Header(concurrency=1)
    left, right = await jobs(tmp_path, monkeypatch, single, single, copies=1)
    code, found = checked(capsys, left, right, copies=1, schedule=("1", "0"))
    graded(code, found, 1)
    code, plain = checked(capsys, left, right, copies=1)
    graded(code, plain, 1)
    assert [plain["arms"][side] for side in SIDES] == [
        found["arms"][side] for side in SIDES
    ]


@linkat
@pytest.mark.asyncio
async def test_clean_default_jobs_stay_covered_with_and_without_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    left, right = await jobs(tmp_path, monkeypatch)
    code, found = checked(capsys, left, right)
    graded(code, found, 2)
    code, found = checked(capsys, left, right, schedule=("2", "0"))
    graded(code, found, 2)
    assert (
        paired.workbench(
            ["cohort-jobs", *scheduled(left, right, 2, ("2", "0")), "--json"]
        )
        == 0
    )
    out, err = capsys.readouterr()
    assert (err, json.loads(out)) == ("", found)


def _concurrency() -> tuple[Header, Header]:
    return BASE, Header(concurrency=3)


def _wait_multiplier() -> tuple[Header, Header]:
    return (
        Header(retry=RetryConfig(max_retries=1)),
        Header(retry=RetryConfig(max_retries=1, wait_multiplier=2.0)),
    )


def _custom_selector() -> tuple[Header, Header]:
    return (
        Header(retry=RetryConfig(max_retries=1)),
        Header(retry=RetryConfig(max_retries=1, include_exceptions={SELECTOR})),
    )


def _known_commits() -> tuple[Header, Header]:
    left, right = (
        Header(harbor=WHEEL.model_copy(update={"git_commit_hash": commit}))
        for commit in COMMITS
    )
    return left, right


DRIFTS = {
    "asymmetric_concurrency": (_concurrency, "concurrency"),
    "retry_wait_multiplier": (_wait_multiplier, "retry_policy"),
    "retry_custom_selector": (_custom_selector, "retry_policy"),
    "harbor_commit": (_known_commits, "harbor_commit"),
}


@linkat
@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(DRIFTS))
async def test_known_registration_contradiction_withholds_both_arms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    build, field = DRIFTS[case]
    left_header, right_header = build()
    left, right = await jobs(tmp_path, monkeypatch, left_header, right_header)
    code, found = checked(capsys, left, right)
    contradicted(code, found)
    shown = found["registration"]
    assert (shown["pair"], shown["fields"][field]) == ("inconsistent", "different")
    if field != "concurrency":
        assert shown["fields"]["concurrency"] == "same"
    if field == "retry_policy":
        assert shown["fields"]["max_retries"] == "same"
    code, found = checked(capsys, left, right, schedule=("2", "1"))
    contradicted(code, found)
    code, swapped = report(
        capsys,
        paired.command(
            (right.job, left.job), (right.key, left.key), (right.pin, left.pin)
        ),
        left,
        right,
    )
    contradicted(code, swapped)
    leak_free(json.dumps(swapped))


@linkat
@pytest.mark.asyncio
async def test_both_jobs_drifting_together_from_schedule_withhold_both_arms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wide = Header(concurrency=4)
    left, right = await jobs(tmp_path, monkeypatch, wide, wide)
    code, found = checked(capsys, left, right)
    graded(code, found, 2)
    for schedule in (("2", "0"), ("4", "1")):
        code, found = checked(capsys, left, right, schedule=schedule)
        contradicted(code, found)
        shown = found["registration"]
        assert shown["pair"] != "inconsistent" and shown["schedule"] == "differs"
    code, found = checked(capsys, left, right, schedule=("4", "0"))
    graded(code, found, 2)
    assert found["registration"]["schedule"] == "matches"


@linkat
@pytest.mark.asyncio
async def test_wheels_without_a_commit_keep_coverage_without_a_source_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wheel = Header(harbor=WHEEL)
    left, right = await jobs(tmp_path, monkeypatch, wheel, wheel)
    code, found = checked(capsys, left, right, schedule=("2", "0"))
    graded(code, found, 2)
    assert source(found) == "unrecorded"
    assert found["registration"]["pair"] == "partly_recorded"
    assert found["registration"]["schedule"] == "matches"
    one_known = Header(harbor=WHEEL.model_copy(update={"git_commit_hash": COMMITS[0]}))
    known, unknown = await jobs(tmp_path / "one-known", monkeypatch, one_known, wheel)
    code, found = checked(capsys, known, unknown)
    graded(code, found, 2)
    assert source(found) == "unrecorded"
    assert found["registration"]["pair"] == "partly_recorded"
    same = await jobs(tmp_path / "same", monkeypatch, one_known, one_known)
    code, found = checked(capsys, *same)
    graded(code, found, 2)
    assert source(found) == "same"
    assert found["registration"]["pair"] == "consistent"


@linkat
@pytest.mark.asyncio
async def test_exception_selector_order_is_not_a_retry_difference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    left, right = await jobs(
        tmp_path,
        monkeypatch,
        Header(retry=ORDERED),
        Header(retry=ORDERED, reversed_selectors=True),
    )
    sides = [
        json.loads((arm.job / "lock.json").read_bytes())["retry"]
        for arm in (left, right)
    ]
    for field in ("include_exceptions", "exclude_exceptions"):
        assert sides[0][field] != sides[1][field]
        assert sorted(sides[0][field]) == sorted(sides[1][field])
    code, found = checked(capsys, left, right)
    graded(code, found, 2)
    code, found = checked(capsys, left, right, schedule=("2", "1"))
    graded(code, found, 2)


MALFORMED = (
    ("--schedule", "1"),
    ("--schedule", "0", "0"),
    ("--schedule", "-1", "0"),
    ("--schedule", "1", "-1"),
    ("--schedule", "01", "0"),
    ("--schedule", "+1", "0"),
    ("--schedule", "1.0", "0"),
    ("--schedule", "1", "x"),
    ("--schedule", "\u0661", "0"),
    ("--schedule", "9" * 40, "0"),
    ("--schedule", "1025", "0"),
    ("--schedule", "1", "17"),
    ("--schedule", "2", "0", "--schedule", "2", "0"),
)


@linkat
@pytest.mark.asyncio
async def test_malformed_or_repeated_schedule_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    left, right = await jobs(tmp_path, monkeypatch)
    good = standard(left, right)
    for extra in MALFORMED:
        refused(capsys, [*good, *extra, "--json"], left, right)
    code, found = checked(capsys, left, right, schedule=("2", "0"))
    graded(code, found, 2)


@pytest.mark.unit
def test_prospective_plans_refuse_a_schedule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    task = root / "tasks" / "hello-seats"
    shutil.copytree(STOCK, task, symlinks=True)
    documents = (
        document(task, "hello-left"),
        document(task, "hello-right", n_concurrent_trials=3, **SETTINGS["channel"]),
    )
    configs = (
        save(root / "left.json", documents[0]),
        save(root / "right.json", documents[1]),
    )
    keys = tuple(only(native_keys(value), 2) for value in documents)
    argv = [*map(str, configs), "--plans", "--pair", *keys, "--copies", "2"]
    assert invoke([*argv, "--json"]) in (0, 1)
    out, err = capsys.readouterr()
    assert err == "" and json.loads(out)["status"]
    for schedule in (("2", "0"), ("3", "0")):
        assert invoke([*argv, "--schedule", *schedule, "--json"]) == 2
        out, err = capsys.readouterr()
        assert out == "" and err.strip()
        assert str(root) not in err
    assert not (root / "jobs").exists()
