import argparse
import os
import re
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import run_panel as panel
import team_driver as teams

from .handoff.__main__ import _meter, _print
from .visibility import clarify_run as files
from .visibility import native_pairs as native
from .visibility import spend
from .visibility.seat_command import load_commands

_EPISODES = 6

type Record = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Experiment:
    tag: str
    grid: Callable[[], Iterable[Record]]
    stage_roles: tuple[tuple[str, str], ...]
    reservation_prefix: str
    agent_import: str
    seat_sources: tuple[str, ...]
    inspect_trial: Callable[[Path, Record, Record, str], Record]
    summarize: Callable[[list[Record], list[Record]], Record]
    channel_kwarg: str | None = None
    shared_seat_sources: tuple[str, ...] = ()

    def schema(self, artifact: str) -> str:
        return f"seatlab.native-{self.tag}-{artifact}/v1"

    def max_calls(self, cells: list[Record]) -> int:
        return len(cells) * len(self.stage_roles)


def _stamps(sources: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256(body).hexdigest() for name, body in sources.items()}


def _checked_study(path: Path) -> Path:
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("use a physical absolute path for the study")
    return teams._root(path)


def _source_git(root: Path, env: dict[str, str], *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "--git-dir=.git", *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise ValueError("historical seat sources are unavailable") from None


def _seat_source_bytes(
    spec: Experiment, source_revision: str | None
) -> dict[str, bytes]:
    seatlab_dir = Path(__file__).resolve().parent
    paths = {
        **{name: seatlab_dir / spec.tag / name for name in spec.seat_sources},
        **{f"shared/{name}": seatlab_dir / name for name in spec.shared_seat_sources},
    }
    if source_revision is None:
        return {name: path.read_bytes() for name, path in paths.items()}
    if re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
        raise ValueError("source revision must be a full lowercase commit ID")
    root = seatlab_dir.parent.parent
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_ALLOW_PROTOCOL="",
        GIT_NO_LAZY_FETCH="1",
        GIT_NO_REPLACE_OBJECTS="1",
    )
    if (
        _source_git(root, env, "rev-parse", "--show-object-format") != b"sha1\n"
        or _source_git(root, env, "cat-file", "-t", source_revision) != b"commit\n"
    ):
        raise ValueError("source revision must identify a local SHA-1 commit")
    sources: dict[str, bytes] = {}
    for name, path in paths.items():
        ref = f"{source_revision}:{path.relative_to(root).as_posix()}"
        if _source_git(root, env, "cat-file", "-t", ref) != b"blob\n":
            raise ValueError("historical seat source is not a blob")
        sources[name] = _source_git(root, env, "cat-file", "blob", ref)
    return sources


def _plan(
    spec: Experiment,
    cells: list[Record],
    transport: str,
    config_sha: str | None,
    *,
    source_revision: str | None = None,
) -> Record:
    return {
        "schema": spec.schema("plan"),
        "transport": transport,
        "command_config_sha256": config_sha,
        "episodes": _EPISODES,
        "max_direct_calls": spec.max_calls(cells) if transport == "direct" else 0,
        "sources": _stamps(panel._snapshot("arrival")),
        "seat_sources": _stamps(_seat_source_bytes(spec, source_revision)),
        "cells": cells,
    }


def _ledger(spec: Experiment, path: Path, cells: list[Record]) -> Record:
    result = spend.report(path, run_prefix=spec.reservation_prefix)
    keys = {
        (f"{cell['cell_id']}-{stage}", role)
        for cell in cells
        for stage, role in spec.stage_roles
    }
    if any((r["run"], r["role"]) not in keys for r in result["calls"]):
        raise ValueError("unregistered direct reservation")
    return result


def _register(
    spec: Experiment, cells: list[Record], root: Path, args: argparse.Namespace
) -> None:
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
    plan = _plan(
        spec, cells, args.transport, None if body is None else sha256(body).hexdigest()
    )
    if directory is not None and _ledger(spec, directory, cells)["calls"]:
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


