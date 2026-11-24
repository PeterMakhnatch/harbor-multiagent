import hashlib
import io
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench import attention, attention_details

JOB_A = "f1d2c3b4-a596-4877-8a9b-0c1d2e3f4a5b"
JOB_B = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
JOB_C = "7c6b5a49-3827-4165-9d4c-3b2a1f0e9d8c"
TRIALS = {
    JOB_A: "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f901",
    JOB_B: "9e8d7c6b-5a49-4382-a1b0-c9d8e7f6a5b4",
    JOB_C: "4d5e6f70-8192-4a3b-8c4d-5e6f708192a3",
}
SENTINEL = "sentinel-prompt-text-7f3e"
FINISHED = "2026-11-24T06:30:00+00:00"
REPORT = "seatlab.workbench.attention/v2"
PROBES = ("lstat", "stat", "open", "scandir", "listdir")
Json = dict[str, Any]


@pytest.fixture(autouse=True)
def foreign_cwd(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))


@pytest.fixture
def analyzed(monkeypatch) -> tuple[list[tuple[str, Json]], dict[str, dict[str, int]]]:
    calls: list[tuple[str, Json]] = []
    planted: dict[str, dict[str, int]] = {}

    def analyze(job: Path, packet: Json, *, seats, handoffs, accounts, witness):
        assert packet["job_closed"] is True
        inputs = {"seats": seats, "handoffs": handoffs, "accounts": accounts}
        calls.append((packet["scope"], inputs | {"witness": witness, "job": job}))
        return dict(planted.get(packet["scope"], {}))

    monkeypatch.setattr(attention_details, "analyze", analyze)
    return calls, planted


def write_json(path: Path, record: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def native_job(root: Path, name: str, job_id: str, state: str) -> Path:
    job = root / name
    envelope: Json = {"id": job_id, "n_total_trials": 1}
    if state == "closed":
        envelope |= {"finished_at": FINISHED, "stats": {"n_completed_trials": 1}}
    if state == "open":
        envelope |= {"finished_at": None, "stats": {"n_completed_trials": 0}}
    write_json(job / "result.json", envelope)
    slot = f"dispatch__{job_id[:7]}"
    config = {
        "job_id": job_id,
        "trial_name": slot,
        "instruction": SENTINEL,
        "participants": [{"seat": "reporter"}, {"seat": "booker"}],
    }
    trial = {
        "id": TRIALS[job_id],
        "trial_name": slot,
        "config": config,
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 1}},
    }
    write_json(job / slot / "result.json", trial)
    return job


def write_spool(root: Path, rows: list[Json], **extra: Any) -> Path:
    path = root / "spool" / "morning.json"
    write_json(path, {"schema": attention.SPOOL, "jobs": rows} | extra)
    return path


def run(spool: Path, capsys, *flags: str) -> tuple[int, str, str]:
    try:
        code = attention.main([str(spool), *flags])
    except SystemExit as stopped:
        assert isinstance(stopped.code, int)
        code = stopped.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def enroll(spool: Path) -> str:
    with spool.open("rb") as manifest:
        return "sha256:" + hashlib.file_digest(manifest, "sha256").hexdigest()


def under(path: object, root: Path) -> bool:
    if not isinstance(path, (str, bytes, Path)):
        return False
    name = os.path.normpath(os.path.abspath(os.fsdecode(path)))
    return os.path.commonpath([name, str(root)]) == str(root)


def watch(
    monkeypatch, root: Path, hook: Callable[[], object] | None = None
) -> list[str]:
    touched: list[str] = []

    def spy(real: Callable[..., Any]) -> Callable[..., Any]:
        def probe(*args: Any, **kwargs: Any) -> Any:
            path = args[0] if args else kwargs.get("path", kwargs.get("file"))
            if not under(path, root):
                return real(*args, **kwargs)
            touched.append(os.fsdecode(path))
            if hook is None or len(touched) > 1:
                return real(*args, **kwargs)
            try:
                return real(*args, **kwargs)
            finally:
                hook()

        return probe

    for name in PROBES:
        monkeypatch.setattr(os, name, spy(getattr(os, name)))
    monkeypatch.setattr(io, "open", spy(io.open))
    return touched


