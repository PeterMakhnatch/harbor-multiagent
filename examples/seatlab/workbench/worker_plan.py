from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SIDES = ("left", "right")
EXITS = {"matches_reference": 0, "different_plan": 1}
PIN_OPTION = "--reference-sha256"
COUNT_OPTIONS = ("--copies", "--concurrency", "--max-retries")
VALUE_OPTIONS = (PIN_OPTION, *COUNT_OPTIONS)
JSON_OPTION = "--json"
HELP_OPTIONS = ("--help", "-h")
CHECK_POSITIONALS = 3
MAX_ARGUMENTS = 1 + CHECK_POSITIONALS + 2 * len(VALUE_OPTIONS) + 1
MAX_ARGUMENT_BYTES = 4096
COUNT = re.compile(r"0|[1-9][0-9]{0,4}")

ARGUMENTS_ERROR = "Invalid worker-plan arguments. Use worker-plan --help."
EXPORT_NATIVE_ERROR = (
    "Worker plan export needs the installed Harbor fork. Do not use -S."
)
EXPORT_ERROR = "Cannot verify the local job config. No worker plan receipt."
CHECK_UNAVAILABLE = (
    "Worker plan check is not available in this checkout. No match report."
)
CHECK_ERROR = (
    "Cannot verify the pinned reference, both worker receipts and the requested schedule. "
    "No match report."
)

EXPORT_USAGE = "usage: workbench worker-plan export JOB_CONFIG"
CHECK_USAGE = (
    "usage: workbench worker-plan check REFERENCE LEFT_RECEIPT RIGHT_RECEIPT "
    "--reference-sha256 sha256:HEX --copies N --concurrency N --max-retries N [--json]"
)
EXPORT_HELP = f"""{EXPORT_USAGE}

Read one local job config through the installed Harbor fork. Print a path-free receipt
with its prepared input, native trial key, copies, concurrency and maximum retries.
Exit 0 with a receipt, or 2 with no receipt. No job starts and nothing is written."""
CHECK_HELP = f"""{CHECK_USAGE}

Check two worker receipts with the Python standard library. Keep the exact stdout of
a successful `isolate --configs --json`. Hold its raw SHA-256 digest independently.
Both receipts must match their assigned inputs and the schedule you specify.
Exit 0: matches_reference, with worker-local native trial keys.
Exit 1: different_plan, with no keys. Exit 2: unusable input, with no report.

  --copies N          Copies per worker, 1 to 64.
  --concurrency N     Concurrent trials per job, 1 to 1024.
  --max-retries N     Retries per trial, 0 to 16.
  --json              Print one line of sorted JSON."""
HELP = f"""{EXPORT_USAGE}
{CHECK_USAGE}

Workers send receipts, not private job configs. The coordinator checks both receipts
against one pinned reference without Harbor. Use export --help or check --help."""
COMMAND_HELP = {"export": EXPORT_HELP, "check": CHECK_HELP}


def _refuse(message: str) -> int:
    assert message and "\n" not in message
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    return 2


def _emit(text: str) -> None:
    assert text and not text.endswith("\n")
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _flagged(token: str) -> bool:
    return token.startswith("-")


def _bounded(args: list[str]) -> bool:
    if type(args) is not list or not 1 <= len(args) <= MAX_ARGUMENTS:
        return False
    for arg in args:
        if type(arg) is not str or not arg or "\x00" in arg:
            return False
        try:
            size = len(os.fsencode(arg))
        except UnicodeError:
            return False
        if size > MAX_ARGUMENT_BYTES:
            return False
    return True


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _export(rest: list[str]) -> int:
    if len(rest) != 1 or _flagged(rest[0]):
        return _refuse(ARGUMENTS_ERROR)
    try:
        from .job_projection import NATIVE_MISSING, export
    except ImportError:
        return _refuse(EXPORT_NATIVE_ERROR)
    try:
        receipt = export(Path(rest[0]))
    except ImportError:
        return _refuse(EXPORT_NATIVE_ERROR)
    except ValueError as error:
        missing = error.args == (NATIVE_MISSING,)
        return _refuse(EXPORT_NATIVE_ERROR if missing else EXPORT_ERROR)
    except (OSError, RecursionError):
        return _refuse(EXPORT_ERROR)
    _emit(_canonical(receipt))
    return 0


def _parse_check(rest: list[str]) -> tuple[list[str], dict[str, str], bool] | None:
    positionals: list[str] = []
    values: dict[str, str] = {}
    as_json = False
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in VALUE_OPTIONS:
            if token in values or index + 1 >= len(rest) or _flagged(rest[index + 1]):
                return None
            values[token] = rest[index + 1]
            index += 2
        elif token == JSON_OPTION:
            if as_json:
                return None
            as_json = True
            index += 1
        elif _flagged(token):
            return None
        else:
            positionals.append(token)
            index += 1
    if len(positionals) != CHECK_POSITIONALS or set(values) != set(VALUE_OPTIONS):
        return None
    return positionals, values, as_json


def _check_text(report: dict[str, Any]) -> str:
    matched = report["status"] == "matches_reference"
    lines = [
        (
            "matches_reference: both worker receipts match the pinned reference plan."
            if matched
            else "different_plan: the worker receipts do not match the pinned reference plan. "
            "No worker native keys."
        ),
        (
            f"Schedule: {report['copies_per_arm']} copies per arm, concurrency "
            f"{report['concurrency']}, max retries {report['max_retries']}."
        ),
        f"Reference: {report['reference_sha256']}; comparison {report['comparison']}.",
    ]
    if matched:
        records = report["trial_lock_records"]
        lines.extend(
            f"{side.capitalize()} worker native trial-lock key: {records[side]}"
            for side in SIDES
        )
    else:
        lines.extend(f"Reason: {reason}" for reason in report["reasons"])
    lines.append(report["caveat"])
    return "\n".join(lines)


def _check(rest: list[str]) -> int:
    parsed = _parse_check(rest)
    if parsed is None:
        return _refuse(ARGUMENTS_ERROR)
    positionals, values, as_json = parsed
    try:
        from .isolate import PIN
        from .worker_handoff import check
    except ImportError:
        return _refuse(CHECK_UNAVAILABLE)
    pin = values[PIN_OPTION]
    counts = [values[option] for option in COUNT_OPTIONS]
    if PIN.fullmatch(pin) is None or any(
        COUNT.fullmatch(count) is None for count in counts
    ):
        return _refuse(ARGUMENTS_ERROR)
    copies, concurrency, max_retries = (int(count) for count in counts)
    reference, left, right = (Path(path) for path in positionals)
    try:
        report = check(reference, left, right, pin, copies, concurrency, max_retries)
    except ImportError:
        return _refuse(CHECK_UNAVAILABLE)
    except (ValueError, OSError, RecursionError):
        return _refuse(CHECK_ERROR)
    _emit(_canonical(report) if as_json else _check_text(report))
    return EXITS[report["status"]]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not _bounded(args):
        return _refuse(ARGUMENTS_ERROR)
    command, rest = args[0], args[1:]
    if command in HELP_OPTIONS and not rest:
        _emit(HELP)
        return 0
    if command not in COMMAND_HELP:
        return _refuse(ARGUMENTS_ERROR)
    if len(rest) == 1 and rest[0] in HELP_OPTIONS:
        _emit(COMMAND_HELP[command])
        return 0
    if command == "export":
        return _export(rest)
    return _check(rest)


if __name__ == "__main__":
    raise SystemExit(main())
