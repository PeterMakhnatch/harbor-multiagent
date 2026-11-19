"""Run and collect registered, private five-seat Harbor cursor trials."""

import argparse
import os
import re
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template
from uuid import uuid4

import study
from cases import DATASET_COMMIT, load_case

ADAPTER = Path(__file__).resolve().parent
MODE = "cursor"
POLICY = "cursor-budget-8"
ROUND_LIMIT = 8
PLAN_SCHEMA = "seatlab.calendar-interactive-plan/v1"
ATTEMPT_SCHEMA = "seatlab.calendar-interactive-attempt/v1"
RESULT_SCHEMA = "seatlab.calendar-interactive-result/v1"
REPLACEMENTS = {
    "interactive_world.py": "environment/calendar_world.py",
    "interactive_client.py": "solution/client.py",
    "interactive_verify.py": "tests/verify.py",
}
BASE_FILES = (
    "task.toml",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/calendar_world.py",
    "environment/player/Dockerfile",
    "solution/solve.sh",
    "solution/client.py",
    "tests/test.sh",
    "tests/verify.py",
)
INSTRUCTIONS = tuple(f"instructions/{seat}.md" for seat in study.SEATS)
TASK_FILES = {
    *BASE_FILES,
    *INSTRUCTIONS,
    "environment/calendar_base.py",
    "environment/case.json",
}
REVIEWED_FILES = (
    *REPLACEMENTS,
    "interactive_instructions.md",
    *(f"template/{name}" for name in BASE_FILES),
)
TASK_SOURCE_FILES = {
    **{target: source for source, target in REPLACEMENTS.items()},
    "environment/calendar_base.py": "template/environment/calendar_world.py",
    **{
        name: f"template/{name}"
        for name in (
            "environment/docker-compose.yaml",
            "environment/player/Dockerfile",
            "solution/solve.sh",
            "tests/test.sh",
        )
    },
}
NATIVE_FILES = {
    **study.NATIVE_FILES,
    "world": "artifacts/logs/artifacts/world.calendar.json",
}
STATUSES = ("unattempted", "incomplete", "failed", "invalid", "verified")
EVIDENCE_ERRORS = (
    AttributeError,
    KeyError,
    OSError,
    RecursionError,
    TypeError,
    ValueError,
)


def _checked_path(root: Path, relative: str) -> Path:
    """Reject symlinks in any receipt or task path below the trusted root."""
    parts = Path(relative).parts
    path = root
    for index, part in enumerate(parts):
        path = path / part
        mode = path.lstat().st_mode
        study._require(not stat.S_ISLNK(mode), "symlink in evidence or task path")
        if index < len(parts) - 1:
            study._require(stat.S_ISDIR(mode), "expected a directory")
    return path


def _read_file(root: Path, relative: str) -> bytes:
    path = _checked_path(root, relative)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        study._require(
            stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "expected a regular file"
        )
        return stream.read()


def _digests(files):
    return {name: study._sha(data) for name, data in files.items()}


def _valid_digests(value, names):
    return (
        isinstance(value, dict)
        and value.keys() == set(names)
        and all(
            isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
            for digest in value.values()
        )
    )


def _task_payloads(case, reviewed):
    payloads = {name: reviewed[f"template/{name}"] for name in BASE_FILES}
    payloads["environment/calendar_base.py"] = payloads["environment/calendar_world.py"]
    payloads.update({dest: reviewed[src] for src, dest in REPLACEMENTS.items()})
    dockerfile = "environment/Dockerfile"
    copy_base = b"COPY calendar_world.py case.json ./\n"
    old_artifact = b'artifacts = ["/logs/world/calendar.json"]\n'
    study._require(
        payloads[dockerfile].count(copy_base) == 1
        and payloads["task.toml"].count(old_artifact) == 1,
        "unexpected base task layout",
    )
    payloads[dockerfile] = payloads[dockerfile].replace(
        copy_base, b"COPY calendar_world.py calendar_base.py case.json ./\n"
    )
    # Harbor already collects the default /logs/artifacts directory from main.
    payloads["task.toml"] = payloads["task.toml"].replace(old_artifact, b"")
    payloads["environment/case.json"] = study._canonical({"mode": MODE, "case": case})
    instructions = Template(reviewed["interactive_instructions.md"].decode("utf-8"))
    for seat, path in zip(study.SEATS, INSTRUCTIONS, strict=True):
        payloads[path] = instructions.substitute(role=seat).encode("utf-8")
    return payloads


