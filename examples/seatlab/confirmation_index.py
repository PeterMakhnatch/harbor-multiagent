from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from . import confirmation_custody as custody
from . import confirmation_summary as summary
from .confirmation_custody import _decode, _need, _object, _physical

_SCHEMAS = {
    "notices": "seatlab.native-attention-confirmation-report/v1",
    "calendar_answers": "seatlab.native-calendar-answers/v1",
    "dispatch_answers": "seatlab.native-dispatch_answers-report/v1",
}
_TEAM_CONDITIONS = {"notices": "reordered", "dispatch_answers": "stale"}
_ABSENT = {"unclaimed", "reserved_without_trial"}
_CLAIMED = {"claimed", "native_failed", "unverified", "complete"}

type _Registered = dict[str, tuple[int, str, tuple[custody._Turn, ...]]]


def _read(path: Path) -> dict[str, Any]:
    _physical(path, directory=False)
    return _decode(path.read_bytes())


def _register(panel: str) -> _Registered:
    worlds, control, treatment, _ = summary._STUDIES[panel]
    cells = {}
    for world in worlds:
        for arm in (control, treatment):
            name, turns = custody._layout(panel, world, arm)
            cells[name] = (world, arm, turns)
    return cells


def _index(
    panel: str, records: object, registered: _Registered
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list) or len(records) != len(registered):
        raise ValueError(f"{panel}: wrong cell count")
    calendar = panel == "calendar_answers"
    indexed = {}
    for record in records:
        row = _object(record, f"{panel}: expected an audit cell")
        name = row["run" if calendar else "cell_id"]
        _need(
            isinstance(name, str) and name in registered and name not in indexed,
            f"{panel}: unexpected or duplicate cell ID",
        )
        world, arm, turns = registered[name]
        _need(
            row["arm"] == arm and row.get("control", False) is False,
            f"{name}: wrong arm or control cell",
        )
        if calendar:
            _need(
                row["control"] is False
                and row["schema"] == _SCHEMAS[panel]
                and row["world"] == f"w{world:02d}"
                and row["final_receiver"] == turns[0].seat
                and row["peer"] == turns[1].seat
                and row["first_arm"] == ("visible" if world % 2 == 0 else "withheld"),
                f"{name}: wrong calendar cell binding",
            )
        else:
            _need(
                type(row["seed"]) is int
                and row["seed"] == world
                and type(row["repetition"]) is int
                and row["repetition"] == 0
                and row["condition"] == _TEAM_CONDITIONS[panel]
                and row["pairing"] == "MM",
                f"{name}: wrong team cell binding",
            )
        indexed[name] = row
    return indexed


def _trial(directory: Path, name: str, panel: str, config_hash: object) -> Path | None:
    trials = directory / "trials"
    has_trials = _physical(trials, directory=True, missing_ok=True)
    config_path = directory / "config.json"
    if not _physical(config_path, directory=False, missing_ok=True):
        _need(not has_trials and config_hash is None, f"{name}: missing native config")
        return None
    payload = config_path.read_bytes()
    if config_hash is not None:
        _need(
            sha256(payload).hexdigest() == config_hash,
            f"{name}: audit config hash mismatch",
        )
    config = _decode(payload)
    _need(
        config["trials_dir"] == str(trials),
        f"{name}: wrong configured trials directory",
    )
    trial_name = config["trial_name"]
    _need(
        isinstance(trial_name, str)
        and trial_name not in ("", ".", "..")
        and Path(trial_name).name == trial_name,
        f"{name}: invalid configured trial name",
    )
    trial = trials / trial_name
    if not has_trials:
        return None
    for entry in trials.iterdir():
        _need(entry == trial, f"{name}: unexpected trial directory entry")
    if not _physical(trial, directory=True, missing_ok=True):
        return None
    custody._trial_location(trial, name, panel)
    return trial