def absent(row: int, *requested: str) -> Json:
    checks = {"native_result": "not_run"}
    for capability in attention.CAPABILITIES:
        checks[capability] = "not_run" if capability in requested else "not_requested"
    return {
        "row": row,
        "job": None,
        "status": "absent",
        "registered": None,
        "retained": None,
        "retries": None,
        "checks": checks,
        "findings": {},
        "outcome": "not_analyzed",
    }


def test_closed_clear_row_reports_requested_capabilities(tmp_path, capsys, analyzed):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "seats": "../streams/a"}])

    code, out, err = run(spool, capsys, "--json")

    assert (code, err) == (0, "")
    report = json.loads(out)
    assert report["schema"] == attention.SCHEMA == REPORT
    assert report["status"] == "clear"
    assert report["spool_sha256"] == enroll(spool)
    (row,) = report["jobs"]
    assert row == {
        "row": 1,
        "job": JOB_A,
        "status": "closed",
        "registered": 1,
        "retained": 1,
        "retries": None,
        "checks": {
            "native_result": "requested",
            "seats": "requested",
            "handoffs": "not_requested",
            "accounts": "not_requested",
            "witness": "not_requested",
        },
        "findings": {},
        "outcome": "clear",
    }
    calls, _ = analyzed
    ((scope, inputs),) = calls
    assert scope == JOB_A
    assert inputs["job"] == Path(tmp_path / "spool" / "../jobs/a")
    assert inputs["seats"] == Path(tmp_path / "spool" / "../streams/a")
    assert inputs["handoffs"] is inputs["accounts"] is inputs["witness"] is None


def test_load_spool_returns_entries_and_digest_of_parsed_bytes(tmp_path, analyzed):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])
    pin = enroll(spool)

    entries, digest = attention.load_spool(str(spool), None)

    assert digest == pin
    assert attention.load_spool(str(spool), pin) == (entries, digest)
    assert [entry["job"] for entry in entries] == [
        str(tmp_path / "spool" / "../jobs/a")
    ]
    with pytest.raises(attention.Malformed):
        attention.load_spool(str(spool), "sha256:" + "0" * 64)


def test_absolute_and_relative_rows_agree_without_cwd(tmp_path, capsys, analyzed):
    job = native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "closed")
    spool = write_spool(tmp_path, [{"job": str(job)}, {"job": "../jobs/b"}])

    code, out, _ = run(spool, capsys, "--json")

    assert code == 0
    rows = json.loads(out)["jobs"]
    assert [(row["job"], row["row"]) for row in rows] == [(JOB_B, 2), (JOB_A, 1)]


def test_absent_rows_mixed_with_closed_finding(tmp_path, capsys, analyzed):
    calls, planted = analyzed
    action = attention_details.ACTIONS[0]
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "closed")
    unfinished = native_job(tmp_path, "jobs/c", JOB_C, "closed")
    (unfinished / "result.json").unlink()
    rows = [
        {"job": f"../jobs/{SENTINEL}", "seats": "s"},
        {"job": "../jobs/a"},
        {"job": "../jobs/c", "accounts": "c"},
        {"job": "../jobs/b"},
    ]
    spool = write_spool(tmp_path, rows)
    planted[JOB_A] = {action: 2}

    code, out, err = run(spool, capsys, "--json")

    assert (code, err) == (1, "")
    report = json.loads(out)
    assert report["status"] == "needs_attention"
    assert report["counts"] == {
        "jobs": 4,
        "needs_attention": 1,
        "closed": 2,
        "open": 0,
        "unknown": 0,
        "absent": 2,
    }
    assert report["actions"] == {action: 2}
    assert [(row["row"], row["job"], row["status"]) for row in report["jobs"]] == [
        (4, JOB_B, "closed"),
        (2, JOB_A, "closed"),
        (1, None, "absent"),
        (3, None, "absent"),
    ]
    assert report["jobs"][2:] == [absent(1, "seats"), absent(3, "accounts")]
    assert sorted(scope for scope, _ in calls) == sorted((JOB_A, JOB_B))

    planted.clear()
    code, out, _ = run(spool, capsys, "--json")
    assert code == 3
    assert json.loads(out)["status"] == "outstanding"
    text = run(spool, capsys)[1]
    lines = text.splitlines()
    assert ", 2 absent" in lines[0]
    assert lines[1].startswith(f"Spool {enroll(spool)}, unpinned")
    assert lines[2].startswith(f"row 4 {JOB_B} closed, no findings")
    assert lines[3].startswith(f"row 2 {JOB_A} closed, no findings")
    assert lines[4].startswith("row 1 absent: no job result; not analyzed")
    assert lines[5].startswith("row 3 absent: no job result; not analyzed")
    assert str(tmp_path) not in text and SENTINEL not in text


