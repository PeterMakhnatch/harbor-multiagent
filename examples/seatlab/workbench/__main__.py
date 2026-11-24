"""List released SeatLab studies or verify their reports offline with the bundled analyzers."""

import hashlib
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from .catalog import STUDIES, Study

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = "seatlab.workbench/v1"
USAGE = "python -m examples.seatlab.workbench list | verify [STUDY ...] | account --help | instrument --help | attention --help | timing --help | handoffs --help | isolate --help | cohort --help | cohort-jobs --help | worker-plan --help | decision --help"
STUDY_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
MODULE = re.compile(
    r"examples\.(?:seatlab(?:\.[a-z_][a-z0-9_]{0,63}){1,4}|ship_inflight\.replay|"
    r"ship_handover\.(?:transfer|campaign_report|receiver_options|receiver_archive))"
)
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_ARGS = 256
REPORT_LIMIT = 1 << 22
OUTPUT_LIMIT = 1 << 22
CHUNK = 1 << 16
TIMEOUT_SECONDS = 120.0
INTERPRETER = ("-B", "-S", "-E", "-X", "utf8")


def _canonical(value: object) -> bytes:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.encode("ascii") + b"\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _usage_error(error: str, **detail: object) -> int:
    message = {"error": error, "schema": SCHEMA, "usage": USAGE, **detail}
    sys.stderr.buffer.write(_canonical(message))
    sys.stderr.buffer.flush()
    return 2


def _inside(relative: str) -> Path | None:
    if not relative or "\x00" in relative:
        return None
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    target = (ROOT / path).resolve()
    if target == ROOT or not target.is_relative_to(ROOT):
        return None
    return target


def _path_arguments(study: Study) -> list[str]:
    return [arg for arg in study.argv if not arg.startswith("-") and "/" in arg]


def _row_problems(study: Study) -> list[str]:
    exit_code = study.exit_code
    checks = (
        ("catalog_id_invalid", STUDY_ID.fullmatch(study.id) is not None),
        ("catalog_module_invalid", MODULE.fullmatch(study.module) is not None),
        ("catalog_sha256_invalid", DIGEST.fullmatch(study.sha256) is not None),
        (
            "catalog_exit_invalid",
            type(exit_code) is int and 0 <= exit_code <= 255,
        ),
        ("catalog_report_invalid", _inside(study.report) is not None),
        (
            "catalog_argv_invalid",
            type(study.argv) is tuple
            and len(study.argv) <= MAX_ARGS
            and all(
                type(arg) is str and arg and "\x00" not in arg for arg in study.argv
            ),
        ),
    )
    return [problem for problem, passed in checks if not passed]


def _analyzer_present(module: str) -> bool:
    base = ROOT.joinpath(*module.split("."))
    return base.with_suffix(".py").is_file() or (base / "__main__.py").is_file()


def _inputs_present(study: Study) -> bool:
    for arg in _path_arguments(study):
        target = _inside(arg)
        if target is None or not target.exists():
            return False
    return True


def _read_report(study: Study) -> tuple[bytes | None, str | None]:
    path = _inside(study.report)
    if path is None or not path.is_file():
        return None, "report_missing"
    try:
        with path.open("rb") as stream:
            data = stream.read(REPORT_LIMIT + 1)
    except OSError:
        return None, "report_unreadable"
    if len(data) > REPORT_LIMIT:
        return None, "report_too_large"
    return data, None


def _command(study: Study) -> list[str]:
    return [sys.executable, *INTERPRETER, "-m", study.module, *study.argv]