def _verify_turns(
    panel: str, value: object, turns: tuple[custody._Turn, ...], name: str
) -> None:
    problem = f"{name}: wrong audited turn roster"
    if panel == "calendar_answers":
        reports = _object(value, problem)
        _need(reports.keys() == {turn.phase for turn in turns}, problem)
        for turn in turns:
            row = _object(reports[turn.phase], problem)
            ledger = _object(row["ledger"], problem)
            _need(
                (
                    row["seat"],
                    row["ledger_role"],
                    row["status"],
                    ledger["run"],
                    ledger["role"],
                )
                == (turn.seat, turn.role, "accepted", turn.run, turn.role),
                problem,
            )
    else:
        if not isinstance(value, list) or len(value) != len(turns):
            raise ValueError(problem)
        for record, turn in zip(value, turns, strict=True):
            row = _object(record, problem)
            _need(
                (row["stage"], row["run"], row["role"], row["status"])
                == (turn.phase, turn.run, turn.role, "received"),
                problem,
            )


def _native(
    panel: str,
    trial: Path,
    audited_id: object,
    audited_reward: object,
    result_hash: object,
    complete: bool,
    seen: set[str],
) -> None:
    path = trial / "result.json"
    if not _physical(path, directory=False, missing_ok=True):
        _need(
            not complete
            and audited_id is None
            and audited_reward is None
            and result_hash is None,
            "audit identifies a missing native result",
        )
        return
    payload = path.read_bytes()
    if result_hash is not None:
        _need(sha256(payload).hexdigest() == result_hash, "audit result hash mismatch")
    raw = _decode(payload)
    config = _object(raw["config"], "invalid native config")
    _need(
        raw["trial_uri"] == trial.as_uri()
        and raw["trial_name"] == config["trial_name"] == trial.name
        and config["trials_dir"] == str(trial.parent),
        "native result belongs to a different trial directory",
    )
    identifier = raw["id"]
    _need(isinstance(identifier, str), "missing native trial UUID")
    parsed = UUID(identifier)
    _need(
        parsed.version == 4 and str(parsed) == identifier and identifier not in seen,
        "invalid or duplicate native trial UUID",
    )
    seen.add(identifier)
    if complete or audited_id is not None:
        expected = (
            str(uuid5(NAMESPACE_URL, f"{_SCHEMAS[panel]}:{identifier}"))
            if panel == "notices"
            else identifier
        )
        _need(audited_id == expected, "audit identifies a different native trial")
    if complete or audited_reward is not None:
        reward = custody._reward(raw, audited_reward, trial, panel)
        _need(
            not complete or reward is not None,
            "complete audit lacks a valid native reward",
        )


def _row(
    panel: str,
    directory: Path,
    row: dict[str, Any],
    world: int,
    arm: str,
    turns: tuple[custody._Turn, ...],
    seen: set[str],
) -> dict[str, Any]:
    name = directory.name
    exists = _physical(directory, directory=True, missing_ok=True)
    config_hash = result_hash = None
    if panel == "calendar_answers":
        complete = row["protocol_complete"]
        _need(
            row["attempted"] is exists
            and type(complete) is bool
            and isinstance(row["status"], str)
            and complete == (row["status"] == "complete")
            and exists == (row["status"] != "not_attempted"),
            f"{name}: calendar attempt status does not match study",
        )
        hashes = _object(row["artifact_sha256"], f"{name}: invalid artifact hashes")
        config_hash, result_hash = hashes.get("config"), hashes.get("result")
        if complete:
            _need(
                row["protocol_bound"] is True
                and row["native_reward_valid"] is True
                and isinstance(config_hash, str)
                and isinstance(result_hash, str),
                f"{name}: incomplete binding in complete audit",
            )
        for file in ("attempt", "outcome"):
            if file in hashes:
                path = directory / f"{file}.json"
                _physical(path, directory=False)
                _need(
                    sha256(path.read_bytes()).hexdigest() == hashes[file],
                    f"{name}: audit {file} hash mismatch",
                )
        identity, reward, receipts = (
            row["trial_uuid"],
            row["native_reward"],
            row["turns"],
        )
    else:
        state, outcome = row["state"], row["outcome"]
        _need(
            state in (_CLAIMED if exists else _ABSENT),
            f"{name}: team attempt status does not match study",
        )
        complete = state == "complete"
        _need(
            complete == (outcome is not None),
            f"{name}: team outcome does not match state",
        )
        identity = reward = receipts = None
        if complete:
            outcome = _object(outcome, f"{name}: invalid audited outcome")
            identity = outcome["trial_alias" if panel == "notices" else "trial_id"]
            reward, receipts = outcome["native_score"], outcome["turns"]
    trial = _trial(directory, name, panel, config_hash) if exists else None
    if complete:
        _verify_turns(panel, receipts, turns, name)
    if trial is None:
        _need(
            not complete
            and identity is None
            and reward is None
            and result_hash is None
            and (exists or config_hash is None),
            f"{name}: missing audited trial directory",
        )
    else:
        _native(panel, trial, identity, reward, result_hash, complete, seen)
    return {
        "world": world,
        "arm": arm,
        "trial_dir": None if trial is None else str(trial),
        "behavior_confirmed": complete,
        "calls": [[turn.run, turn.role] for turn in turns],
    }