def test_absent_and_open_without_finding_is_outstanding(tmp_path, capsys, analyzed):
    native_job(tmp_path, "jobs/b", JOB_B, "open")
    spool = write_spool(tmp_path, [{"job": "../jobs/b"}, {"job": "../jobs/gone"}])

    code, out, _ = run(spool, capsys, "--json")

    assert code == 3
    report = json.loads(out)
    assert report["status"] == "outstanding"
    assert [(row["row"], row["status"]) for row in report["jobs"]] == [
        (1, "open"),
        (2, "absent"),
    ]
    assert analyzed[0] == []


def break_job_link(job: Path) -> None:
    shutil.rmtree(job)
    job.symlink_to(job.parent / "moved-away", target_is_directory=True)


def break_result_link(job: Path) -> None:
    (job / "result.json").unlink()
    (job / "result.json").symlink_to(job / "moved-away.json")


def invalid_result(job: Path) -> None:
    (job / "result.json").write_text('{"id": "' + JOB_A[:8], encoding="utf-8")


def file_instead_of_job(job: Path) -> None:
    shutil.rmtree(job)
    job.write_text("{}", encoding="utf-8")


@pytest.mark.parametrize(
    "damage", [break_job_link, break_result_link, invalid_result, file_instead_of_job]
)
def test_damaged_job_is_rejected_not_absent(tmp_path, capsys, analyzed, damage):
    damage(native_job(tmp_path, "jobs/a", JOB_A, "closed"))
    spool = write_spool(tmp_path, [{"job": "../jobs/gone"}, {"job": "../jobs/a"}])

    code, out, err = run(spool, capsys, "--json")

    assert (code, out) == (2, "")
    assert err.startswith("Attention input rejected: spool row")
    assert analyzed[0] == []


def test_job_appearing_during_read_is_rejected_not_absent(
    tmp_path, capsys, analyzed, monkeypatch
):
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])
    touched = watch(
        monkeypatch,
        tmp_path / "jobs",
        lambda: native_job(tmp_path, "jobs/a", JOB_A, "closed"),
    )

    code, out, err = run(spool, capsys, "--json")

    assert touched
    assert (tmp_path / "jobs" / "a" / "result.json").is_file()
    assert (code, out) == (2, "")
    assert err.startswith("Attention input rejected: spool row")


