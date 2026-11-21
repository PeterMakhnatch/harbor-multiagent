"""Measure a two-read dispatcher in real, independently claimed Harbor trials."""

import argparse
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, NamedTuple

from ..handoff.__main__ import _meter, _print
from ..visibility import clarify_run as files
from ..visibility import native_pairs as native
from ..visibility import spend
from ..visibility.seat_command import load_commands
from . import audit, confirmation, protocol, ranking

panel = native.run_panel
teams = native.team_driver
_RUNS = {"inventory": "inventory", "select": "dispatch", "decide": "dispatch"}
_PLAN_SCHEMA = "seatlab.native-attention-plan/v1"
_REPORT_SCHEMA = "seatlab.native-attention-report/v1"


class _Cohort(NamedTuple):
    grid: Callable[[], tuple[dict[str, Any], ...]]
    plan_schema: str
    report_schema: str
    prefix: str
    seat_sources: tuple[str, ...]


_ORIGINAL = _Cohort(protocol.grid, _PLAN_SCHEMA, _REPORT_SCHEMA, "attn-", ())
_CONFIRMATION = _Cohort(
    confirmation.grid,
    confirmation.PLAN_SCHEMA,
    confirmation.REPORT_SCHEMA,
    confirmation.RUN_PREFIX,
    ("confirmation.py", "confirmation_agent.py"),
)
_RANKED = _Cohort(
    ranking.grid,
    ranking.PLAN_SCHEMA,
    ranking.REPORT_SCHEMA,
    ranking.RUN_PREFIX,
    ("ranking.py", "ranked_agent.py"),
)
_COHORTS = {"original": _ORIGINAL, "confirmation": _CONFIRMATION, "ranked": _RANKED}
_BY_PLAN = {c.plan_schema: c for c in _COHORTS.values()}
_BY_REPORT = {c.report_schema: c for c in _COHORTS.values()}


def _source_stamp() -> dict[str, str]:
    files_by_name = panel._snapshot("arrival")
    return {name: sha256(body).hexdigest() for name, body in files_by_name.items()}


def _checked_study(path: Path) -> Path:
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("use a physical absolute path for the study")
    return teams._root(path)


def _plan(
    transport: str, config_sha: str | None, *, cohort: _Cohort = _ORIGINAL
) -> dict[str, Any]:
    if cohort is _CONFIRMATION and (transport != "direct" or config_sha is not None):
        raise ValueError("confirmation requires the direct transport")
    cells = cohort.grid()
    return {
        "schema": cohort.plan_schema,
        "transport": transport,
        "command_config_sha256": config_sha,
        "episodes": protocol.EPISODES,
        "max_direct_calls": len(cells) * len(_RUNS) if transport == "direct" else 0,
        "sources": _source_stamp(),
        "seat_sources": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("agent.py", "protocol.py", *cohort.seat_sources)
        },
        "cells": list(cells),
    }


def _ledger(
    path: Path, cells: list[dict[str, Any]], *, cohort: _Cohort
) -> dict[str, Any]:
    result = spend.report(path, run_prefix=cohort.prefix)
    keys = {
        (f"{c['cell_id']}-{stage}", role)
        for c in cells
        for stage, role in _RUNS.items()
    }
    if any((r["run"], r["role"]) not in keys for r in result["calls"]):
        raise ValueError("unregistered direct reservation")
    return result


def _register(root: Path, args: argparse.Namespace) -> None:
    config = args.config
    if (config is None) != (args.transport == "direct") or (args.spend_dir is None) != (
        args.transport == "commands"
    ):
        raise ValueError("register either two commands or one direct spend directory")
    body: bytes | None = None
    if config is not None:
        if (
            not config.is_absolute()
            or config != config.resolve(strict=True)
            or not stat.S_ISREG(config.stat().st_mode)
        ):
            raise ValueError("command config must be an absolute regular file")
        with config.open("rb") as stream:
            body = stream.read(65537)
        if not body or len(body) > 65536:
            raise ValueError("invalid command config")
    directory = args.spend_dir
    if directory is not None and (
        not directory.is_absolute() or directory != directory.resolve(strict=True)
    ):
        raise ValueError("direct spend directory must be a physical absolute path")
    cohort = _COHORTS[args.cohort]
    plan = _plan(
        args.transport,
        None if body is None else sha256(body).hexdigest(),
        cohort=cohort,
    )
    if (
        directory is not None
        and _ledger(directory, plan["cells"], cohort=cohort)["calls"]
    ):
        raise ValueError("a prior registered direct run already occupies this ledger")
    root.mkdir(mode=0o700)
    teams._check_path(root, 0o700, directory=True)
    (root / "cells").mkdir(mode=0o700)
    if body is not None:
        teams._seal(root / "commands.json", body)
        load_commands(root / "commands.json")
    private = {
        "plan": plan,
        "spend_directory": str(directory) if directory is not None else None,
    }
    teams._seal(root / "register.json", teams._bytes(private))
    _print(plan)