def _panel(
    panel: str, root: Path, report: Path, seen: set[str]
) -> list[dict[str, Any]]:
    _physical(root, directory=True)
    document = _read(report)
    _need(document["schema"] == _SCHEMAS[panel], f"{panel}: wrong audit schema")
    registered = _register(panel)
    calendar = panel == "calendar_answers"
    if not calendar:
        plan = _object(document["plan"], f"{panel}: invalid audit plan")
        _need(
            plan["schema"] == _SCHEMAS[panel].replace("-report/", "-plan/")
            and plan["transport"] == "direct"
            and plan["command_config_sha256"] is None
            and plan == _read(root / "register.json")["plan"],
            f"{panel}: audit plan does not match study register",
        )
        _index(panel, plan["cells"], registered)
    rows = _index(panel, document["cells" if calendar else "rows"], registered)
    cells = root / "cells"
    if _physical(cells, directory=True, missing_ok=calendar):
        for entry in cells.iterdir():
            _need(entry.name in registered, f"{panel}: unexpected study cell directory")
    return [
        _row(panel, cells / name, rows[name], world, arm, turns, seen)
        for name, (world, arm, turns) in registered.items()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read three registered studies and print a PRIVATE custody index to stdout.",
        epilog="Run with python3 -B -m examples.seatlab.confirmation_index. "
        "Use absolute, physical paths and stable study roots. Keep stdout private.",
        allow_abbrev=False,
    )
    for panel, command in (
        ("notices", "attention report (confirmation)"),
        ("calendar_answers", "live_answers read (without --controls)"),
        ("dispatch_answers", "dispatch_answers report"),
    ):
        parser.add_argument(
            "--" + panel.replace("_", "-"),
            action="append",
            nargs=2,
            required=True,
            metavar=("ROOT", "REPORT"),
            type=Path,
            help=f"study root and private JSON from {command}",
        )
    args = parser.parse_args(argv)
    pairs = [getattr(args, panel) for panel in _SCHEMAS]
    if any(len(pair) != 1 for pair in pairs):
        parser.error("provide each study and report pair exactly once")
    paths = [path for pair in pairs for path in pair[0]]
    if len(set(paths)) != len(paths):
        parser.error("study roots and report files must be distinct")
    seen: set[str] = set()
    try:
        panels = {
            panel: _panel(panel, *pair[0], seen)
            for panel, pair in zip(_SCHEMAS, pairs, strict=True)
        }
    except (OSError, KeyError, TypeError, ValueError, RuntimeError):
        parser.exit(
            2, "confirmation_index: invalid private study, audit, or native evidence\n"
        )
    print(
        json.dumps(
            {"schema": custody._PRIVATE, "panels": panels}, indent=2, allow_nan=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
