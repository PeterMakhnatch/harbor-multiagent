import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from .. import evidence, native_panel
from ..briefing import audit, protocol
from ..briefing.__main__ import experiment
from . import packet

_MAX_REPORT = 1024 * 1024
_MAX_NATIVE = 2 * 1024 * 1024
_MAX_SEAL = 64 * 1024
_MAX_TASK_INPUT = 1024 * 1024
_MISSING_STATES = ("unclaimed", "reserved_without_trial")
_STATES = (*_MISSING_STATES, "claimed", "native_failed", "unverified", "complete")
_HASH_FIELDS = ("prompt_sha256", "response_sha256")
_REPORT_FIELDS = {"schema", "plan", "rows", "summary"}
_OBSERVED_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    RecursionError,
)
_BUILD_ERRORS = (*_OBSERVED_ERRORS, RuntimeError)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sealed(path: Path) -> bytes:
    native_panel.teams._check_path(path, 0o400)
    return packet._read_bytes(path, _MAX_SEAL)


def _study_plan(root: Path, cells: list[dict[str, Any]]) -> dict[str, Any]:
    native_panel.teams._check_path(root, 0o700, directory=True)
    native_panel.teams._check_path(root / "cells", 0o700, directory=True)
    seal = packet._decode(_sealed(root / "register.json"), integers_only=False)
    record = packet._object(seal, "plan", "spend_directory")
    spend_directory = record["spend_directory"]
    spec = experiment()
    plan = packet._object(
        record["plan"],
        "schema",
        "transport",
        "command_config_sha256",
        "episodes",
        "max_direct_calls",
        "sources",
        "seat_sources",
        "cells",
    )
    if (
        not isinstance(spend_directory, str)
        or not Path(spend_directory).is_absolute()
        or plan["schema"] != spec.schema("plan")
        or plan["transport"] != "direct"
        or plan["command_config_sha256"] is not None
        or type(plan["episodes"]) is not int
        or plan["episodes"] != 6
        or type(plan["max_direct_calls"]) is not int
        or plan["max_direct_calls"] != spec.max_calls(cells)
        or _canonical(plan["cells"]) != _canonical(cells)
    ):
        raise ValueError("unexpected native briefing plan")
    sources = packet._object(
        plan["sources"], *native_panel.panel._source_names("arrival")
    )
    seat_sources = packet._object(
        plan["seat_sources"],
        *spec.seat_sources,
        *(f"shared/{name}" for name in spec.shared_seat_sources),
    )
    for stamps in (sources, seat_sources):
        for stamp in stamps.values():
            packet._digest(stamp)
    return plan


