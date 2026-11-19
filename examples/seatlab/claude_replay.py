"""Replay two held-out dispatch observations through first-party Claude Max.

Plan and run are separate commands. Run consumes the plan even if the auth
preflight or an arm fails. A failed arm stops the study; no cells are replaced.
The CLI budget and reported cost are estimates, not personal billing records.
"""

import argparse
import json
import math
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from typing import Any, NamedTuple, Never, cast, override
from uuid import uuid4

if __package__:
    from .capsule import _reject_constant, _unique_object, encode_view, extract_view
    from .evidence import _score, read_trial_with_world
    from .export_capsules import reconstructed_capsules
    from .paired import _read_receipt, _receipt_cells
else:
    from capsule import _reject_constant, _unique_object, encode_view, extract_view
    from evidence import _score, read_trial_with_world
    from export_capsules import reconstructed_capsules
    from paired import _read_receipt, _receipt_cells

_MODEL = "claude-sonnet-5-5"
_BUDGET_USD = "0.30"
_AUTH_TIMEOUT = 15
_GENERATION_TIMEOUT = 90
_EPISODES = 12
_CONDITION = "reordered"
_SEEDS = (1001, 1003)
_ORDERS = (("raw", "highest_revision"), ("highest_revision", "raw"))
_AUTH = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "max",
}
_TELEMETRY = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "duration_ms",
    "duration_api_ms",
    "estimated_cost_usd",
)
# These documented CLI controls are fixed by the study, never inherited.
_CLI_CONTROLS = {
    "CLAUDE_CODE_MAX_RETRIES": "0",
    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1",
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}


class _Invalid(ValueError):
    """A safe preflight error code, with no underlying process or path text."""


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        raise _Invalid("invalid_arguments")


class _Cell(NamedTuple):
    record: dict[str, Any]
    body: bytes
    world: dict[str, Any]


_Invoke = Callable[[list[str], bytes | None, int], subprocess.CompletedProcess[bytes]]


def _policy() -> Any:
    # Do not load Harbor's optional runtime until this experiment needs its policy.
    if __package__:
        from . import seat_agent
    else:
        import seat_agent

    return seat_agent


def _system_prompt() -> str:
    return _policy()._RULES["dispatch"] + (
        " Output only a JSON object. Do not include explanations."
    )


