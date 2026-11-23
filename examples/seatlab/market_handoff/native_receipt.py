from __future__ import annotations

import argparse
import math
import re
import sys
import tomllib
from pathlib import Path
from typing import Any
from uuid import UUID

from examples.seatlab.market_handoff import decisions, native_stage

SCHEMA = "seatlab.market-native-receipt/v1"
EVIDENCE_SCHEMA = "seatlab.market-native-evidence/v1"
PAYOFF_SCHEMA = "seatlab.market-payoffs/v1"
ROLES = native_stage.ROLES
SCENARIO_KEYS = {"schema", "completed_days", *ROLES}
CONFIG_KEYS = {"schema_version", "task", "trial_name", "trials_dir", "agent_timeout_multiplier",
               "environment", "verifier", "participants"}
PARTICIPANT_KEYS = {"seat", "name", "import_path", "model_name", "kwargs"}
TRIAL = re.compile(r"market-(\d{2})-(own|donor)-(replay|deepseek|command)")
Record = dict[str, Any]


def require(subject: str, checks: dict[str, bool]) -> None:
    bad = [name for name, ok in checks.items() if not ok]
    if bad:
        decisions.fail(f"{subject}: invalid {', '.join(bad)}")


def hex64(value: object) -> bool:
    return isinstance(value, str) and decisions.HEX64.fullmatch(value) is not None


