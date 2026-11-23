from __future__ import annotations

import argparse
import os
import re
import runpy
import sys
from pathlib import Path

from ..visibility import calendar_driver
from . import decisions, native_stage


def checked_stage(out: Path, allow_deepseek: bool,
                  seat_commands: bool = False) -> tuple[Path, str]:
    assert type(allow_deepseek) is bool and type(seat_commands) is bool
    if allow_deepseek and seat_commands:
        raise ValueError("--allow-deepseek and --seat-commands are incompatible")
    config_path = out / "config.json"
    config = decisions.decode(decisions.read_text(config_path))
    if not isinstance(config, dict) or not isinstance(config.get("trial_name"), str):
        raise TypeError("not a staged market trial")
    name = config["trial_name"]
    match = re.fullmatch(r"market-(0[0-9]|1[01])-(own|donor)-(replay|deepseek|command)", name)
    if not match:
        raise ValueError("not a registered market trial")
    mode = match[3]
    if (mode == "deepseek") != allow_deepseek:
        raise ValueError("--allow-deepseek is required only for a paid stage")
    if (mode == "command") != seat_commands:
        raise ValueError("--seat-commands is required only for a command stage")
    picks = None
    if mode == "replay":
        picks = {r["seat"]: r["kwargs"]["pick"] for r in config["participants"]}
        if set(picks) != set(native_stage.ROLES) or any(
            type(pick) is not int or pick not in decisions.PICKS for pick in picks.values()
        ):
            raise ValueError("invalid staged replay picks")
    if config != native_stage.trial_config(out, name, mode, picks):
        raise ValueError("configuration differs from the two-seat market task")
    if (out / "trials").exists():
        raise ValueError("this stage already has a native attempt")
    task = out / "task"
    expected = set(native_stage.TASK_FILES) | {native_stage.SCENARIO, native_stage.PAYOFFS}
    actual = {str(p.relative_to(task)) for p in task.rglob("*") if not p.is_dir()}
    if task.is_symlink() or actual != expected or any(p.is_symlink() for p in task.rglob("*")):
        raise ValueError("unexpected staged task files")
    for file in native_stage.TASK_FILES:
        if (task / file).read_bytes() != native_stage.template_bytes(file):
            raise ValueError(f"staged template differs: {file}")
    return config_path, mode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one staged market trial in local Harbor.")
    parser.add_argument("stage", type=Path)
    parser.add_argument("--allow-deepseek", action="store_true")
    parser.add_argument("--seat-commands", type=Path)
    args = parser.parse_args()
    try:
        out = args.stage.resolve(strict=True)
        config, mode = checked_stage(out, args.allow_deepseek, args.seat_commands is not None)
        harbor = calendar_driver._entrypoint(Path(sys.executable).parent / "harbor")
        if mode == "deepseek":
            from ..visibility import deepseek, spend

            if not all(os.environ.get(k) for k in ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")):
                raise ValueError("direct mode requires a host key and private spend ledger")
            deepseek.bind_host_key()
            spend.bind_host_spend_dir()
        if mode == "command":
            from . import command_host

            command_host.bind(args.seat_commands)
        env = calendar_driver._native_env(out)
        root = Path(__file__).resolve().parents[3]
        env["PYTHONPATH"] = str(root / "src") + os.pathsep + env["PYTHONPATH"]
        os.environ.clear()
        os.environ.update(env)
        sys.path[:0] = env["PYTHONPATH"].split(os.pathsep)
        os.chdir(out)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"market trial: {error}", file=sys.stderr)
        return 2
    sys.argv = [str(harbor), "trial", "start", "-c", str(config)]
    runpy.run_path(str(harbor), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