def _json_object(raw: str | bytes) -> dict[str, Any]:
    value = json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _highest_revision(view: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for case in view["cases"]:
        reports = case["messages"]
        revisions = [report["revision"] for report in reports]
        if len(set(revisions)) != len(revisions):
            raise _Invalid("ambiguous_observed_revision")
        latest = max(reports, key=lambda report: report["revision"], default=None)
        cases.append({**case, "messages": [] if latest is None else [latest]})
    return {**view, "cases": cases}


def _arrival_config(path: Path) -> bool:
    native = _json_object((path / "result.json").read_bytes())
    # The native evidence reader already verifies the 2 seats and their shape.
    return all(
        participant.get("import_path") == "seat_agent:SeatAgent"
        and participant.get("kwargs", {}).get("policy") == "arrival"
        for participant in native["config"]["participants"]
    )


def _study(panel: Path, system: str) -> tuple[dict[str, Any], list[_Cell]]:
    panel = panel.resolve(strict=True)
    plan = _read_receipt(panel / "plan.json", "seatlab.panel-plan/v1")
    if (
        plan.get("policy") != "arrival"
        or plan.get("episodes") != _EPISODES
        or plan.get("seeds") != list(_SEEDS)
        or not isinstance(plan.get("conditions"), list)
        or _CONDITION not in plan["conditions"]
        or plan.get("hosted_route") is not None
    ):
        raise _Invalid("not_heldout_arrival_panel")
    planned = _receipt_cells(plan)
    runs = _receipt_cells(_read_receipt(panel / "runs.json", "seatlab.panel-runs/v1"))
    cells = []
    native_ids = set()
    for seed, order in zip(_SEEDS, _ORDERS, strict=True):
        selected = [
            cell
            for cell in planned.values()
            if (cell["seed"], cell["condition"]) == (seed, _CONDITION)
        ]
        if len(selected) != 1:
            raise _Invalid("missing_source_cell")
        run = runs.get(selected[0]["cell_id"], {})
        relative = run.get("trial_path")
        if (
            run.get("status") != "measured"
            or (run.get("seed"), run.get("condition")) != (seed, _CONDITION)
            or not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
        ):
            raise _Invalid("invalid_source_completion")
        trial = (panel / relative).resolve(strict=True)
        if not trial.is_relative_to(panel) or trial == panel:
            raise _Invalid("invalid_source_path")
        receipt, world = read_trial_with_world(trial)
        if (
            receipt != run.get("evidence")
            or (receipt["seed"], receipt["condition"]) != (seed, _CONDITION)
            or receipt["score"]["episodes"] != _EPISODES
            or receipt["trial_id"] in native_ids
            or not _arrival_config(trial)
            or any(
                seat["agent_name"] != "seatlab-dispatch-handoff"
                or seat["model_info"] is not None
                for seat in receipt["seats"].values()
            )
        ):
            raise _Invalid("source_dossier_mismatch")
        native_ids.add(receipt["trial_id"])
        capsule = reconstructed_capsules(world)["dispatch"]
        raw = extract_view(capsule)
        if capsule["basis"] != "reconstructed" or not isinstance(raw, dict):
            raise _Invalid("not_reconstructed_dispatch")
        _policy()._checked_view(raw, "dispatch")
        checked = cast("dict[str, Any]", raw)
        views = {"raw": checked, "highest_revision": _highest_revision(checked)}
        source_hash = _digest(encode_view(world))
        for arm in order:
            view = views[arm]
            body = encode_view(view)
            record = {
                "position": len(cells) + 1,
                "seed": seed,
                "arm": arm,
                "cases": _EPISODES,
                "observed_reports": sum(
                    len(case["messages"]) for case in view["cases"]
                ),
                "input_bytes": len(body),
                "input_sha256": _digest(body),
                "source_trial_id": receipt["trial_id"],
                "source_dossier_sha256": source_hash,
            }
            cells.append(_Cell(record, body, world))
    return {
        "schema": "seatlab.claude-replay-plan/v1",
        "route": "first_party_claude_max",
        "model": _MODEL,
        "condition": _CONDITION,
        "system_prompt_sha256": _digest(system.encode()),
        "generation_timeout_seconds": _GENERATION_TIMEOUT,
        "estimated_budget_usd_per_generation": float(_BUDGET_USD),
        "maximum_generations": len(cells),
        "stop_on_first_unmeasured": True,
        "cells": [cell.record for cell in cells],
    }, cells


def _output(path: Path, panel: Path) -> Path:
    if path.is_symlink():
        raise _Invalid("output_must_be_private_and_external")
    path = path.resolve()
    if path.is_relative_to(panel.resolve()) or any(
        os.path.lexists(parent / ".git") for parent in (path, *path.parents)
    ):
        raise _Invalid("output_must_be_private_and_external")
    return path


def _create(path: Path, document: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encode_view(document) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_plan(path: Path, expected: dict[str, Any]) -> str:
    directory = path.stat()
    approved = path / "plan.json"
    plan_stat = approved.lstat()
    if (
        directory.st_uid != os.geteuid()
        or stat.S_IMODE(directory.st_mode) != 0o700
        or plan_stat.st_uid != os.geteuid()
        or not stat.S_ISREG(plan_stat.st_mode)
        or stat.S_IMODE(plan_stat.st_mode) != 0o400
    ):
        raise _Invalid("output_must_be_private_and_external")
    approved_bytes = approved.read_bytes()
    if approved_bytes != encode_view(expected) + b"\n":
        raise _Invalid("plan_or_source_changed")
    return _digest(approved_bytes)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _managed_configuration() -> bool:
    # Safe/restricted modes still honor local managed settings and managed hooks.
    # Do not use this study on a managed Claude host, or try to bypass its policy.
    root = Path("/etc/claude-code")
    candidates = []
    if sys.platform == "darwin":
        root = Path("/Library/Application Support/ClaudeCode")
        for parent in (
            Path("/Library/Managed Preferences"),
            Path("/Library/Preferences"),
        ):
            candidates.extend(
                directory / "com.anthropic.claudecode.plist"
                for directory in (parent, parent / Path.home().name)
            )
    return any(
        os.path.lexists(path)
        for path in (
            *candidates,
            root / "managed-settings.json",
            root / "managed-settings.d",
            root / "managed-mcp.json",
        )
    )


def _invoke(
    args: list[str], body: bytes | None, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    env = {
        key: os.environ[key]
        for key in ("HOME", "USER", "LOGNAME", "PATH")
        if key in os.environ
    }
    env["PATH"] = os.pathsep.join(
        part
        for part in env.get("PATH", os.defpath).split(os.pathsep)
        if os.path.isabs(part)
    )
    executable = which("claude", path=env["PATH"])
    if not executable:
        raise FileNotFoundError
    with TemporaryDirectory(prefix="seatlab-replay-", dir="/tmp") as scratch:
        return subprocess.run(
            [executable, *args],
            input=body if body is not None else b"",
            capture_output=True,
            cwd=scratch,
            env={**env, **_CLI_CONTROLS, "TMPDIR": scratch},
            timeout=timeout,
            check=False,
        )


def _preflight(invoke: _Invoke) -> str:
    if _managed_configuration():
        return "managed_configuration"
    try:
        process = invoke(["auth", "status", "--json"], None, _AUTH_TIMEOUT)
        if process.returncode == 0:
            response = _json_object(process.stdout)
            if response.get("loggedIn") is True and all(
                response.get(key) == value for key, value in _AUTH.items()
            ):
                return "verified"
    except FileNotFoundError:
        return "cli_unavailable"
    except subprocess.TimeoutExpired:
        return "auth_timeout"
    except (OSError, ValueError, RecursionError):
        pass
    return "unverified_max_auth"


def _args(system: str, session: str) -> list[str]:
    # See https://code.claude.com/docs/en/cli-reference . A single turn is not
    # forced to produce JSON, and there is no schema tool to induce a retry.
    return [
        "--safe-mode",
        "--restricted",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-chrome",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--system-prompt",
        system,
        "--model",
        _MODEL,
        "--max-turns",
        "1",
        "--max-budget-usd",
        _BUDGET_USD,
        "--session-id",
        session,
        "--output-format",
        "json",
        "--print",
    ]


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value < 2**53 else None


def _telemetry(response: Mapping[str, Any]) -> dict[str, int | float | None]:
    result: dict[str, int | float | None] = dict.fromkeys(_TELEMETRY)
    model_usage = response.get("modelUsage")
    usage = model_usage.get(_MODEL) if isinstance(model_usage, dict) else None
    if isinstance(usage, dict):
        for field, key in zip(
            _TELEMETRY[:4],
            (
                "inputTokens",
                "cacheReadInputTokens",
                "cacheCreationInputTokens",
                "outputTokens",
            ),
            strict=True,
        ):
            result[field] = _count(usage.get(key))
    for field in ("duration_ms", "duration_api_ms"):
        result[field] = _count(response.get(field))
    cost = response.get("total_cost_usd")
    if (
        isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and 0 <= cost < math.inf
    ):
        result["estimated_cost_usd"] = cost
    return result


def _failure(
    process: subprocess.CompletedProcess[bytes], response: dict[str, Any]
) -> str | None:
    if (
        response.get("subtype") == "error_max_budget_usd"
        or response.get("terminal_reason") == "budget_exhausted"
    ):
        return "budget_exhausted"
    if response.get("stop_reason") == "refusal":
        return "refused"
    if response.get("api_error_status") == 429:
        return "rate_limited"
    if not response or process.returncode != 0 or response.get("is_error") is not False:
        return _cli_failure(process)
    if (
        response.get("type") != "result"
        or response.get("subtype") != "success"
        or response.get("stop_reason") not in (None, "end_turn")
        or response.get("terminal_reason") not in (None, "completed")
    ):
        return "unfinished"
    return None


def _cli_failure(process: subprocess.CompletedProcess[bytes]) -> str:
    # Older CLI versions can report quota and unsupported options in prose.
    # Inspect transiently; never keep this text or the account identity.
    text = (process.stdout[:16384] + process.stderr[:8192]).lower()
    if re.search(
        rb"rate.limit|usage limit|hit your limit|out of (?:extra usage|usage credits)",
        text,
    ):
        return "rate_limited"
    if re.search(
        rb"(?:unknown|unrecognized|invalid) option|unsupported model"
        rb"|issue with the selected model|model is not a recognized model id",
        text,
    ):
        return "cli_or_model_unsupported"
    return "cli_failed" if process.returncode != 0 else "invalid_cli_result"


def _grade(world: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Replace only the completed choices in a local copy. The independent
    # native scorer determines the outcome against the original reference.
    cases = [
        {**case, "chosen": row["bay"]}
        for case, row in zip(world["cases"], rows, strict=True)
    ]
    score = _score({**world, "cases": cases})
    return {
        "optimal": score["optimal"],
        "feasible": score["feasible"],
        "decisions": [
            {
                "ordinal": row["id"] + 1,
                "bay": row["bay"],
                "optimal": grade["optimal"],
                "feasible": grade["feasible"],
            }
            for row, grade in zip(rows, score["rows"], strict=True)
        ],
    }


def _observe(
    result: dict[str, Any], cell: _Cell, system: str, session: str, invoke: _Invoke
) -> None:
    try:
        process = invoke(_args(system, session), cell.body, _GENERATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        result["stage"] = "generation_timeout"
        return
    except OSError:
        result["stage"] = "cli_launch_failed"
        return
    try:
        response = _json_object(process.stdout)
    except (ValueError, RecursionError):
        response = {}
    result["telemetry"] = _telemetry(response)
    failure = _failure(process, response)
    if failure:
        result["stage"] = failure
        return
    usage = response.get("modelUsage")
    if (
        not isinstance(usage, dict)
        or usage.keys() != {_MODEL}
        or not isinstance(usage[_MODEL], dict)
    ):
        result["stage"] = "model_unattested"
        return
    result["served_model"] = _MODEL
    if (
        response.get("session_id") != session
        or _count(response.get("num_turns")) != 1
        or response.get("permission_denials", []) != []
    ):
        result["stage"] = "session_unattested"
        return
    cost = result["telemetry"]["estimated_cost_usd"]
    if cost is not None and cost > float(_BUDGET_USD):
        result["stage"] = "budget_estimate_exceeded"
        return
    policy = _policy()
    try:
        text = response.get("result")
        if not isinstance(text, str) or len(text) > 8192:
            raise ValueError("not a bounded JSON response")
        submission = _json_object(text)
        policy._checked_submission(submission, "dispatch", _EPISODES)
    except (ValueError, RecursionError, policy._Unmeasured):
        result["stage"] = "invalid_or_refused_decisions"
        return
    rows = sorted(submission["rows"], key=lambda row: row["id"])
    result["measurement"] = _grade(cell.world, rows)
    result["stage"] = "complete"


def _pairs(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(cell["seed"], cell["arm"]): cell["measurement"] for cell in cells}
    pairs = []
    for seed in _SEEDS:
        raw, reduced = (by_key[seed, arm] for arm in ("raw", "highest_revision"))
        changed = None
        if raw is not None and reduced is not None:
            changed = [
                first["ordinal"]
                for first, second in zip(
                    raw["decisions"], reduced["decisions"], strict=True
                )
                if first["bay"] != second["bay"]
            ]
        pairs.append({"seed": seed, "changed_ordinals": changed})
    return pairs


def _run(
    out: Path, study: dict[str, Any], cells: list[_Cell], system: str, invoke: _Invoke
) -> dict[str, Any]:
    digest = _read_plan(out, study)
    if any(path.name != "plan.json" for path in out.iterdir()):
        raise _Invalid("study_already_consumed")
    try:
        _create(out / "started.json", {"plan_sha256": digest, "at": _timestamp()})
    except FileExistsError:
        raise _Invalid("study_already_consumed") from None
    route = _preflight(invoke)
    results = []
    proceed = route == "verified"
    for cell in cells:
        row = {
            **{
                key: value
                for key, value in cell.record.items()
                if not key.startswith("source_")
            },
            "attempted": False,
            "stage": "not_attempted",
            "served_model": None,
            "measurement": None,
            "telemetry": _telemetry({}),
        }
        if proceed:
            position = row["position"]
            session = str(uuid4())
            _create(
                out / f"attempt-{position:02}.json",
                {
                    "position": position,
                    "plan_sha256": digest,
                    "at": _timestamp(),
                    "session_id": session,
                },
            )
            row["attempted"] = True
            _observe(row, cell, system, session, invoke)
            _create(out / f"result-{position:02}.json", row)
            proceed = row["stage"] == "complete"
        results.append(row)
    report = {
        "schema": "seatlab.claude-replay/v1",
        "plan_sha256": digest,
        "configured_model": _MODEL,
        "route_preflight": route,
        "planned": len(cells),
        "attempted": sum(row["attempted"] for row in results),
        "complete": sum(row["stage"] == "complete" for row in results),
        "cells": results,
        "pairs": _pairs(results),
    }
    _create(out / "report.json", report)
    return report


def main() -> None:
    parser = _Parser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument(
        "--panel", required=True, type=Path, help="Retained held-out arrival panel A"
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="New (plan) or prepared (run) private directory outside git",
    )
    try:
        args = parser.parse_args()
        out = _output(args.out, args.panel)
        system = _system_prompt()
        study, cells = _study(args.panel, system)
        if args.command == "plan":
            out.mkdir(mode=0o700)
            _create(out / "plan.json", study)
            result = study
        else:
            result = _run(out, study, cells, system, _invoke)
    except _Invalid as exc:
        result = {"status": "invalid", "stage": str(exc)}
        code = 2
    except (ValueError, OSError, RuntimeError, ImportError, TypeError, KeyError):
        result = {"status": "invalid", "stage": "source_or_path_unavailable"}
        code = 2
    else:
        code = int(args.command == "run" and result["complete"] != len(cells))
    print(encode_view(result).decode("ascii"))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