def _load(
    spec: Experiment,
    cells: list[Record],
    root: Path,
    *,
    source_revision: str | None = None,
) -> tuple[Record, Record | None]:
    teams._check_path(root, 0o700, directory=True)
    teams._check_path(root / "cells", 0o700, directory=True)
    record = files._object(teams._read_sealed(root / "register.json"))
    registered_plan: Record = record["plan"]
    transport = registered_plan["transport"]
    spend_directory = None
    if transport == "commands":
        digest = sha256(teams._read_sealed(root / "commands.json")).hexdigest()
        if record["spend_directory"] is not None:
            raise ValueError("command study has no direct spend directory")
    elif transport == "direct":
        digest = None
        spend_directory = Path(record["spend_directory"])
        if (
            not spend_directory.is_absolute()
            or spend_directory != spend_directory.resolve(strict=True)
        ):
            raise ValueError("original direct spend directory unavailable")
    else:
        raise ValueError("unsupported transport")
    expected_plan = _plan(
        spec, cells, transport, digest, source_revision=source_revision
    )
    if teams._bytes(registered_plan) != teams._bytes(expected_plan):
        raise ValueError("registered sources or cells changed")
    keys = {cell["cell_id"] for cell in cells}
    with os.scandir(root / "cells") as entries:
        if any(
            entry.name not in keys or not entry.is_dir(follow_symlinks=False)
            for entry in entries
        ):
            raise ValueError("unexpected native trial claim")
    ledger = None if spend_directory is None else _ledger(spec, spend_directory, cells)
    return registered_plan, ledger


def _config(spec: Experiment, root: Path, cell: Record, transport: str) -> Record:
    config = teams._trial_config(root, cast(teams.Cell, cell))
    config["agent_timeout_multiplier"] = 3.0
    for participant in config["participants"]:
        kwargs = {
            "cell_id": cell["cell_id"],
            "transport": transport,
            "command_config": str(root / "commands.json")
            if transport == "commands"
            else None,
        }
        if spec.channel_kwarg is not None:
            kwargs[spec.channel_kwarg] = config["trial_name"]
        participant.update(
            import_path=spec.agent_import,
            model_name=native.DECLARED_MODEL if transport == "direct" else None,
            kwargs=kwargs,
        )
    return config


def _cell_dir(root: Path, cell: Record) -> Path:
    return teams._cell_dir(root, cast(teams.Cell, cell))


def _reserved(ledger: Record | None, cell: Record) -> bool:
    return ledger is not None and any(
        reservation["run"].startswith(cell["cell_id"] + "-")
        for reservation in ledger["calls"]
    )


def _check_task(task: Path, cell: Record, plan: Record) -> None:
    if teams._tree(task, directory_mode=0o755) != set(panel.TASK_FILES):
        raise ValueError("native task inputs differ from the registered file set")
    scenario = teams._bytes(
        {
            "schema": "seatlab.scenario/v1",
            "seed": cell["seed"],
            "condition": cell["condition"],
            "episodes": plan["episodes"],
        }
    )
    for name in panel.TASK_FILES:
        path = task / name
        teams._check_path(path, 0o755 if path.suffix == ".sh" else 0o644)
        body = path.read_bytes()
        matches = (
            body == scenario
            if name == panel.SCENARIO
            else sha256(body).hexdigest() == plan["sources"][panel._task_source(name)]
        )
        if not matches:
            raise ValueError("native task input differs from its registered content")