def _load(root: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    teams._check_path(root, 0o700, directory=True)
    teams._check_path(root / "cells", 0o700, directory=True)
    record = files._object(teams._read_sealed(root / "register.json"))
    plan = record["plan"]
    cohort = _BY_PLAN[plan["schema"]]
    transport = plan["transport"]
    if transport == "commands":
        digest = sha256(teams._read_sealed(root / "commands.json")).hexdigest()
        if record["spend_directory"] is not None:
            raise ValueError("command study has no direct spend directory")
    elif transport == "direct":
        digest = None
        path = Path(record["spend_directory"])
        if not path.is_absolute() or path != path.resolve(strict=True):
            raise ValueError("original direct spend directory unavailable")
    else:
        raise ValueError("unsupported transport")
    if teams._bytes(plan) != teams._bytes(_plan(transport, digest, cohort=cohort)):
        raise ValueError("registered sources or cells changed")
    keys = {c["cell_id"] for c in plan["cells"]}
    with os.scandir(root / "cells") as entries:
        if any(
            e.name not in keys or not e.is_dir(follow_symlinks=False) for e in entries
        ):
            raise ValueError("unexpected native trial claim")
    ledger = (
        None
        if transport == "commands"
        else _ledger(
            Path(record["spend_directory"]),
            plan["cells"],
            cohort=cohort,
        )
    )
    return plan, ledger


def _config(root: Path, cell: dict[str, Any], transport: str) -> dict[str, Any]:
    config = teams._trial_config(root, cell)
    config["agent_timeout_multiplier"] = 3.0
    for p in config["participants"]:
        p.update(
            import_path=audit._agent_import(cell["cell_id"]),
            model_name=native.DECLARED_MODEL if transport == "direct" else None,
            kwargs={
                "cell_id": cell["cell_id"],
                "transport": transport,
                "command_config": str(root / "commands.json")
                if transport == "commands"
                else None,
            },
        )
    return config


def _reserved(ledger: dict[str, Any] | None, cell: dict[str, Any]) -> bool:
    return ledger is not None and any(
        r["run"].startswith(cell["cell_id"] + "-") for r in ledger["calls"]
    )


def _observed(
    root: Path, cell: dict[str, Any], transport: str, ledger: dict[str, Any] | None
) -> dict[str, Any]:
    directory = teams._cell_dir(root, cell)
    row = dict(cell, state="unclaimed", outcome=None)
    if not os.path.lexists(directory):
        if _reserved(ledger, cell):
            row["state"] = "reserved_without_trial"
        return row
    teams._check_path(directory, 0o700, directory=True)
    row["state"] = "claimed"
    config = _config(root, cell, transport)
    if not (directory / "outcome.json").exists():
        return row
    if teams._read_sealed(directory / "attempt.json") != teams._bytes(
        cell
    ) or teams._read_sealed(directory / "config.json") != teams._bytes(config):
        raise ValueError("cell claim or native configuration changed")
    task = directory / "dispatch-handoff"
    if teams._tree(task, directory_mode=0o755) != set(panel.TASK_FILES):
        raise ValueError("native task inputs differ from the registered file set")
    for name in panel.TASK_FILES:
        path = task / name
        teams._check_path(path, 0o755 if path.suffix == ".sh" else 0o644)
        expected = (
            teams._bytes(
                {
                    "schema": "seatlab.scenario/v1",
                    "seed": cell["seed"],
                    "condition": cell["condition"],
                    "episodes": 6,
                }
            )
            if name == panel.SCENARIO
            else (panel.EXAMPLES_DIR / panel._task_source(name)).read_bytes()
        )
        if path.read_bytes() != expected:
            raise ValueError("native task input differs from its registered content")
    exit_record = teams._outcome(directory / "outcome.json")
    if exit_record["returncode"] != 0:
        row["state"] = "native_failed"
        return row
    trial = teams._trial_dir(root, cell)
    try:
        result = audit.inspect_trial(trial, config, cell, transport)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        row["state"] = "unverified"
        return row
    row.update(state="complete", outcome=result)
    return row


def _commands_trial(directory: Path, harbor: Path, env: dict[str, str]) -> int:
    if "DEEPSEEK_API_KEY" in env or "SEATLAB_SPEND_DIR" in env:
        raise ValueError("external commands must not receive direct bindings")
    (directory / "trials").mkdir(mode=0o700)
    with (directory / "native.log").open("xb") as stream:
        return subprocess.run(
            [str(harbor), "trial", "start", "-c", str(directory / "config.json")],
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=teams._TRIAL_TIMEOUT_SEC,
            check=False,
            umask=0o077,
        ).returncode


def _run(
    root: Path,
    cell: dict[str, Any],
    plan: dict[str, Any],
    ledger: dict[str, Any] | None,
    harbor: Path,
) -> dict[str, Any]:
    directory = teams._cell_dir(root, cell)
    if os.path.lexists(directory) or _reserved(ledger, cell):
        raise ValueError("a first attempt or reservation already exists")
    if _BY_PLAN[plan["schema"]] is not _ORIGINAL:
        cells = plan["cells"]
        index = cells.index(cell)
        if index % 2:
            first = teams._cell_dir(root, cells[index - 1])
            teams._outcome(first / "outcome.json")
    transport = plan["transport"]
    if transport == "direct":
        record = files._object(teams._read_sealed(root / "register.json"))
        if os.environ.get("SEATLAB_SPEND_DIR") != record["spend_directory"]:
            raise ValueError("use the registered direct ledger")
        native.deepseek.preflight()
    harbor, env = native._preflight(harbor)
    sources = panel._snapshot("arrival")
    if plan["sources"] != {
        name: sha256(body).hexdigest() for name, body in sources.items()
    }:
        raise ValueError("native source changed")
    scenario = panel._make_plan(
        [cell["seed"]],
        [cell["condition"]],
        plan["episodes"],
        "arrival",
        plan["sources"],
    )
    directory.mkdir(mode=0o700)
    teams._seal(directory / "attempt.json", teams._bytes(cell))
    config = _config(root, cell, transport)
    panel._prepare_cell(directory, scenario, cell, sources)
    teams._seal(directory / "config.json", teams._bytes(config))
    code = None
    try:
        launch = native._launch if transport == "direct" else _commands_trial
        code = launch(directory, harbor, env)
    finally:
        teams._seal(
            directory / "outcome.json",
            teams._bytes(
                {
                    "returncode": code,
                    "launch_error": "native_launch_failed" if code is None else None,
                }
            ),
        )
    return _observed(root, cell, transport, ledger)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "seal", "pending", "run", "collect", "report", "verify"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        if mode in ("plan", "seal"):
            sub.add_argument("--cohort", choices=tuple(_COHORTS), default="original")
        if mode == "verify":
            sub.add_argument("report", type=Path)
        elif mode != "plan":
            sub.add_argument("--study", type=Path, required=True)
        if mode == "seal":
            sub.add_argument(
                "--transport", choices=("commands", "direct"), required=True
            )
            sub.add_argument("--config", type=Path)
            sub.add_argument("--spend-dir", type=Path)
        if mode == "run":
            sub.add_argument(
                "--harbor",
                type=Path,
                required=True,
                help="Absolute fork executable; the environment must support native participants.",
            )
        if mode in ("run", "collect"):
            sub.add_argument("--cell", required=True)
    args = cli.parse_args(argv)
    try:
        if args.mode == "plan":
            cells = _COHORTS[args.cohort].grid()
            _print(
                {
                    "cells": list(cells),
                    "episodes": protocol.EPISODES,
                    "max_calls": len(cells) * len(_RUNS),
                }
            )
            return 0
        if args.mode == "verify":
            from ..handoff.external_audit import _report

            item = _report(args.report)
            cohort = _BY_REPORT[item["schema"]]
            cells = list(cohort.grid())
            if cohort is not _ORIGINAL:
                plan = item["plan"]
                expected = _plan(
                    plan["transport"], plan["command_config_sha256"], cohort=cohort
                )
                if teams._bytes(expected) != teams._bytes(plan):
                    raise ValueError("native cohort sources or register changed")
            elif item["plan"]["cells"] != cells:
                raise ValueError("unexpected public register")
            summary = audit.summarize(
                cells,
                item["rows"],
                confirmation_cohort=cohort is _CONFIRMATION,
                ranked_cohort=cohort is _RANKED,
            )
            if teams._bytes(summary) != teams._bytes(item["summary"]):
                raise ValueError("published reward summary differs")
            _print(summary)
            return 0
        root = _checked_study(args.study)
        if args.mode == "seal":
            _register(root, args)
            return 0
        plan, ledger = _load(root)
        cells = plan["cells"]
        cohort = _BY_PLAN[plan["schema"]]
        if args.mode in ("run", "collect"):
            cell = next((c for c in cells if c["cell_id"] == args.cell), None)
            if cell is None:
                raise ValueError("unknown cell")
            row = (
                _run(root, cell, plan, ledger, args.harbor)
                if args.mode == "run"
                else _observed(root, cell, plan["transport"], ledger)
            )
            _print(row)
            return 0 if row["state"] == "complete" else 1
        if args.mode == "pending":
            _print(
                {
                    "cells": [
                        c["cell_id"]
                        for c in cells
                        if not os.path.lexists(teams._cell_dir(root, c))
                        and not _reserved(ledger, c)
                    ]
                }
            )
            return 0
        rows = [_observed(root, c, plan["transport"], ledger) for c in cells]
        report = {
            "schema": cohort.report_schema,
            "plan": plan,
            "rows": rows,
            "summary": audit.summarize(
                cells,
                rows,
                confirmation_cohort=cohort is _CONFIRMATION,
                ranked_cohort=cohort is _RANKED,
            ),
        }
        if ledger is not None:
            report["meter"] = _meter(
                [r["outcome"] for r in rows if r["outcome"] is not None], ledger
            )
        _print(report)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ):
        print(
            "Cannot register, launch, or verify this first-attempt study. Keep existing claims closed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