def test_pin_blocks_shortened_or_reordered_manifest_before_any_job_read(
    tmp_path, capsys, analyzed, monkeypatch
):
    calls, planted = analyzed
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "closed")
    rows = [{"job": "../jobs/a"}, {"job": "../jobs/b"}]
    spool = write_spool(tmp_path, rows)
    planted[JOB_B] = {attention_details.ACTIONS[0]: 1}

    code, out, _ = run(spool, capsys, "--json")
    assert code == 1
    pin = json.loads(out)["spool_sha256"]
    assert pin == enroll(spool)
    pinned = run(spool, capsys, "--json", "--expected-spool", pin)
    assert pinned == (1, out, "")
    text = run(spool, capsys, f"--expected-spool={pin}")[1]
    assert text.splitlines()[1].startswith(f"Spool {pin}, pinned.")

    write_spool(tmp_path, rows[:1])
    code, out, _ = run(spool, capsys, "--json")
    assert code == 0
    report = json.loads(out)
    assert report["status"] == "clear"
    assert report["counts"]["jobs"] == 1
    assert [row["row"] for row in report["jobs"]] == [1]
    assert report["spool_sha256"] != pin
    text = run(spool, capsys)[1]
    assert text.splitlines()[1].startswith(f"Spool {report['spool_sha256']}, unpinned")
    assert JOB_B not in text

    calls.clear()
    touched = watch(monkeypatch, tmp_path / "jobs")
    for shown in (rows[:1], rows[::-1]):
        write_spool(tmp_path, shown)
        for flags in (
            ("--json", "--expected-spool", pin),
            (f"--expected-spool={pin}",),
        ):
            code, out, err = run(spool, capsys, *flags)
            assert (code, out) == (2, "")
            assert (
                err
                == "Attention input rejected: spool does not match --expected-spool.\n"
            )
    assert touched == []
    assert calls == []


def test_digest_is_a_byte_commitment_not_a_signature(tmp_path, capsys, analyzed):
    _, planted = analyzed
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "closed")
    rows = [{"job": "../jobs/a"}, {"job": "../jobs/b"}]
    spool = write_spool(tmp_path, rows)
    pin = enroll(spool)
    planted[JOB_B] = {attention_details.ACTIONS[0]: 1}

    spool.write_text(
        json.dumps({"schema": attention.SPOOL, "jobs": rows}), encoding="utf-8"
    )
    assert run(spool, capsys, "--expected-spool", pin)[:2] == (2, "")

    write_spool(tmp_path, rows[:1])
    rewritten = enroll(spool)
    code, out, _ = run(spool, capsys, "--json", "--expected-spool", rewritten)
    assert code == 0
    assert json.loads(out)["spool_sha256"] == rewritten != pin


@pytest.mark.parametrize(
    "expected",
    [
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "a" * 64,
        "sha512:" + "a" * 64,
        "sha256:" + "g" * 64,
        "",
    ],
)
def test_malformed_pin_rejected_before_any_job_read(
    tmp_path, capsys, analyzed, monkeypatch, expected
):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])
    touched = watch(monkeypatch, tmp_path / "jobs")

    code, out, err = run(spool, capsys, "--expected-spool", expected)

    assert (code, out) == (2, "")
    assert err == (
        "Attention input rejected: spool digest is not sha256:<64 lowercase hex>.\n"
    )
    assert touched == []
    assert analyzed[0] == []


def test_precedence_attention_over_outstanding(tmp_path, capsys, analyzed):
    calls, planted = analyzed
    action = attention_details.ACTIONS[0]
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "open")
    native_job(tmp_path, "jobs/c", JOB_C, "unknown")
    rows = [{"job": f"../jobs/{name}", "seats": "s", "witness": "w"} for name in "abc"]
    spool = write_spool(tmp_path, rows)
    planted[JOB_A] = {action: 2}

    code, out, _ = run(spool, capsys, "--json")

    assert code == 1
    report = json.loads(out)
    assert report["status"] == "needs_attention"
    assert report["counts"] == {
        "jobs": 3,
        "needs_attention": 1,
        "closed": 1,
        "open": 1,
        "unknown": 1,
        "absent": 0,
    }
    assert report["actions"] == {action: 2}
    assert [row["job"] for row in report["jobs"]] == sorted([JOB_A, JOB_B, JOB_C])
    states = {
        row["job"]: (row["row"], row["status"], row["outcome"])
        for row in report["jobs"]
    }
    assert states == {
        JOB_A: (1, "closed", "needs_attention"),
        JOB_B: (2, "open", "not_analyzed"),
        JOB_C: (3, "unknown", "not_analyzed"),
    }
    open_row = next(row for row in report["jobs"] if row["job"] == JOB_B)
    assert open_row["checks"] == {
        "native_result": "not_run",
        "seats": "not_run",
        "handoffs": "not_requested",
        "accounts": "not_requested",
        "witness": "not_run",
    }
    assert [scope for scope, _ in calls] == [JOB_A]

    planted.clear()
    assert run(spool, capsys)[0] == 3


