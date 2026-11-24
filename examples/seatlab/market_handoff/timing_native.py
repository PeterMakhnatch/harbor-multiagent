from __future__ import annotations

import argparse
import os
import re
import runpy
import stat
import sys
from pathlib import Path

from ..visibility import calendar_driver
from . import decisions

ACTOR_IMPORT = "examples.seatlab.market_handoff.timing_actor:TimingSeat"
SEATS = ("sales", "finance_a", "finance_b")
TRIAL = re.compile(r"price-w([0-9]{2})-r([0-9]+)")
CONTROL_WORLD = 8
HOST_NAMES = ("HOME", "PATH", "LANG", "TMPDIR")
PARTICIPANT_KEYS = frozenset({"seat", "name", "import_path", "model_name", "kwargs"})
KWARG_KEYS = frozenset({"client", "owner", "control"})


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


def _private_root(out: Path) -> None:
    info = out.lstat()
    _require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) & 0o077 == 0,
        "stage root must be a private directory owned by this user",
    )


def _participant(raw: object) -> tuple[str, Path, Path, bool]:
    _require(isinstance(raw, dict), "participant must be an object")
    assert isinstance(raw, dict)
    _require(
        PARTICIPANT_KEYS <= raw.keys()
        and not any(raw[key] for key in raw.keys() - PARTICIPANT_KEYS),
        "participant may not set env, endpoints, tools or other overrides",
    )
    kwargs = raw["kwargs"]
    _require(
        raw["seat"] in SEATS
        and raw["import_path"] == ACTOR_IMPORT
        and raw["name"] is None
        and raw["model_name"] is None
        and isinstance(kwargs, dict)
        and kwargs.keys() == KWARG_KEYS
        and type(kwargs["control"]) is bool,
        "participant is not a registered price timing seat",
    )
    client = _host_path(kwargs["client"], "client must be an absolute host path")
    owner = _host_path(kwargs["owner"], "owner must be an absolute host path")
    return raw["seat"], client, owner, kwargs["control"]


def checked_config(config_path: Path) -> bool:
    out = config_path.parent
    _private_root(out)
    config = decisions.decode(decisions.read_text(config_path))
    _require(isinstance(config, dict), "not a staged price timing trial")
    name = config.get("trial_name")
    match = TRIAL.fullmatch(name) if isinstance(name, str) else None
    _require(match is not None, "not a registered price timing trial")
    assert match is not None
    world = int(match[1])
    _require(0 <= world <= CONTROL_WORLD, "world is outside the registered companies")
    trials = out / "trials"
    _require(
        config.get("trials_dir") == str(trials), "trials must go to the stage root"
    )
    _require(not os.path.lexists(trials), "this stage already has a native attempt")
    task = config.get("task")
    _require(isinstance(task, dict), "missing task")
    assert isinstance(task, dict)
    task_path = _host_path(task.get("path"), "task must be an absolute local path")
    participants = config.get("participants")
    _require(
        isinstance(participants, list) and len(participants) == len(SEATS),
        "three seats",
    )
    assert isinstance(participants, list)
    rows = [_participant(raw) for raw in participants]
    _require(
        sorted(row[0] for row in rows) == sorted(SEATS),
        "seats must be sales and finance",
    )
    clients = {row[1] for row in rows}
    owners = [row[2] for row in rows]
    controls = {row[3] for row in rows}
    _require(
        len(clients) == 1 and len(controls) == 1, "one client and one mode per trial"
    )
    control = controls.pop()
    _require(
        (world == CONTROL_WORLD) == control,
        "control runs only on the non-study company",
    )
    _require(
        not any(a != b and a.is_relative_to(b) for a in owners for b in owners)
        and len(set(owners)) == len(owners),
        "each seat needs its own disjoint owner root",
    )
    guest = (trials, out / "home", out / "tmp", task_path)
    for owner in owners:
        _require(
            not any(owner.is_relative_to(g) or g.is_relative_to(owner) for g in guest),
            "owner roots must stay outside guest, task and trial paths",
        )
        _require(
            not os.path.lexists(owner) or (owner.is_dir() and not owner.is_symlink()),
            "owner root must be a real directory",
        )
    if not control:
        client = clients.pop()
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
        description="Run one staged price timing trial in local Harbor."
    )
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    try:
        config_path = args.config.resolve(strict=True)
        _require(config_path.name == "config.json", "expected a staged config.json")
        control = checked_config(config_path)
        harbor = calendar_driver._entrypoint(Path(sys.executable).parent / "harbor")
        from . import timing_actor, timing_seat

        _require(
            timing_actor.CONTROL_WORLD == CONTROL_WORLD, "actor control company differs"
        )
        host = {k: os.environ[k] for k in HOST_NAMES if os.environ.get(k)}
        env = calendar_driver._native_env(config_path.parent)
        if not control:
            timing_seat.bind_host(host)
        host.clear()
        _isolate(config_path.parent, env)
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print(f"price timing trial: {error}", file=sys.stderr)
        return 2
    sys.argv = [str(harbor), "trial", "start", "-c", str(config_path)]
    runpy.run_path(str(harbor), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