def pick_ok(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in decisions.PICKS


def number_ok(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def record(value: object, subject: str) -> Record:
    if not isinstance(value, dict):
        decisions.fail(f"{subject}: expected a JSON object")
    fields: Record = {key: item for key, item in value.items() if isinstance(key, str)}
    if len(fields) != len(value):
        decisions.fail(f"{subject}: expected string keys")
    return fields


def read_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if not 0 < len(data) <= decisions.MAX_FILE_BYTES:
        decisions.fail(f"{path.name}: size is out of bounds")
    return data


def load(path: Path) -> tuple[str, Record]:
    data = read_bytes(path)
    value = record(decisions.decode(data.decode("utf-8")), path.name)
    return native_stage.digest(data), value


def participant_ok(row: Record, mode: str, name: str) -> bool:
    assert mode in native_stage.MODES
    replay = mode == "replay"
    kwargs = row.get("kwargs")
    keys = {"source", "run_id", "pick"} if replay else {"source", "run_id"}
    return (row.keys() == PARTICIPANT_KEYS and row["name"] is None
            and row["import_path"] == native_stage.SEAT_IMPORT
            and row["model_name"] == (native_stage.DECLARED_MODEL if mode == "deepseek" else None)
            and isinstance(kwargs, dict) and kwargs.keys() == keys
            and kwargs["source"] == mode and kwargs["run_id"] == name
            and (not replay or pick_ok(kwargs["pick"])))


def by_seat(value: object, subject: str) -> dict[str, Record]:
    if not isinstance(value, list) or len(value) != len(ROLES):
        decisions.fail(f"{subject}: expected one sales and one finance seat")
    seats: dict[str, Record] = {}
    for item in value:
        row = record(item, subject)
        seat = row.get("seat")
        if not isinstance(seat, str) or seat not in ROLES or seat in seats:
            decisions.fail(f"{subject}: unknown or duplicate seat")
        seats[seat] = row
    assert seats.keys() == set(ROLES)
    return seats


def declared(root: Path, config: Record) -> tuple[int, str, str, dict[str, Record]]:
    if config.keys() != CONFIG_KEYS:
        decisions.fail("config.json: unexpected keys")
    name, task, trials = config["trial_name"], config["task"], config["trials_dir"]
    match = TRIAL.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        decisions.fail("config.json: invalid trial_name")
    world, arm, mode = int(match[1]), match[2], match[3]
    seats = by_seat(config["participants"], "config.json participants")
    require("config.json", {
        "schema_version": type(config["schema_version"]) is int and config["schema_version"] == 1,
        "trial_name": world < decisions.WORLDS
        and native_stage.trial_name(world, arm, mode) == name,
        "task": isinstance(task, dict) and task.keys() == {"path", "git_url", "name"}
        and task["git_url"] is None and task["name"] is None and isinstance(task["path"], str)
        and Path(task["path"]).resolve() == root / "task",
        "trials_dir": isinstance(trials, str) and Path(trials).resolve() == root / "trials",
        "participants": all(participant_ok(row, mode, name) for row in seats.values()),
    })
    return world, arm, mode, seats


def task_name(task: Path) -> str:
    section = tomllib.loads(read_bytes(task / "task.toml").decode("utf-8")).get("task")
    name = section.get("name") if isinstance(section, dict) else None
    if not isinstance(name, str) or not name:
        decisions.fail("task.toml: missing task name")
    return name


def options(payoffs: Record, world: int) -> dict[int, Record]:
    require("payoffs.json", {
        "keys": payoffs.keys() == decisions.PAYOFF_KEYS,
        "schema": payoffs.get("schema") == PAYOFF_SCHEMA,
        "source": payoffs.get("source") == decisions.PINNED_SOURCE,
        "world": type(payoffs.get("world")) is int and payoffs["world"] == world,
        "prefix_fingerprint": hex64(payoffs.get("prefix_fingerprint")),
    })
    rows = payoffs["options"]
    if not isinstance(rows, list) or len(rows) != len(decisions.PICKS):
        decisions.fail("payoffs.json: expected one option per pick")
    table: dict[int, Record] = {}
    for pick, item in zip(decisions.PICKS, rows, strict=True):
        row = record(item, "payoffs.json option")
        if not (row.keys() == decisions.OPTION_KEYS and pick_ok(row["pick"])
                and row["pick"] == pick and number_ok(row["multiple"])
                and number_ok(row["world_score"]) and 0 <= row["world_score"] <= 1):
            decisions.fail(f"payoffs.json: invalid option {pick}")
        table[pick] = row
    return table


def scenario_days(scenario: Record) -> dict[str, int]:
    require("scenario.json", {
        "keys": scenario.keys() == SCENARIO_KEYS,
        "schema": scenario.get("schema") == native_stage.SCENARIO_SCHEMA,
        "observations": all(isinstance(scenario.get(role), dict) for role in ROLES),
    })
    counts = record(scenario["completed_days"], "scenario.json completed_days")
    if counts.keys() != set(ROLES):
        decisions.fail("scenario.json: completed_days needs exactly sales and finance")
    days: dict[str, int] = {}
    for role in ROLES:
        count = counts[role]
        if not isinstance(count, int) or isinstance(count, bool) or not (
                0 <= count <= decisions.PREFIX_DAYS):
            decisions.fail(f"scenario.json: {role} completed_days is out of range")
        days[role] = count
    return days


def dossier_rows(dossier: Record) -> dict[str, Record]:
    if dossier.keys() != {"schema", *ROLES} or dossier["schema"] != EVIDENCE_SCHEMA:
        decisions.fail("market.json: expected seatlab.market-native-evidence/v1")
    rows: dict[str, Record] = {}
    for role in ROLES:
        row = record(dossier[role], f"market.json {role}")
        if (row.keys() != {"pick", "view_sha256"} or not pick_ok(row["pick"])
                or not hex64(row["view_sha256"])):
            decisions.fail(f"market.json: invalid {role} row")
        rows[role] = row
    return rows


def rebuild(scenario: Record, dossier: Record) -> dict[str, Record]:
    days, rows = scenario_days(scenario), dossier_rows(dossier)
    rebuilt: dict[str, Record] = {}
    for role in ROLES:
        view: Record = {"role": role, "completed_days": days[role], "observation": scenario[role]}
        task = decisions.SALES_TASK
        if role == "finance":
            view["advice"] = rows["sales"]["pick"]
            task = decisions.FINANCE_TASK.format(advice=view["advice"])
        cell = {"completed_days": days[role], role: scenario[role]}
        rebuilt[role] = {
            "pick": rows[role]["pick"],
            "view_sha256": native_stage.digest(native_stage.canonical(view)),
            "prompt_sha256": decisions.envelope(role, 0, "own", cell, task)["prompt_sha256"],
        }
        if rebuilt[role]["view_sha256"] != rows[role]["view_sha256"]:
            decisions.fail(f"market.json: {role} view digest differs from the rebuilt view")
    return rebuilt


def trial_id(native: Record) -> str:
    value = native.get("id")
    if not isinstance(value, str):
        decisions.fail("result.json: missing native trial UUID")
    parsed = UUID(value)
    if str(parsed) != value or parsed.int == 0:
        decisions.fail("result.json: expected a canonical native trial UUID")
    return value


def check_trial(native: Record, root: Path, config: Record, name: str) -> None:
    trial, path = config["trial_name"], config["task"]["path"]
    settings = record(native.get("config"), "result.json config")
    task = settings.get("task")
    seats = by_seat(settings.get("participants"), "result.json participants")
    declared_seats = by_seat(config["participants"], "config.json participants")
    finished = native.get("finished_at")
    require("result.json", {
        "schema_version": native.get("schema_version", 1) == 1,
        "trial_name": native.get("trial_name") == trial,
        "trial_uri": native.get("trial_uri") == (root / "trials" / trial).resolve().as_uri(),
        "task_name": native.get("task_name") == name,
        "task_id": native.get("task_id") == {"path": path},
        "config": settings.get("trial_name") == trial
        and isinstance(task, dict) and task.get("path") == path,
        "participants": all(seats[role].get(key) == value for role in ROLES
                            for key, value in declared_seats[role].items()),
        "exception_info": "exception_info" in native and native["exception_info"] is None,
        "finished_at": isinstance(finished, str) and finished != "",
        "reward_valid": native.get("reward_valid") is True,
        "seat_trial": native.get("step_results") is None and native.get("agent_result") is None,
    })


def native_seats(native: Record, mode: str, declared_seats: dict[str, Record],
                 rebuilt: dict[str, Record]) -> dict[str, Record]:
    seats = by_seat(native.get("seat_results"), "result.json seat_results")
    receipts: dict[str, Record] = {}
    for role in ROLES:
        seat = seats[role]
        subject = f"result.json seat {role}"
        context = record(seat.get("agent_result"), subject)
        metadata = record(context.get("metadata"), f"{subject} metadata")
        served = metadata.get("served_model")
        expected = {"role": role, "source": mode, "served_model": served,
                    "phase": "submitted", **rebuilt[role]}
        require(subject, {
            "under_test": seat.get("under_test") is True,
            "exception_info": "exception_info" in seat and seat["exception_info"] is None,
            "metadata": metadata == expected and pick_ok(metadata["pick"]),
            "served_model": isinstance(served, str) and served.strip() != ""
            if mode == "deepseek" else served is None,
            "replay_pick": mode != "replay"
            or rebuilt[role]["pick"] == declared_seats[role]["kwargs"]["pick"],
        })
        receipts[role] = {"source": mode, "served_model": served, **rebuilt[role]}
    return receipts


def grade(native: Record, option: Record) -> float | int:
    verifier = record(native.get("verifier_result"), "result.json verifier_result")
    rewards = record(verifier.get("rewards"), "result.json rewards")
    if rewards.keys() != {"reward"}:
        decisions.fail("result.json: expected the native verifier's single reward")
    reward = rewards["reward"]
    if not number_ok(reward):
        decisions.fail("result.json: native verifier reward is not a finite number")
    if reward != option["world_score"]:
        decisions.fail("result.json: native verifier reward differs from the sealed world_score")
    return reward


def receipt(out: Path) -> Record:
    root = out.expanduser().resolve(strict=True)
    config_sha, config = load(root / "config.json")
    world, arm, mode, declared_seats = declared(root, config)
    task = root / "task"
    name = task_name(task)
    _, scenario = load(task / "environment/scenario.json")
    payoffs_sha, payoffs = load(task / "tests/payoffs.json")
    trial_dir = root / "trials" / config["trial_name"]
    result_sha, native = load(trial_dir / "result.json")
    evidence_sha, dossier = load(trial_dir / "artifacts/logs/world/market.json")
    table = options(payoffs, world)
    rebuilt = rebuild(scenario, dossier)
    check_trial(native, root, config, name)
    seats = native_seats(native, mode, declared_seats, rebuilt)
    option = table[rebuilt["finance"]["pick"]]
    return {
        "schema": SCHEMA, "trial_id": trial_id(native), "trial_name": config["trial_name"],
        "kind": mode, "world": world, "arm": arm, "task_name": name,
        "prefix_fingerprint": payoffs["prefix_fingerprint"],
        "sha256": {"config": config_sha, "result": result_sha, "evidence": evidence_sha,
                   "payoffs": payoffs_sha},
        "choices": {role: rebuilt[role]["pick"] for role in ROLES},
        "seats": seats,
        "grade": grade(native, option), "multiple": option["multiple"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.seatlab.market_handoff.native_receipt")
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(native_stage.canonical(receipt(args.out)).decode("ascii") + "\n")
    except (OSError, ValueError, RecursionError) as exc:
        message = (f"{exc.strerror or type(exc).__name__}: {Path(exc.filename).name}"
                   if isinstance(exc, OSError) and exc.filename else str(exc))
        sys.stderr.write(f"error: {message}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