@pytest.mark.parametrize("state", ["open", "unknown"])
def test_open_or_unknown_alone_is_outstanding(tmp_path, capsys, analyzed, state):
    native_job(tmp_path, "jobs/a", JOB_A, state)
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])

    code, out, _ = run(spool, capsys, "--json")

    assert code == 3
    report = json.loads(out)
    assert report["status"] == "outstanding"
    (row,) = report["jobs"]
    assert (row["registered"], row["retained"], row["outcome"]) == (
        1,
        None,
        "not_analyzed",
    )
    assert analyzed[0] == []


def partial_child(job: Path, job_id: str) -> Path:
    child = job / f"dispatch__{job_id[:7]}" / "result.json"
    child.write_text('{"id": "' + TRIALS[job_id][:8], encoding="utf-8")
    (job / "dispatch__pending").mkdir()
    return child


@pytest.mark.parametrize("state", ["open", "unknown"])
def test_open_root_skips_partial_trial_directories(
    tmp_path, capsys, analyzed, monkeypatch, state
):
    job = native_job(tmp_path, "jobs/a", JOB_A, state)
    partial_child(job, JOB_A)
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "seats": "s"}])

    def untouched(*args: object) -> None:
        raise AssertionError("open job trial directories were inventoried")

    monkeypatch.setattr(attention, "collect", untouched)
    code, out, err = run(spool, capsys)

    assert (code, err) == (3, "")
    assert (
        f"row 1 {JOB_A} {state}: not analyzed; job result only, trial directories not inventoried"
        in out
    )
    assert "not run: native_result, seats" in out
    assert analyzed[0] == []


def test_closed_root_with_partial_trial_is_rejected(tmp_path, capsys, analyzed):
    job = native_job(tmp_path, "jobs/a", JOB_A, "closed")
    partial_child(job, JOB_A)
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])

    code, out, err = run(spool, capsys, "--json")

    assert (code, out) == (2, "")
    assert (
        err
        == "Attention input rejected: spool row 0 trial result is not strict UTF-8 JSON.\n"
    )
    assert analyzed[0] == []


def test_duplicate_native_job_under_other_spelling(tmp_path, capsys, analyzed):
    job = native_job(tmp_path, "jobs/a", JOB_A, "closed")
    shutil.copytree(job, tmp_path / "copies" / "a")
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}, {"job": "../copies/a"}])

    code, out, err = run(spool, capsys)

    assert (code, out) == (2, "")
    assert "spool row 1 names a native job already in the spool" in err


def test_duplicate_job_path_spelling(tmp_path, capsys, analyzed):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    absolute = str(tmp_path / "jobs" / "a")
    spool = write_spool(tmp_path, [{"job": "../jobs/./a"}, {"job": absolute}])

    code, out, err = run(spool, capsys)

    assert (code, out) == (2, "")
    assert "spool row 1 repeats a job path" in err
    assert analyzed[0] == []


