import json
import shutil
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
    code = attention.main([str(spool), *flags])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_closed_clear_row_reports_requested_capabilities(tmp_path, capsys, analyzed):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "seats": "../streams/a"}])

    code, out, err = run(spool, capsys, "--json")

    assert (code, err) == (0, "")
    report = json.loads(out)
    assert report["status"] == "clear"
    (row,) = report["jobs"]
    assert row == {
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


def test_absolute_and_relative_rows_agree_without_cwd(tmp_path, capsys, analyzed):
    job = native_job(tmp_path, "jobs/a", JOB_A, "closed")
    native_job(tmp_path, "jobs/b", JOB_B, "closed")
    spool = write_spool(tmp_path, [{"job": str(job)}, {"job": "../jobs/b"}])

    code, out, _ = run(spool, capsys, "--json")

    assert code == 0
    assert [row["job"] for row in json.loads(out)["jobs"]] == sorted([JOB_A, JOB_B])


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
    }
    assert report["actions"] == {action: 2}
    assert [row["job"] for row in report["jobs"]] == sorted([JOB_A, JOB_B, JOB_C])
    states = {row["job"]: (row["status"], row["outcome"]) for row in report["jobs"]}
    assert states == {
        JOB_A: ("closed", "needs_attention"),
        JOB_B: ("open", "not_analyzed"),
        JOB_C: ("unknown", "not_analyzed"),
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
        f"{JOB_A} {state}: not analyzed; job result only, trial directories not inventoried"
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


def test_help_states_exit_precedence(capsys):
    with pytest.raises(SystemExit) as stopped:
        attention.main(["--help"])

    assert stopped.value.code == 0
    shown = " ".join(capsys.readouterr().out.split())
    assert "Exit 0: every job is closed without findings." in shown
    assert (
        "Exit 1: at least one closed job needs attention, whatever the other jobs are."
        in shown
    )
    assert "Exit 2: input rejected or changed during inspection; no report." in shown
    assert "but at least one job is open or unknown; unknown is never 0." in shown


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


def test_missing_job_rejected_without_partial_report(tmp_path, capsys, analyzed):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    spool = write_spool(tmp_path, [{"job": "../jobs/a"}, {"job": "../jobs/gone"}])

    code, out, err = run(spool, capsys, "--json")

    assert (code, out) == (2, "")
    assert err.startswith("Attention input rejected: spool row 1 job")


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
        ],
    )
    planted[JOB_A] = {attention_details.ACTIONS[-1]: 1}

    for flags in ((), ("--json",)):
        code, out, err = run(spool, capsys, *flags)
        assert (code, err) == (1, "")
        assert str(tmp_path) not in out
        assert SENTINEL not in out
        assert "dispatch" not in out and "reporter" not in out
    text = run(spool, capsys)[1]
    assert f"{JOB_A} closed, needs attention" in text
    assert "not requested: witness" in text
    assert f"{JOB_B} open: not analyzed" in text
    assert "not run: native_result, accounts" in text