def _task_hashes(root):
    study._require(stat.S_ISDIR(root.lstat().st_mode), "expected a task directory")
    found = {}
    directories = {Path(name).parent for name in TASK_FILES} - {Path(".")}
    pending = [Path(".")]
    while pending:
        directory = pending.pop()
        for path in (root / directory).iterdir():
            relative = path.relative_to(root)
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                study._require(relative in directories, "unexpected task directory")
                pending.append(relative)
            else:
                name = relative.as_posix()
                study._require(name in TASK_FILES, "unexpected task file")
                permissions = 0o755 if name.endswith(".sh") else 0o644
                study._require(
                    stat.S_IMODE(mode) == permissions, "unexpected task file mode"
                )
                found[name] = study._sha(_read_file(root, name))
    return found


def _materialize(dataset, case, task, payloads):
    from materialize import materialize

    materialize(dataset, case["case_id"], "full", task)
    case_file = "environment/case.json"
    study._require(stat.S_ISDIR(task.lstat().st_mode), "expected a task directory")
    study._require(
        _read_file(task, case_file) == study._canonical({"mode": "full", "case": case}),
        "materialized case differs from registered source",
    )
    for name in (
        "environment/calendar_base.py",
        *REPLACEMENTS.values(),
        *INSTRUCTIONS,
        "environment/Dockerfile",
        "task.toml",
        case_file,
    ):
        path = task / name
        if name == "environment/calendar_base.py":
            study._require(
                _read_file(task, "environment/calendar_world.py") == payloads[name],
                "base relay differs from reviewed source",
            )
            with path.open("xb") as stream:
                stream.write(payloads[name])
            path.chmod(0o644)
        else:
            _checked_path(task, name).write_bytes(payloads[name])


def _plan(study_id, source, case_ids, hashes):
    return {
        "schema": PLAN_SCHEMA,
        "study_id": study_id,
        "mode": MODE,
        "policy": POLICY,
        "round_limit": ROUND_LIMIT,
        "source_sha256": study._sha(study._canonical(source)),
        "task_sha256": hashes,
        "cells": [
            {
                "case_id": case_id,
                "mode": MODE,
                "trial_name": (
                    f"calendar-interactive-{study_id}-{case_id.replace('/', '-')}"
                ),
            }
            for case_id in case_ids
        ],
    }


def _native_files(out, native):
    files = {}
    for key, name in NATIVE_FILES.items():
        try:
            files[key] = _read_file(out, (native / name).relative_to(out).as_posix())
        except FileNotFoundError:
            continue
    return files


def _native_env():
    # Keep the local tool settings, not model credentials or provider controls.
    names = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
    )
    return {name: os.environ[name] for name in names if name in os.environ}


def _ready(env):
    endpoint = None if env.get("DOCKER_CONTEXT") else env.get("DOCKER_HOST")
    if not endpoint:
        endpoint = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            env=env,
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    study._require(
        endpoint.startswith("unix:///"), "native trials require a local Docker socket"
    )
    study._ready()