@pytest.mark.parametrize(
    "document",
    [
        {"schema": "seatlab.workbench.spool/v2", "jobs": [{"job": "a"}]},
        {"schema": attention.SPOOL, "jobs": [{"job": "a"}], "label": "x"},
        {"schema": attention.SPOOL, "jobs": []},
        {"schema": attention.SPOOL, "jobs": [{"job": f"j{n}"} for n in range(33)]},
        {"schema": attention.SPOOL, "jobs": [{"job": "a", "prompt": "x"}]},
        {"schema": attention.SPOOL, "jobs": [{"seats": "s"}]},
        {"schema": attention.SPOOL, "jobs": [{"job": "a", "witness": "w"}]},
        {"schema": attention.SPOOL, "jobs": [{"job": "a", "seats": None}]},
        {"schema": attention.SPOOL, "jobs": [{"job": ""}]},
        {"schema": attention.SPOOL, "jobs": [{"job": "a\u0000b"}]},
        {"schema": attention.SPOOL, "jobs": [{"job": "a" * 4097}]},
        {"schema": attention.SPOOL, "jobs": [{"job": 7}]},
        {"schema": attention.SPOOL, "jobs": {"job": "a"}},
    ],
)
def test_malformed_spool_rejected(tmp_path, capsys, analyzed, document):
    path = tmp_path / "spool.json"
    write_json(path, document)

    code, out, err = run(path, capsys)

    assert (code, out) == (2, "")
    assert err.startswith("Attention input rejected: spool")
    assert analyzed[0] == []


def test_duplicate_json_name_and_oversize_rejected(tmp_path, capsys, analyzed):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema":"seatlab.workbench.spool/v1","jobs":[{"job":"a","job":"b"}]}',
        encoding="utf-8",
    )
    oversize = tmp_path / "oversize.json"
    oversize.write_bytes(b" " * (attention.MAX_SPOOL_BYTES + 1))

    assert run(duplicate, capsys)[:2] == (2, "")
    assert run(oversize, capsys)[:2] == (2, "")


def test_moving_job_result_rejected(tmp_path, capsys, monkeypatch):
    job = native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])

    def analyze(path: Path, packet: Json, **inputs: Path | None) -> dict[str, int]:
        envelope = json.loads((path / "result.json").read_text(encoding="utf-8"))
        envelope["stats"]["n_retries"] = 1
        write_json(path / "result.json", envelope)
        return {}

    monkeypatch.setattr(attention_details, "analyze", analyze)
    code, out, err = run(spool, capsys, "--json")

    assert (code, out) == (2, "")
    assert "spool row 0 job result changed during inspection" in err
    assert job.is_dir()


@pytest.mark.parametrize(
    "returned",
    [{"not_a_known_action": 1}, {"__zero__": 0}, {"__bool__": True}, [("x", 1)]],
)
def test_analysis_contract_enforced(tmp_path, capsys, monkeypatch, returned):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}])
    action = attention_details.ACTIONS[0]
    if isinstance(returned, dict):
        returned = {
            (action if key.startswith("__") else key): value
            for key, value in returned.items()
        }
    monkeypatch.setattr(attention_details, "analyze", lambda *a, **k: returned)

    code, out, _ = run(spool, capsys)

    assert (code, out) == (2, "")


def test_outputs_hold_no_paths_prompts_or_labels(tmp_path, capsys, analyzed):
    _, planted = analyzed
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "open")
    spool = write_spool(
        tmp_path,
        [
            {"job": "../jobs/a", "seats": "s", "handoffs": "h", "accounts": "c"},
            {"job": str(tmp_path / "jobs" / "b"), "accounts": "c"},
            {"job": "../jobs/worker-c-private-name"},
        ],
    )
    planted[JOB_A] = {attention_details.ACTIONS[-1]: 1}
    pin = enroll(spool)

    for flags in ((), ("--json",), ("--json", "--expected-spool", pin)):
        code, out, err = run(spool, capsys, *flags)
        assert (code, err) == (1, "")
        assert str(tmp_path) not in out
        assert SENTINEL not in out
        assert "dispatch" not in out and "reporter" not in out
        assert "worker-c" not in out and "morning" not in out
    text = run(spool, capsys)[1]
    assert f"row 1 {JOB_A} closed, needs attention" in text
    assert "not requested: witness" in text
    assert f"row 2 {JOB_B} open: not analyzed" in text
    assert "not run: native_result, accounts" in text
    assert "row 3 absent: no job result; not analyzed" in text