def _report_rows(
    raw: bytes, plan: dict[str, Any], cells: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    report = packet._decode(raw, integers_only=False)
    if (
        not isinstance(report, dict)
        or report.keys() - {"meter"} != _REPORT_FIELDS
        or report["schema"] != experiment().schema("report")
        or _canonical(report["plan"]) != _canonical(plan)
        or not isinstance(report["summary"], dict)
        or not isinstance(report.get("meter", {}), dict)
    ):
        raise ValueError("report does not match the sealed plan")
    entries = report["rows"]
    if not isinstance(entries, list) or len(entries) != len(cells):
        raise ValueError("report does not cover the registered grid")
    rows: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid report row")
        key = entry.get("cell_id")
        if not isinstance(key, str) or key not in cells or key in rows:
            raise ValueError("unregistered or duplicate report row")
        cell = cells[key]
        row = packet._object(entry, *cell, "state", "outcome")
        if _canonical({field: row[field] for field in cell}) != _canonical(cell):
            raise ValueError("report row does not match the registered cell")
        if cell["arm"] == "brief" and (
            row["state"] not in _STATES
            or (row["state"] == "complete" and not isinstance(row["outcome"], dict))
            or (row["state"] != "complete" and row["outcome"] is not None)
        ):
            raise ValueError("invalid brief report row state")
        rows[key] = row
    return rows


def _task_inputs(task: Path, cell: dict[str, Any], plan: dict[str, Any]) -> None:
    panel, teams = native_panel.panel, native_panel.teams
    children: dict[Path, set[str]] = {}
    for name in panel.TASK_FILES:
        path = Path(name)
        for parent in path.parents:
            children.setdefault(parent, set()).add(path.name)
            path = parent
    for parent in sorted(children, key=lambda path: len(path.parts)):
        directory = task / parent
        teams._check_path(directory, 0o755, directory=True)
        missing = children[parent].copy()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name not in missing:
                    raise ValueError("unexpected registered task entry")
                missing.remove(entry.name)
        if missing:
            raise ValueError("missing registered task input")
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
        raw = packet._read_bytes(path, _MAX_TASK_INPUT)
        if name == panel.SCENARIO:
            matches = raw == scenario
        else:
            matches = (
                sha256(raw).hexdigest() == plan["sources"][panel._task_source(name)]
            )
        if not matches:
            raise ValueError("registered task input changed")


def _trial(config: dict[str, Any]) -> Path | None:
    directory = Path(config["trials_dir"])
    if not os.path.lexists(directory):
        return None
    native_panel.teams._check_path(directory, 0o700, directory=True)
    name = config["trial_name"]
    with os.scandir(directory) as entries:
        found = False
        for entry in entries:
            if entry.name != name or not entry.is_dir(follow_symlinks=False):
                raise ValueError("unexpected or duplicate native trial folder")
            found = True
    return directory / name if found else None


def _native_json(path: Path) -> Any:
    if path.resolve() != path:
        raise ValueError("unsafe native evidence path")
    return packet._decode(packet._read_bytes(path, _MAX_NATIVE), integers_only=False)


def _inspect(
    trial: Path | None, config: dict[str, Any], cell: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if trial is None:
        return None
    try:
        native = _native_json(trial / "result.json")
        _native_json(trial / "config.json")
        world = _native_json(trial / "artifacts/logs/world/handoff.json")
    except FileNotFoundError:
        return None
    try:
        observed = audit.inspect_trial(trial, config, cell, "direct")
    except _OBSERVED_ERRORS:
        return None
    return observed, native, world


def _source(
    cell: dict[str, Any], checked: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> dict[str, Any]:
    observed, native, world = checked
    turns = observed["turns"]
    upstream = {
        turn["stage"]: {key: turn[key] for key in _HASH_FIELDS} for turn in turns[:2]
    }
    seats = evidence._by_role(native["seat_results"], "seat_results")
    inventory = seats["inventory"]["agent_result"]["metadata"]["turns"][0]["text"]
    inventory_raw = inventory.encode("utf-8")
    brief = seats["dispatch"]["agent_result"]["metadata"]["turns"][0]["text"]
    brief_hash = sha256(brief.encode("utf-8")).hexdigest()
    fingerprint = sha256(
        _canonical(
            [
                {key: case[key] for key in ("id", "utilities", "before", "after")}
                for case in world["cases"]
            ]
        )
    ).hexdigest()
    if (
        native["id"] != observed["trial_id"]
        or fingerprint != observed["fingerprint"]
        or brief_hash != upstream["brief"]["response_sha256"]
        or sha256(inventory_raw).hexdigest() != upstream["inventory"]["response_sha256"]
    ):
        raise ValueError("native evidence does not match its audited commitments")
    accepted = [
        {key: case[key] for key in ("id", "reports")} for case in world["cases"]
    ]
    submitted = packet._decode(inventory_raw, integers_only=True)["rows"]
    decision_hash = native_panel.native._decision_hash
    if decision_hash("inventory", submitted) != decision_hash("inventory", accepted):
        raise ValueError("accepted reports do not match the audited inventory turn")
    seed, rep = cell["seed"], cell["repetition"]
    return {
        "source_id": f"s{seed}-r{rep}",
        "seed": seed,
        "repetition": rep,
        "trial_id": observed["trial_id"],
        "world_sha256": fingerprint,
        "upstream": upstream,
        "card": protocol.parse_brief(brief),
        "cases": [
            {key: case[key] for key in ("id", "utilities", "reports", "after")}
            for case in world["cases"]
        ],
    }


def _cell_source(
    root: Path, plan: dict[str, Any], cell: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any] | None:
    directory = native_panel._cell_dir(root, cell)
    if not os.path.lexists(directory):
        if row["state"] not in _MISSING_STATES:
            raise ValueError("missing native claim")
        return None
    teams = native_panel.teams
    teams._check_path(directory, 0o700, directory=True)
    config = native_panel._config(experiment(), root, cell, "direct")
    trial = _trial(config)
    exit_path = directory / "outcome.json"
    if not os.path.lexists(exit_path):
        if row["state"] != "claimed" or _inspect(trial, config, cell) is not None:
            raise ValueError("missing native completion record")
        return None
    if _sealed(directory / "attempt.json") != teams._bytes(cell) or _sealed(
        directory / "config.json"
    ) != teams._bytes(config):
        raise ValueError("sealed native claim differs from the registered cell")
    _task_inputs(directory / "dispatch-handoff", cell, plan)
    exit_raw = _sealed(exit_path)
    exit_record = packet._object(
        packet._decode(exit_raw, integers_only=True), "returncode", "launch_error"
    )
    code, error = exit_record["returncode"], exit_record["launch_error"]
    if exit_raw != teams._bytes(exit_record) or not (
        (type(code) is int and error is None)
        or (code is None and error == "native_launch_failed")
    ):
        raise ValueError("invalid sealed process outcome")
    checked = _inspect(trial, config, cell) if code == 0 else None
    expected_state = (
        "native_failed"
        if code != 0
        else "unverified"
        if checked is None
        else "complete"
    )
    if row["state"] != expected_state or (
        checked is not None and _canonical(checked[0]) != _canonical(row["outcome"])
    ):
        raise ValueError("report row differs from audited native evidence")
    return None if checked is None else _source(cell, checked)


def build(study: Path, report: Path) -> dict[str, Any]:
    try:
        root = study.resolve(strict=True)
        cells = list(protocol.grid())
        plan = _study_plan(root, cells)
        grid = {cell["cell_id"]: cell for cell in cells}
        with os.scandir(root / "cells") as entries:
            if any(entry.name not in grid for entry in entries):
                raise ValueError("unregistered native cell folder")
        raw = packet._read_bytes(report, _MAX_REPORT)
        rows = _report_rows(raw, plan, grid)
        sources = []
        for cell in cells:
            if cell["arm"] != "brief":
                continue
            source = _cell_source(root, plan, cell, rows[cell["cell_id"]])
            if source is not None:
                sources.append(source)
        return packet._validate(
            {
                "schema": packet.SCHEMA,
                "source_report_sha256": sha256(raw).hexdigest(),
                "sources": sources,
            }
        )
    except _BUILD_ERRORS:
        raise ValueError(
            "cannot build packet from inconsistent native briefing evidence"
        ) from None