def _run_cell(
    dataset, out, harbor, executable_hash, env, cell, case, payloads, task_hashes
):
    root, native = study._paths(out, cell)
    root.mkdir(mode=0o700)
    task = root / "task"
    _materialize(dataset, case, task, payloads)
    study._require(
        _task_hashes(task) == task_hashes, "generated task differs from registration"
    )
    study._require(
        not native.exists() and not native.is_symlink(),
        "native trial directory already exists",
    )
    config_path = root / "trial-config.json"
    study._write_once(config_path, study._native_config(task, native, cell))
    study._require(
        study._sha(_read_file(harbor.parent, harbor.name)) == executable_hash,
        "Harbor executable differs from registration",
    )
    with (root / "harbor.log").open("xb") as log:
        process = subprocess.run(
            [str(harbor), "trial", "start", "--config", str(config_path)],
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    try:
        native_hashes = _digests(_native_files(out, native))
    except (OSError, ValueError):
        # Keep the attempt even if an unsafe artifact prevents sealing its bytes.
        native_hashes = None
    study._write_once(
        root / "attempt.json",
        {
            "schema": ATTEMPT_SCHEMA,
            "trial_name": cell["trial_name"],
            "returncode": process.returncode,
            "task_sha256": study._sha(study._canonical(task_hashes)),
            "native_sha256": native_hashes,
        },
    )


def _root(path):
    # Permit an OS directory alias (for example /tmp on macOS), not a linked run.
    study._require(not path.is_symlink(), "--out cannot be a symlink")
    return path.resolve()


def _stop(out):
    # Any directory entry named STOP is a stop request, including a broken link.
    return (out / "STOP").exists() or (out / "STOP").is_symlink()


def run(dataset: Path, out: Path, harbor: Path, case_spec: str):
    """Freeze source files and admit listed cases serially, once in this run."""
    out = _root(out)
    study._require(not out.exists(), "--out must be a new directory")
    study._require(harbor.is_absolute(), "--harbor must be an absolute path")
    harbor = harbor.resolve(strict=True)
    study._require(
        harbor.is_file() and os.access(harbor, os.X_OK), "--harbor must be executable"
    )
    executable_hash = study._sha(_read_file(harbor.parent, harbor.name))
    dataset = dataset.resolve(strict=True)
    case_ids = case_spec.split(",")
    available = study._source_case_ids(dataset)
    study._require(
        len(case_ids) == len(set(case_ids)) and set(case_ids) <= set(available),
        "--cases must be a comma-separated list of distinct original case IDs",
    )
    reviewed = {name: _read_file(ADAPTER, name) for name in REVIEWED_FILES}
    cases = {case_id: load_case(dataset, case_id) for case_id in case_ids}
    source = {
        "out_path": str(out),
        "dataset_path": str(dataset),
        "dataset_commit": DATASET_COMMIT,
        "policy": POLICY,
        "round_limit": ROUND_LIMIT,
        "harbor_executable_path": str(harbor),
        "harbor_executable_sha256": executable_hash,
        "reviewed_sha256": _digests(reviewed),
        "cases": cases,
    }
    payloads = {
        case_id: _task_payloads(case, reviewed) for case_id, case in cases.items()
    }
    hashes = {case_id: _digests(files) for case_id, files in payloads.items()}
    plan = _plan(uuid4().hex, source, case_ids, hashes)
    out.mkdir(mode=0o700)
    (out / "cells").mkdir(mode=0o700)
    study._write_once(out / "source.json", source)
    study._write_once(out / "plan.json", plan)
    env = _native_env()
    if not _stop(out):
        _ready(env)
    for index, cell in enumerate(plan["cells"], 1):
        if _stop(out):
            print(
                f"STOP: {len(case_ids) - index + 1} cells not attempted",
                file=sys.stderr,
            )
            break
        case_id = cell["case_id"]
        print(
            f"[{index}/{len(case_ids)}] {case_id} {MODE}", file=sys.stderr, flush=True
        )
        case = cases[case_id]
        _run_cell(
            dataset,
            out,
            harbor,
            executable_hash,
            env,
            cell,
            case,
            payloads[case_id],
            hashes[case_id],
        )
    return collect(out)


def _fresh_native(result, cell):
    study._verify_native(result, cell)
    start = datetime.fromisoformat(result["started_at"])
    finish = datetime.fromisoformat(result["finished_at"])
    verifier = result["verifier"]
    verify_start = datetime.fromisoformat(verifier["started_at"])
    verify_finish = datetime.fromisoformat(verifier["finished_at"])
    study._require(
        start <= verify_start <= verify_finish <= finish,
        "verifier timing lies outside the trial",
    )
    for seat in result["seat_results"]:
        execution = seat["agent_execution"]
        entered = datetime.fromisoformat(execution["started_at"])
        exited = datetime.fromisoformat(execution["finished_at"])
        study._require(
            start <= entered <= exited <= verify_start,
            "seat timing lies outside the trial",
        )


def _collect_cell(out, registered_out, cell, case, task_hashes, verify):
    row = {"case_id": cell["case_id"], "status": "unattempted"}
    root, native = study._paths(out, cell)
    registered_root, registered_native = study._paths(registered_out, cell)
    reason = "invalid cell path"
    try:
        try:
            _checked_path(out, root.relative_to(out).as_posix())
        except FileNotFoundError:
            return row
        reason = "invalid attempt receipt"
        try:
            receipt = study._object(_read_file(root, "attempt.json"))
        except FileNotFoundError:
            return {**row, "status": "incomplete"}
        study._require(
            receipt.keys()
            == {"schema", "trial_name", "returncode", "task_sha256", "native_sha256"}
            and receipt["schema"] == ATTEMPT_SCHEMA
            and receipt["trial_name"] == cell["trial_name"]
            and type(receipt["returncode"]) is int
            and receipt["task_sha256"] == study._sha(study._canonical(task_hashes)),
            reason,
        )
        reason = "task or launch configuration differs from registration"
        task = _checked_path(root, "task")
        config = study._object(_read_file(root, "trial-config.json"))
        study._require(
            _task_hashes(task) == task_hashes
            and _read_file(task, "environment/case.json")
            == study._canonical({"mode": MODE, "case": case})
            and study._canonical(config)
            == study._canonical(
                study._native_config(registered_root / "task", registered_native, cell)
            ),
            reason,
        )
        reason = "invalid native file evidence or digest mismatch"
        files = _native_files(out, native)
        study._require(receipt["native_sha256"] == _digests(files), reason)
        reason = "invalid or missing native result"
        result = study._object(files["result"]) if "result" in files else None
        if result is not None:
            study._require(
                result["trial_name"] == cell["trial_name"]
                and result["trial_uri"] == registered_native.as_uri(),
                reason,
            )
            row["native_id"] = study._uuid(result["id"])
        if receipt["returncode"] != 0 or (
            result is not None and result["exception_info"] is not None
        ):
            return {
                **row,
                "status": "failed",
                "reason": "native trial returned an error",
            }
        if result is None or files.keys() != NATIVE_FILES.keys():
            raise ValueError(reason)
        _fresh_native(result, cell)
        reason = "invalid interactive world evidence"
        score = verify(case, study._object(files["world"]))
        reason = "native reward mismatch"
        rewards = result["verifier_result"]["rewards"]
        study._require(
            isinstance(rewards, dict)
            and rewards.keys() == {"reward"}
            and type(rewards["reward"]) in (int, float)
            and rewards["reward"] == score["reward"]
            and files["reward"].strip() in {b"0", b"0.0", b"1", b"1.0"}
            and float(files["reward"]) == score["reward"],
            reason,
        )
        observed = {
            key: score[key]
            for key in (
                "choice",
                "termination",
                "official_exact_match",
                "physically_feasible",
                "reward",
                "resources",
            )
        }
        return {**row, "status": "verified", "observed": observed}
    except EVIDENCE_ERRORS:
        return {**row, "status": "invalid", "reason": reason}


def _summary(rows):
    totals = {"planned": len(rows), **dict.fromkeys(STATUSES, 0)}
    terminations = dict.fromkeys(("exhausted", "unanimous", "budget"), 0)
    totals.update(
        measured_nulls=0,
        official_exact_matches=0,
        physically_feasible=0,
        rounds=0,
        scalar_disclosures=0,
    )
    for row in rows:
        totals[row["status"]] += 1
        if row["status"] == "verified":
            observed = row["observed"]
            totals["measured_nulls"] += observed["choice"] is None
            terminations[observed["termination"]] += 1
            totals["official_exact_matches"] += observed["official_exact_match"] is True
            totals["physically_feasible"] += observed["physically_feasible"] is True
            totals["rounds"] += observed["resources"]["round_trips"]
            totals["scalar_disclosures"] += observed["resources"]["scalar_disclosures"]
    return {**totals, "terminations": terminations}


def collect(out: Path):
    """Revalidate registered native evidence without launching any process."""
    out = _root(out)
    mode = out.stat().st_mode
    study._require(
        stat.S_ISDIR(mode) and not mode & 0o077, "--out must be a private directory"
    )
    study._require(
        _checked_path(out, "cells").is_dir(), "missing registered cells directory"
    )
    plan = study._object(_read_file(out, "plan.json"))
    source = study._object(_read_file(out, "source.json"))
    study._uuid(plan["study_id"])
    registered_out = Path(source["out_path"])
    cases = source["cases"]
    reviewed = source["reviewed_sha256"]
    hashes = plan["task_sha256"]
    case_ids = [cell["case_id"] for cell in plan["cells"]]
    for case_id in case_ids:
        study._case_key(case_id)
    study._require(
        registered_out.is_absolute()
        and all(
            Path(source[key]).is_absolute()
            for key in ("dataset_path", "harbor_executable_path")
        )
        and re.fullmatch(r"[a-f0-9]{64}", source["harbor_executable_sha256"])
        and source["dataset_commit"] == DATASET_COMMIT
        and source.get("policy") == POLICY
        and type(source.get("round_limit")) is int
        and source["round_limit"] == ROUND_LIMIT
        and type(plan.get("round_limit")) is int
        and 0 < len(case_ids) == len(set(case_ids)) <= study.CORPUS_SIZE
        and set(case_ids) == cases.keys() == hashes.keys()
        and _valid_digests(reviewed, REVIEWED_FILES)
        and all(
            cases[case_id]["case_id"] == case_id
            and cases[case_id]["dataset_commit"] == DATASET_COMMIT
            and _valid_digests(hashes[case_id], TASK_FILES)
            and all(
                hashes[case_id][target] == reviewed[name]
                for target, name in TASK_SOURCE_FILES.items()
            )
            for case_id in case_ids
        )
        and plan == _plan(plan["study_id"], source, case_ids, hashes),
        "registered plan and source snapshot do not match",
    )
    study._require(
        study._sha(_read_file(ADAPTER, "interactive_verify.py"))
        == reviewed["interactive_verify.py"],
        "local independent verifier differs from registration",
    )
    from interactive_verify import verify

    rows = [
        _collect_cell(
            out,
            registered_out,
            cell,
            cases[cell["case_id"]],
            hashes[cell["case_id"]],
            verify,
        )
        for cell in sorted(
            plan["cells"], key=lambda cell: study._case_key(cell["case_id"])
        )
    ]
    owners = {}
    groups = {}
    for row in rows:
        groups.setdefault(row["case_id"].split("/")[0], []).append(row)
        if "native_id" in row:
            owners.setdefault(row["native_id"], []).append(row)
    for claims in owners.values():
        if len(claims) > 1:
            for row in claims:
                row.pop("observed", None)
                row.update(status="invalid", reason="duplicate native trial UUID")
    return {
        "schema": RESULT_SCHEMA,
        "study_id": plan["study_id"],
        "mode": MODE,
        "policy": POLICY,
        "round_limit": ROUND_LIMIT,
        "summary": _summary(rows),
        "by_stratum": {stratum: _summary(group) for stratum, group in groups.items()},
        "cells": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    launch = subcommands.add_parser("run", help="register and run a new private cohort")
    launch.add_argument("--dataset", required=True, type=Path)
    launch.add_argument("--out", required=True, type=Path)
    launch.add_argument("--harbor", required=True, type=Path)
    launch.add_argument("--cases", required=True, metavar="ID,ID,...")
    lookup = subcommands.add_parser("collect", help="revalidate existing evidence")
    lookup.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            report = run(args.dataset, args.out, args.harbor, args.cases)
        else:
            report = collect(args.out)
    except (*EVIDENCE_ERRORS, subprocess.SubprocessError) as exc:
        parser.exit(1, f"interactive: {exc}\n")
    print(study._canonical(report).decode("ascii"))
    summary = report["summary"]
    return int(args.command == "run" and summary["verified"] != summary["planned"])


if __name__ == "__main__":
    sys.exit(main())
