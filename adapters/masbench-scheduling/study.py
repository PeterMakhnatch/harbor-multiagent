"""Audit MASBench Scheduling source data and run deterministic Harbor seat trials."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cases import (
    DATASET_COMMIT,
    evaluate_reference,
    list_cases,
    load_case,
    member_starts,
)
from template.tests.verify import verify_world

SOURCE_DIR = "Scheduling/meeting/minutes_10/5days"
CORPUS_SIZE = 200
MODES = ("full", "first8")
SEATS = ("m1", "m2", "m3", "m4", "m5")
DATASET = {
    "repository": "https://github.com/BUPT-GAMMA/MASBench",
    "commit": DATASET_COMMIT,
    "path": SOURCE_DIR,
    "reference": "task_plans",
    "corpus_cases": CORPUS_SIZE,
}
PROTOCOLS = {
    "origin": "adapter controls, not upstream MASBench model runs",
    "full": "all available starts per seat; choose the earliest common start",
    "first8": "first 8 available starts per seat; choose the earliest common start",
}
NATIVE_FILES = {
    "result": "result.json",
    "world": "artifacts/logs/world/calendar.json",
    "reward": "verifier/reward.txt",
}
PLAN_SCHEMA = "seatlab.calendar-study-plan/v1"
ATTEMPT_SCHEMA = "seatlab.calendar-study-attempt/v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate field in JSON receipt")
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _object(data: bytes) -> dict[str, Any]:
    value = json.loads(data, object_pairs_hook=_unique_keys, parse_constant=_nonfinite)
    _require(isinstance(value, dict), "expected a JSON object")
    return value


def _write_once(path, value):
    # Publish only complete receipts; link fails rather than replacing a file.
    temp = path.with_suffix(".tmp")
    stream = temp.open("xb")
    try:
        with stream:
            stream.write(_canonical(value) + b"\n")
        os.link(temp, path)
    finally:
        temp.unlink()


def _command(args):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=20, check=False
    )
    if result.returncode:
        raise ValueError(f"{args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _case_key(case_id):
    _require(re.fullmatch(r"[2-5]/[1-9][0-9]*", case_id), "invalid case ID")
    stratum, number = case_id.split("/")
    return int(stratum), int(number)


def _source_case_ids(dataset):
    git = ["git", "--no-optional-locks", "-C", str(dataset)]
    _require(
        Path(_command([*git, "rev-parse", "--show-toplevel"])).resolve() == dataset,
        "--dataset must be the upstream repository root",
    )
    _require(
        _command([*git, "rev-parse", "HEAD"]) == DATASET_COMMIT,
        f"--dataset must be at {DATASET_COMMIT}",
    )
    _require(
        not _command(
            [*git, "diff", "--name-only", "--no-ext-diff", "HEAD", "--", SOURCE_DIR]
        ),
        "original Scheduling source files differ from the pinned revision",
    )
    case_ids = sorted(list_cases(dataset), key=_case_key)
    _require(
        len(set(case_ids)) == len(case_ids) == CORPUS_SIZE,
        f"expected {CORPUS_SIZE} unique original cases",
    )
    return case_ids


def _select(case_spec, mode_spec, shard_spec, available):
    cases = list(available) if case_spec == "all" else case_spec.split(",")
    _require(
        len(cases) == len(set(cases)) and set(cases) <= set(available),
        "--cases must be all or a comma-separated list of distinct original case IDs",
    )
    modes = mode_spec.split(",")
    _require(
        len(modes) == len(set(modes)) and set(modes) <= set(MODES),
        "--modes must contain distinct values from full,first8",
    )
    _require(
        re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", shard_spec),
        "--shard must be a 1-based i/n",
    )
    index, count = map(int, shard_spec.split("/"))
    _require(
        1 <= index <= count <= len(cases), "--shard is outside the selected cohort"
    )
    cohort = sorted(cases, key=_case_key)
    # Contiguous numeric blocks; the first remainder shards each get one more case.
    size, remainder = divmod(len(cohort), count)
    start = (index - 1) * size + min(index - 1, remainder)
    stop = start + size + (index <= remainder)
    return cohort[start:stop], modes, len(cohort)


def _expected(case, mode):
    reports = {}
    for seat in SEATS:
        starts = member_starts(
            case["members"][seat],
            case["duration"],
            case["horizon"],
            case["slots_per_day"],
        )
        reports[seat] = starts if mode == "full" else starts[:8]
    choice = min(
        set(reports[SEATS[0]]).intersection(*(reports[s] for s in SEATS[1:])),
        default=None,
    )
    return reports, choice


def _report_hashes(reports):
    return {seat: _sha(_canonical(starts)) for seat, starts in reports.items()}


def _expected_score(case, reports, choice):
    exact = choice is not None and choice in case["gold"]
    return {
        "choice": choice,
        "official_exact_match": exact,
        "physically_feasible": None if choice is None else True,
        "reward": int(exact),
        "reports_sha256": _report_hashes(reports),
    }


def _summary(rows, modes, *, source_only=False):
    summary = {}
    for mode in modes:
        planned = [row for row in rows if row["mode"] == mode]
        totals: dict[str, int | dict[str, int]] = {"planned": len(planned)}
        if source_only:
            scored = [row["source_expected"] for row in planned]
            totals["computed"] = len(scored)
        else:
            statuses = ("unattempted", "incomplete", "failed", "invalid", "verified")
            totals.update(
                (status, sum(row["status"] == status for row in planned))
                for status in statuses
            )
            scored = [row["observed"] for row in planned if row["status"] == "verified"]
            totals["protocol_matches"] = sum(
                score["protocol_match"] for score in scored
            )
        totals["official_exact_match"] = {
            "passes": sum(score["official_exact_match"] for score in scored),
            "denominator": len(scored),
        }
        totals["physically_feasible"] = {
            "passes": sum(score["physically_feasible"] is True for score in scored),
            "denominator": sum(score["choice"] is not None for score in scored),
        }
        totals["no_choice"] = sum(score["choice"] is None for score in scored)
        summary[mode] = totals
    return summary


def audit(dataset: Path) -> dict[str, Any]:
    """Compare original task_plans with the calendars; do not run Harbor."""
    dataset = dataset.resolve(strict=True)
    case_ids = _source_case_ids(dataset)
    rows, references = [], []
    for case_id in case_ids:
        case = load_case(dataset, case_id)
        case_hash = _sha(_canonical(case))
        reference = evaluate_reference(case)
        references.append(
            {
                "case_id": case_id,
                "source_sha256": case["source_sha256"],
                "agrees": reference["agrees"],
                "only_in_reference": reference["only_in_reference"],
                "only_in_calendars": reference["only_in_calendars"],
            }
        )
        feasible = set(reference["intersection"]) | set(reference["only_in_calendars"])
        for mode in MODES:
            reports, choice = _expected(case, mode)
            score = _expected_score(case, reports, choice)
            score["physically_feasible"] = (
                None if choice is None else choice in feasible
            )
            rows.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "case_sha256": case_hash,
                    "source_expected": score,
                }
            )
    return {
        "schema": "seatlab.calendar-audit/v1",
        "evidence": "source-only audit; no native trials",
        "dataset": DATASET,
        "protocols": PROTOCOLS,
        "reference_agreement": {
            "matches": sum(ref["agrees"] for ref in references),
            "denominator": len(case_ids),
        },
        "reference_cases": references,
        "summary": _summary(rows, MODES, source_only=True),
        "cells": rows,
    }


def _plan(study_id, cases, modes, shard, cohort_count):
    return {
        "schema": PLAN_SCHEMA,
        "study_id": study_id,
        "dataset": DATASET,
        "protocols": PROTOCOLS,
        "shard": shard,
        "cohort_cases": cohort_count,
        "modes": modes,
        "case_hashes": {
            case_id: _sha(_canonical(case)) for case_id, case in cases.items()
        },
        "serial_native_trials": True,
        "runtime": "docker",
        "participants": {seat: "oracle" for seat in SEATS},
        "cells": [
            {
                "case_id": case_id,
                "mode": mode,
                "trial_name": f"calendar-{study_id}-{case_id.replace('/', '-')}-{mode}",
            }
            for case_id in sorted(cases, key=_case_key)
            for mode in modes
        ],
    }


def _cell_name(cell):
    return f"{cell['case_id'].replace('/', '-')}-{cell['mode']}"


def _paths(out, cell):
    root = out / "cells" / _cell_name(cell)
    return root, root / "native" / cell["trial_name"]


def _native_config(task, native, cell):
    return {
        "task": {"path": str(task)},
        "trial_name": cell["trial_name"],
        "trials_dir": str(native.parent),
        "participants": [{"seat": seat, "name": "oracle"} for seat in SEATS],
        "environment": {"type": "docker", "force_build": False, "delete": True},
    }


def _ready():
    _require(
        _command(["docker", "info", "--format", "{{.OSType}}"]) == "linux",
        "native Scheduling trials need a running Linux Docker daemon",
    )
    _command(["docker", "compose", "version", "--short"])


def _native_files(native):
    files = {}
    for key, relative in NATIVE_FILES.items():
        path = native / relative
        _require(path.resolve() == path, "symlink in native evidence path")
        if path.exists():
            files[key] = path.read_bytes()
    return files


def _run_cell(dataset, harbor, out, cell, case_hash):
    from materialize import materialize

    root, native = _paths(out, cell)
    root.mkdir(mode=0o700)
    _require(
        not native.exists() and not native.is_symlink(),
        "native trial directory already exists",
    )
    task = materialize(dataset, cell["case_id"], cell["mode"], root / "task")
    generated = _object((task / "environment" / "case.json").read_bytes())
    _require(
        set(generated) == {"mode", "case"}
        and generated["mode"] == cell["mode"]
        and _sha(_canonical(generated["case"])) == case_hash,
        "materialized input differs from registered input",
    )
    config_path = root / "trial-config.json"
    _write_once(config_path, _native_config(task, native, cell))
    with (root / "harbor.log").open("xb") as log:
        process = subprocess.run(
            [str(harbor), "trial", "start", "--config", str(config_path)],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    files = _native_files(native)
    _write_once(
        root / "attempt.json",
        {
            "schema": ATTEMPT_SCHEMA,
            "trial_name": cell["trial_name"],
            "case_sha256": case_hash,
            "returncode": process.returncode,
            "sha256": {key: _sha(data) for key, data in files.items()},
        },
    )


def run(
    dataset: Path,
    out: Path,
    harbor: Path,
    case_spec: str,
    mode_spec: str,
    shard_spec: str = "1/1",
) -> dict[str, Any]:
    """Register one immutable shard and run its cells serially, at most once."""
    dataset = dataset.resolve(strict=True)
    _require(not out.is_symlink(), "--out cannot be a symlink")
    out = out.resolve()
    harbor = harbor.resolve(strict=True)
    _require(not out.exists(), "--out must be a new directory")
    _require(
        harbor.is_file() and os.access(harbor, os.X_OK), "--harbor must be executable"
    )
    available = _source_case_ids(dataset)
    case_ids, modes, count = _select(case_spec, mode_spec, shard_spec, available)
    cases = {case_id: load_case(dataset, case_id) for case_id in case_ids}
    plan = _plan(uuid4().hex, cases, modes, shard_spec, count)
    with harbor.open("rb") as executable:
        harbor_digest = hashlib.file_digest(executable, "sha256").hexdigest()

    out.mkdir(mode=0o700)
    (out / "cells").mkdir(mode=0o700)
    # Host paths and calendar inputs are private. Only publish through an exporter.
    _write_once(
        out / "source.json",
        {
            "dataset_path": str(dataset),
            "dataset_commit": DATASET_COMMIT,
            "harbor_executable_path": str(harbor),
            "harbor_executable_sha256": harbor_digest,
            "cases": cases,
        },
    )
    _write_once(out / "plan.json", plan)
    if not (out / "STOP").exists():
        _ready()
    for n, cell in enumerate(plan["cells"], start=1):
        if (out / "STOP").exists():
            print(
                f"STOP: {len(plan['cells']) - n + 1} cells not attempted",
                file=sys.stderr,
            )
            break
        case_id, mode = cell["case_id"], cell["mode"]
        print(
            f"[{n}/{len(plan['cells'])}] {case_id} {mode}", file=sys.stderr, flush=True
        )
        _run_cell(dataset, harbor, out, cell, plan["case_hashes"][case_id])
    return collect(out)


def _uuid(value):
    identifier = UUID(value)
    _require(
        identifier.version == 4 and value in (str(identifier), identifier.hex),
        "invalid native or study UUID",
    )
    return str(identifier)


def _finished(phase):
    _require(isinstance(phase, dict), "missing native phase timing")
    start = datetime.fromisoformat(phase["started_at"])
    stop = datetime.fromisoformat(phase["finished_at"])
    _require(
        start.tzinfo is not None and stop.tzinfo is not None and stop >= start,
        "invalid native phase timing",
    )


def _oracle(agent_info):
    return agent_info["name"] == "oracle" and agent_info["model_info"] is None


def _verify_native(result, cell):
    _require(
        type(result.get("schema_version")) is int
        and result["schema_version"] == 1
        and result["trial_name"] == cell["trial_name"],
        "native result identity mismatch",
    )
    _finished(result)
    _finished(result["verifier"])
    _require(
        result["verifier_environment_mode"] == "shared", "unexpected verifier mode"
    )
    config = result["config"]
    trials_dir = Path(config["trials_dir"])
    _require(
        config["trial_name"] == cell["trial_name"]
        and config["install_only"] is False
        and config["source_trial"] is None
        and config["user_agent"] is None
        and config["verifier"]["disable"] is False
        and trials_dir.name == "native"
        and trials_dir.parent.name == _cell_name(cell)
        and Path(config["task"]["path"]) == trials_dir.parent / "task"
        and result["trial_uri"] == (trials_dir / cell["trial_name"]).as_uri(),
        "unexpected native trial config",
    )
    env = config["environment"]
    _require(
        env["type"] == "docker"
        and env["delete"] is True
        and env["force_build"] is False
        and env["import_path"] is None,
        "unexpected native environment config",
    )
    participants = config["participants"]
    model_fields = (
        "import_path",
        "model_name",
        "base_url",
        "model_api_key_name",
        "model_api_format",
    )
    _require(
        sorted(p["seat"] for p in participants) == list(SEATS)
        and all(
            p["name"] == "oracle" and all(p[key] is None for key in model_fields)
            for p in participants
        ),
        "unexpected native participant config",
    )
    _require(_oracle(result["agent_info"]), "unexpected native primary agent")
    seats = result["seat_results"]
    _require(
        sorted(seat["seat"] for seat in seats) == list(SEATS),
        "incomplete native seat set",
    )
    for seat in seats:
        _require(
            seat["under_test"] is True
            and _oracle(seat["agent_info"])
            and seat["exception_info"] is None,
            "invalid native seat result",
        )
        _finished(seat["agent_execution"])


def _collect_cell(out, cell, case, case_hash):
    reports, choice = _expected(case, cell["mode"])
    row = {
        "case_id": cell["case_id"],
        "mode": cell["mode"],
        "case_sha256": case_hash,
        "source_expected": _expected_score(case, reports, choice),
        "status": "unattempted",
    }
    root, native = _paths(out, cell)
    receipt_path = root / "attempt.json"
    if root.resolve() != root or receipt_path.resolve() != receipt_path:
        return {**row, "status": "invalid", "reason": "symlink in cell receipt path"}
    if not root.exists():
        return row
    if not receipt_path.exists():
        return {**row, "status": "incomplete"}
    reason = "invalid attempt receipt"
    try:
        receipt = _object(receipt_path.read_bytes())
        _require(
            receipt["schema"] == ATTEMPT_SCHEMA
            and receipt["trial_name"] == cell["trial_name"]
            and receipt["case_sha256"] == case_hash
            and type(receipt["returncode"]) is int,
            reason,
        )
        reason = "native file digest mismatch"
        files = _native_files(native)
        digests = {key: _sha(data) for key, data in files.items()}
        _require(receipt["sha256"] == digests, reason)
        reason = "invalid or missing native result"
        result = _object(files["result"]) if "result" in files else None
        if result is not None:
            row["native_id"] = _uuid(result["id"])
        if receipt["returncode"] != 0 or (
            result is not None and result["exception_info"] is not None
        ):
            return {
                **row,
                "status": "failed",
                "reason": "native trial returned an error",
            }
        if result is None or set(files) != set(NATIVE_FILES):
            raise ValueError(reason)
        _verify_native(result, cell)
        reason = "invalid world evidence"
        world = _object(files["world"])
        score = verify_world(case, world, mode=cell["mode"])
        reason = "native reward mismatch"
        rewards = result["verifier_result"]["rewards"]
        _require(
            isinstance(rewards, dict)
            and set(rewards) == {"reward"}
            and type(rewards["reward"]) in (int, float)
            and rewards["reward"] == score["reward"]
            and files["reward"].strip() in {b"0", b"0.0", b"1", b"1.0"}
            and float(files["reward"]) == score["reward"],
            reason,
        )
        row["status"] = "verified"
        row["observed"] = {
            "choice": score["choice"],
            "official_exact_match": score["official_exact_match"],
            "physically_feasible": score["physically_feasible"],
            "reward": score["reward"],
            "protocol_match": world["reports"] == reports and score["choice"] == choice,
            "initial_view_sha256": world["initial_view_sha256"],
            "reports_sha256": _report_hashes(world["reports"]),
            "proposal_sha256": world["proposal_sha256"],
            "evidence_sha256": digests,
        }
    except (KeyError, TypeError, AttributeError, OSError, ValueError):
        # Raw failure details can contain host paths. Keep them in the private cell only.
        row.update(status="invalid", reason=reason)
    return row


def collect(out: Path) -> dict[str, Any]:
    """Read registered inputs and receipts only. Never start or repair trials."""
    out = out.resolve(strict=True)
    plan_path, source_path = out / "plan.json", out / "source.json"
    _require(
        plan_path.resolve() == plan_path and source_path.resolve() == source_path,
        "symlink in registration path",
    )
    plan = _object(plan_path.read_bytes())
    source = _object(source_path.read_bytes())
    cases = source["cases"]
    _uuid(plan["study_id"])
    modes = plan["modes"]
    _require(isinstance(modes, list), "invalid registered modes")
    cohort_count = plan["cohort_cases"]
    _require(
        type(cohort_count) is int and len(cases) <= cohort_count <= CORPUS_SIZE,
        "invalid registered cohort size",
    )
    _require(
        re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", plan["shard"]),
        "invalid registered shard",
    )
    index, count = map(int, plan["shard"].split("/"))
    _require(1 <= index <= count <= cohort_count, "invalid registered shard")
    size, remainder = divmod(cohort_count, count)
    _require(
        len(cases) == size + (index <= remainder)
        and 0 < len(modes) == len(set(modes))
        and set(modes) <= set(MODES)
        and source["dataset_commit"] == DATASET_COMMIT
        and all(
            case["case_id"] == case_id and case["dataset_commit"] == DATASET_COMMIT
            for case_id, case in cases.items()
        )
        and plan == _plan(plan["study_id"], cases, modes, plan["shard"], cohort_count),
        "registered plan and source snapshot do not match",
    )
    rows = [
        _collect_cell(
            out, cell, cases[cell["case_id"]], plan["case_hashes"][cell["case_id"]]
        )
        for cell in plan["cells"]
    ]
    owners = {}
    for i, row in enumerate(rows):
        if "native_id" in row:
            owners.setdefault(row["native_id"], []).append(i)
    for native_id, indexes in owners.items():
        if len(indexes) > 1:
            for i in indexes:
                rows[i].pop("observed", None)
                rows[i].update(status="invalid", reason="duplicate native trial UUID")
    return {
        "schema": "seatlab.calendar-study-result/v1",
        "evidence": "registered deterministic Harbor trials",
        "study_id": plan["study_id"],
        "dataset": DATASET,
        "protocols": PROTOCOLS,
        "cohort_cases": cohort_count,
        "shard": plan["shard"],
        "summary": _summary(rows, modes),
        "cells": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit_parser = commands.add_parser(
        "audit", help="audit all 200 original cases without Harbor"
    )
    audit_parser.add_argument("--dataset", type=Path, required=True)
    run_parser = commands.add_parser("run", help="create a new registered native run")
    run_parser.add_argument("--dataset", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--harbor", type=Path, required=True)
    run_parser.add_argument("--cases", required=True, metavar="all|ID,ID,...")
    run_parser.add_argument("--modes", required=True, metavar="full,first8")
    run_parser.add_argument("--shard", default="1/1", metavar="i/n")
    commands.add_parser(
        "collect", help="read a registered run without executing it"
    ).add_argument(
        "--out",
        type=Path,
        required=True,
    )
    args = parser.parse_args()
    try:
        if args.command == "audit":
            report = audit(args.dataset)
        elif args.command == "run":
            report = run(
                args.dataset, args.out, args.harbor, args.cases, args.modes, args.shard
            )
        else:
            report = collect(args.out)
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, f"study: {exc}\n")
    if args.command == "run":
        # Full per-cell results remain available through collect.
        print(
            json.dumps({key: report[key] for key in ("study_id", "shard", "summary")})
        )
        return int(
            any(
                totals["protocol_matches"] != totals["planned"]
                for totals in report["summary"].values()
            )
        )
    print(json.dumps(report, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