def _observed(
    spec: Experiment, root: Path, cell: Record, plan: Record, ledger: Record | None
) -> Record:
    directory = _cell_dir(root, cell)
    row: Record = dict(cell, state="unclaimed", outcome=None)
    if not os.path.lexists(directory):
        if _reserved(ledger, cell):
            row["state"] = "reserved_without_trial"
        return row
    teams._check_path(directory, 0o700, directory=True)
    row["state"] = "claimed"
    if not (directory / "outcome.json").exists():
        return row
    transport = plan["transport"]
    config = _config(spec, root, cell, transport)
    if teams._read_sealed(directory / "attempt.json") != teams._bytes(
        cell
    ) or teams._read_sealed(directory / "config.json") != teams._bytes(config):
        raise ValueError("cell claim or native configuration changed")
    _check_task(directory / "dispatch-handoff", cell, plan)
    exit_record = teams._outcome(directory / "outcome.json")
    if exit_record["returncode"] != 0:
        row["state"] = "native_failed"
        return row
    trial = teams._trial_dir(root, cast(teams.Cell, cell))
    try:
        result = spec.inspect_trial(trial, config, cell, transport)
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
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
    spec: Experiment,
    root: Path,
    cell: Record,
    plan: Record,
    ledger: Record | None,
    harbor: Path,
) -> Record:
    directory = _cell_dir(root, cell)
    if os.path.lexists(directory) or _reserved(ledger, cell):
        raise ValueError("a first attempt or reservation already exists")
    transport = plan["transport"]
    if transport == "direct":
        record = files._object(teams._read_sealed(root / "register.json"))
        if os.environ.get("SEATLAB_SPEND_DIR") != record["spend_directory"]:
            raise ValueError("use the registered direct ledger")
        native.deepseek.preflight()
    harbor, env = native._preflight(harbor)
    sources = panel._snapshot("arrival")
    if plan["sources"] != _stamps(sources):
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
    config = _config(spec, root, cell, transport)
    panel._prepare_cell(directory, scenario, cast(panel.Cell, cell), sources)
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
    return _observed(spec, root, cell, plan, ledger)


def _verify(spec: Experiment, cells: list[Record], path: Path) -> None:
    from .handoff.external_audit import _report

    report = _report(path)
    if report["schema"] != spec.schema("report") or report["plan"]["cells"] != cells:
        raise ValueError("unexpected public register")
    rows = report["rows"]
    if not isinstance(rows, list) or len(rows) != len(cells):
        raise ValueError("unexpected public outcome count")
    summary = spec.summarize(cells, rows)
    if teams._bytes(summary) != teams._bytes(report["summary"]):
        raise ValueError("published reward summary differs")
    _print(summary)


def _show_report(
    spec: Experiment,
    root: Path,
    plan: Record,
    cells: list[Record],
    ledger: Record | None,
) -> None:
    rows = [_observed(spec, root, cell, plan, ledger) for cell in cells]
    report = {
        "schema": spec.schema("report"),
        "plan": plan,
        "rows": rows,
        "summary": spec.summarize(cells, rows),
    }
    if ledger is not None:
        report["meter"] = _meter(
            [row["outcome"] for row in rows if row["outcome"] is not None], ledger
        )
    _print(report)


def _dispatch(spec: Experiment, args: argparse.Namespace) -> int:
    cells = list(spec.grid())
    if args.mode == "plan":
        _print(
            {"cells": cells, "episodes": _EPISODES, "max_calls": spec.max_calls(cells)}
        )
        return 0
    if args.mode == "verify":
        _verify(spec, cells, args.report)
        return 0
    root = _checked_study(args.study)
    if args.mode == "seal":
        _register(spec, cells, root, args)
        return 0
    source_revision = (
        getattr(args, "source_revision", None)
        if args.mode in ("collect", "report")
        else None
    )
    plan, ledger = _load(spec, cells, root, source_revision=source_revision)
    if args.mode in ("run", "collect"):
        cell = next((cell for cell in cells if cell["cell_id"] == args.cell), None)
        if cell is None:
            raise ValueError("unknown cell")
        row = (
            _run(spec, root, cell, plan, ledger, args.harbor)
            if args.mode == "run"
            else _observed(spec, root, cell, plan, ledger)
        )
        _print(row)
        return 0 if row["state"] == "complete" else 1
    if args.mode == "pending":
        _print(
            {
                "cells": [
                    cell["cell_id"]
                    for cell in cells
                    if not os.path.lexists(_cell_dir(root, cell))
                    and not _reserved(ledger, cell)
                ]
            }
        )
        return 0
    _show_report(spec, root, plan, cells, ledger)
    return 0


def main(spec: Experiment, argv: list[str] | None = None, *, description: str) -> int:
    cli = argparse.ArgumentParser(description=description, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "seal", "pending", "run", "collect", "report", "verify"):
        sub = modes.add_parser(mode, allow_abbrev=False)
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
        if mode in ("collect", "report"):
            sub.add_argument(
                "--source-revision",
                help="Full lowercase commit ID for the registered seat source files.",
            )
    args = cli.parse_args(argv)
    try:
        return _dispatch(spec, args)
    except (
        OSError,
        KeyError,
        TypeError,
        IndexError,
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
