from __future__ import annotations

import argparse
import os
import re
import runpy
import stat
import sys
from pathlib import Path

from ..market_handoff import decisions
from ..visibility import calendar_driver
from . import domain

ACTOR_IMPORT = "examples.seatlab.report_budget.actor:BudgetSeat"
SEATS = ("sales", "finance")
TRIAL = re.compile(r"budget-w([0-9]{2})-t([0-9]{3})")
HOST_NAMES = ("HOME", "PATH", "LANG", "TMPDIR")
PARTICIPANT_KEYS = frozenset({"seat", "name", "import_path", "model_name", "kwargs"})
LIVE_KWARG_KEYS = frozenset({"client", "owner", "control", "target"})
CONTROL_KWARG_KEYS = frozenset({"owner", "control", "target"})


def _require(test: bool, why: str) -> None:
    if not test:
        raise ValueError(why)


def _host_path(value: object, why: str) -> Path:
    _require(
        isinstance(value, str)
        and Path(value).is_absolute()
        and os.path.normpath(value) == value,
        why,
    )
    assert isinstance(value, str)
    return Path(value)


def _private_dir(path: Path, why: str) -> None:
    info = path.lstat()
    _require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) & 0o077 == 0,
        why,
    )


def _record(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    record: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        record[key] = item
    return record


def _participant(raw: object) -> tuple[str, Path | None, Path, bool, int]:
    record = _record(raw)
    if record is None:
        raise TypeError("participant must be an object")
    _require(
        PARTICIPANT_KEYS <= record.keys()
        and not any(record[key] for key in record.keys() - PARTICIPANT_KEYS),
        "participant may not set env, endpoints, tools or other overrides",
    )
    seat, kwargs = record["seat"], _record(record["kwargs"])
    control = None if kwargs is None else kwargs.get("control")
    target = None if kwargs is None else kwargs.get("target")
    if (
        not isinstance(seat, str)
        or seat not in SEATS
        or record["import_path"] != ACTOR_IMPORT
        or record["name"] is not None
        or record["model_name"] is not None
        or kwargs is None
        or not isinstance(control, bool)
        or type(target) is not int
        or target not in domain.TARGETS
        or kwargs.keys() != (CONTROL_KWARG_KEYS if control else LIVE_KWARG_KEYS)
    ):
        raise ValueError("participant is not a registered report budget seat")
    client = (
        None
        if control
        else _host_path(kwargs["client"], "client must be an absolute host path")
    )
    owner = _host_path(kwargs["owner"], "owner must be an absolute host path")
    return seat, client, owner, control, target


def _trial(out: Path, config: dict[str, object]) -> tuple[int, int]:
    name = config.get("trial_name")
    match = TRIAL.fullmatch(name) if isinstance(name, str) else None
    _require(match is not None, "not a registered report budget trial")
    assert match is not None
    world, target = int(match[1]), int(match[2])
    _require(0 <= world <= domain.CONTROL_WORLD, "world outside registered companies")
    _require(target in domain.TARGETS, "target outside the registered arms")
    trials = out / "trials"
    _require(config.get("trials_dir") == str(trials), "trials must go to the stage")
    _require(not os.path.lexists(trials), "this stage already has a native attempt")
    task = config.get("task")
    _require(isinstance(task, dict), "missing task")
    assert isinstance(task, dict)
    task_path = _host_path(task.get("path"), "task must be an absolute local path")
    _require(
        not task_path.is_relative_to(out / "private"),
        "task must stay outside owner roots",
    )
    return world, target


def checked_config(config_path: Path) -> bool:
    out = config_path.parent
    _private_dir(out, "stage root must be a private directory owned by this user")
    config = decisions.decode(decisions.read_text(config_path))
    _require(isinstance(config, dict), "not a staged report budget trial")
    assert isinstance(config, dict)
    world, target = _trial(out, config)
    participants = config.get("participants")
    _require(
        isinstance(participants, list) and len(participants) == len(SEATS),
        "two seats",
    )
    assert isinstance(participants, list)
    rows = [_participant(raw) for raw in participants]
    _require(
        sorted(row[0] for row in rows) == sorted(SEATS),
        "seats must be sales and finance",
    )
    clients = {row[1] for row in rows}
    controls = {row[3] for row in rows}
    _require(
        len(clients) == 1 and len(controls) == 1, "one client and one mode per trial"
    )
    _require(all(row[4] == target for row in rows), "seat target differs from trial")
    _require(
        all(row[2] == out / "private" / row[0] for row in rows),
        "each seat owns exactly <stage>/private/<seat>",
    )
    _private_dir(out / "private", "owner parent must be private")
    for _, _, owner, _, _ in rows:
        _require(
            not os.path.lexists(owner),
            "owner root must be fresh",
        )
    control = controls.pop()
    _require(
        (world == domain.CONTROL_WORLD) == control,
        "control runs only on the non-study company",
    )
    if not control:
        client = clients.pop()
        assert client is not None
        _require(
            client.is_file() and os.access(client, os.X_OK),
            "missing trusted model client",
        )
    return control


def _isolate(out: Path, env: dict[str, str]) -> None:
    root = Path(__file__).resolve().parents[3]
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env["PYTHONPATH"]
    os.environ.clear()
    os.environ.update(env)
    sys.path[:0] = env["PYTHONPATH"].split(os.pathsep)
    os.chdir(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one staged report budget trial in local Harbor."
    )
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    try:
        _require(args.config.is_absolute(), "expected an absolute config path")
        config_path = args.config.resolve(strict=True)
        _require(config_path.name == "config.json", "expected a staged config.json")
        control = checked_config(config_path)
        harbor = calendar_driver._entrypoint(Path(sys.executable).parent / "harbor")
        from ..market_handoff import timing_seat
        from . import actor

        _require(
            ACTOR_IMPORT == f"{actor.__name__}:{actor.BudgetSeat.__name__}",
            "actor import differs",
        )
        _require(HOST_NAMES == timing_seat.HOST_KEYS, "host allowlist differs")
        host = {k: os.environ[k] for k in HOST_NAMES if os.environ.get(k)}
        env = calendar_driver._native_env(config_path.parent)
        if not control:
            timing_seat.bind_host(host)
        host.clear()
        _isolate(config_path.parent, env)
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print(f"report budget trial: {error}", file=sys.stderr)
        return 2
    sys.argv = [str(harbor), "trial", "start", "-c", str(config_path)]
    runpy.run_path(str(harbor), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