def _run(study: Study) -> tuple[int | None, bytes | None, str | None]:
    try:
        process = subprocess.Popen(
            _command(study),
            cwd=ROOT,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return None, None, "analyzer_not_started"
    pipe = process.stdout
    assert pipe is not None
    captured = bytearray()
    overflow: list[bool] = []

    def drain() -> None:
        while True:
            chunk = pipe.read(CHUNK)
            if not chunk:
                return
            if len(captured) + len(chunk) > OUTPUT_LIMIT:
                overflow.append(True)
                process.kill()
                return
            captured.extend(chunk)

    deadline = time.monotonic() + TIMEOUT_SECONDS
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    reader.join(TIMEOUT_SECONDS)
    timed_out = reader.is_alive()
    if not timed_out:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    if timed_out:
        process.kill()
        reader.join()
        process.wait()
        pipe.close()
        return None, None, "analyzer_timeout"
    pipe.close()
    if overflow:
        return process.returncode, None, "output_too_large"
    return process.returncode, bytes(captured), None


def _verify(study: Study) -> dict[str, object]:
    result: dict[str, object] = {
        "id": study.id,
        "kind": study.kind,
        "guide": study.guide,
        "report": study.report,
        "expected_exit": study.exit_code,
        "expected_sha256": study.sha256,
        "report_sha256": None,
        "output_sha256": None,
        "analyzer_exit": None,
    }
    why = _row_problems(study)
    if not why:
        why = _check(study, result)
    result["status"] = "failed" if why else "verified"
    result["why"] = why
    return result


def _check(study: Study, result: dict[str, object]) -> list[str]:
    why: list[str] = []
    report, problem = _read_report(study)
    if report is None:
        why.append(str(problem))
    else:
        result["report_sha256"] = _sha(report)
        if result["report_sha256"] != study.sha256:
            why.append("report_digest_mismatch")
    if not _analyzer_present(study.module):
        return [*why, "analyzer_missing"]
    if not _inputs_present(study):
        return [*why, "input_missing"]
    code, output, problem = _run(study)
    result["analyzer_exit"] = code
    if problem is not None:
        why.append(problem)
    if code is not None and code != study.exit_code:
        why.append("exit_mismatch")
    if output is not None:
        result["output_sha256"] = _sha(output)
        if result["output_sha256"] != study.sha256:
            why.append("output_digest_mismatch")
    return why


def _describe(study: Study) -> dict[str, object]:
    return {
        "id": study.id,
        "question": study.question,
        "kind": study.kind,
        "guide": study.guide,
        "module": study.module,
        "argv": list(study.argv),
        "command": shlex.join(["python3.12", "-BS", "-m", study.module, *study.argv]),
        "report": study.report,
        "exit_code": study.exit_code,
        "sha256": study.sha256,
    }


def _select(ids: list[str]) -> list[Study] | int:
    known = [study.id for study in STUDIES]
    if not ids:
        return list(STUDIES)
    seen: set[str] = set()
    for position, value in enumerate(ids, start=1):
        if STUDY_ID.fullmatch(value) is None:
            return _usage_error("malformed_study_id", position=position)
        if value in seen:
            return _usage_error("duplicate_study", study=value)
        if value not in known:
            return _usage_error("unknown_study", study=value, known=known)
        seen.add(value)
    return [study for study in STUDIES if study.id in seen]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["account"]:
        from .team_account import main as account

        return account(args[1:])
    if args[:1] == ["instrument"]:
        from .instrument import main as instrument

        return instrument(args[1:])
    if args[:1] == ["attention"]:
        from .attention import main as attention

        return attention(args[1:])
    if args[:1] == ["timing"]:
        from .team_timing import main as timing

        return timing(args[1:])
    if args[:1] == ["handoffs"]:
        from .handoffs import main as handoffs

        return handoffs(args[1:])
    if args[:1] == ["isolate"]:
        from .isolate import main as isolate

        return isolate(args[1:])
    if args[:1] == ["cohort"]:
        from .cohort import main as cohort

        return cohort(args[1:])
    if args[:1] == ["cohort-jobs"]:
        from .paired_jobs import main as cohort_jobs

        return cohort_jobs(args[1:])
    if args[:1] == ["worker-plan"]:
        from .worker_plan import main as worker_plan

        return worker_plan(args[1:])
    if args[:1] == ["decision"]:
        from ..decision_receipt.explain import main as decision

        return decision(args[1:])
    if not args or args[0] not in ("list", "verify"):
        return _usage_error("unknown_command")
    if len(args) > 1 + MAX_ARGS:
        return _usage_error("too_many_arguments", limit=MAX_ARGS)
    if len({study.id for study in STUDIES}) != len(STUDIES):
        return _usage_error("catalog_duplicate_id")
    if args[0] == "list":
        if len(args) != 1:
            return _usage_error("list_takes_no_arguments")
        listing = {
            "schema": SCHEMA,
            "command": "list",
            "studies": [_describe(study) for study in STUDIES],
        }
        sys.stdout.buffer.write(_canonical(listing))
        return 0
    selected = _select(args[1:])
    if isinstance(selected, int):
        return selected
    results = [_verify(study) for study in selected]
    failed = sum(result["status"] != "verified" for result in results)
    summary = {
        "schema": SCHEMA,
        "command": "verify",
        "status": "failed" if failed else "verified",
        "verified": len(results) - failed,
        "failed": failed,
        "studies": results,
    }
    sys.stdout.buffer.write(_canonical(summary))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
