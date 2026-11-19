"""Export public Dispatch Handoff study data or check an exported bundle offline.

Native SHA-256 pins identify the original bytes; they do not attest that a
trial occurred. Verification checks the public data, not the original files,
pricing, credentials, or execution.
"""

import argparse
import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from evidence import _score as score_manifest
from evidence import _trial_id, read_trial_with_world
from run_panel import (
    PLAN_SCHEMA,
    POLICIES,
    RUNS_SCHEMA,
    SCENARIO,
    Cell,
    Plan,
    _config_matches,
    _hosted_route,
    _make_plan,
    _scenario,
    _sha256,
    _source_names,
    _trial_config,
)

BUNDLE_SCHEMA = "seatlab.dispatch-handoff-study/v1"
VERIFICATION_SCHEMA = "seatlab.dispatch-handoff-study-verification/v1"
TASK = "harbor/dispatch-handoff"
WORLD = "artifacts/logs/world/handoff.json"
ARTIFACTS = "artifacts/manifest.json"
ROLES = ("inventory", "dispatch")
BAYS = ("A", "B", "C", "D")
TOKENS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
ABSENCES = (
    "not_attempted",
    "launch_failed",
    "native_failed",
    "missing_native_output",
    "incomplete_dossier",
)
PROCESS_STATES = ("no_receipt", "invalid_receipt", "launch_failed", "exited")


def _provider_binding(provider: object, slug: object, declared: str) -> str:
    if not isinstance(provider, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", provider
    ):
        raise ValueError("invalid served provider display name")
    if slug is not None:
        if slug != declared:
            raise ValueError("native provider slug contradicts the planned route")
        return "native_slug_matches"
    # A display name can match the spelling without attesting to an endpoint.
    return "display_matches_slug" if provider.lower() == declared else "unknown"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(_: str) -> None:
    raise ValueError("nonfinite JSON number")


def _decode(content: bytes, field: str) -> Any:
    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_invalid_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError(f"invalid {field} JSON") from None


def _object(
    value: object, field: str, keys: Iterable[str] | None = None
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or any(not isinstance(key, str) for key in value)
        or (keys is not None and value.keys() != set(keys))
    ):
        raise ValueError(f"invalid {field} object")
    return cast("dict[str, Any]", value)


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"invalid {field} array")
    return value


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _panel_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise ValueError("panel name must be a public lowercase label")
    return value


def _plan(value: object) -> Plan:
    raw = _object(value, "panel plan")
    policy = raw.get("policy")
    if (
        not isinstance(policy, str)
        or policy not in POLICIES
        or raw.get("schema") != PLAN_SCHEMA
    ):
        raise ValueError("unsupported Dispatch Handoff plan or policy")
    seeds = _array(raw.get("seeds"), "plan seeds")
    conditions = _array(raw.get("conditions"), "plan conditions")
    episodes = raw.get("episodes")
    if type(episodes) is not int:
        raise ValueError("expected an integer episode count")
    if any(not isinstance(condition, str) for condition in conditions):
        raise ValueError("expected string conditions")
    sources = _object(
        raw.get("source_sha256"), "source SHA-256 pins", _source_names(policy)
    )
    if not all(map(_digest, sources.values())):
        raise ValueError("invalid source SHA-256 pin")
    route = raw.get("hosted_route")
    if route is not None:
        route = _object(route, "public hosted route", ("model", "provider"))
        if (
            not isinstance(route["model"], str)
            or len(route["model"]) > 128
            or not isinstance(route["provider"], str)
            or len(route["provider"]) > 64
        ):
            raise ValueError("invalid public hosted route")
        route = _hosted_route(route["model"], route["provider"])
    plan = _make_plan(seeds, conditions, episodes, policy, sources, route)
    try:
        matches_plan = _canonical(raw) == _canonical(plan)
    except (TypeError, ValueError):
        raise ValueError("invalid panel plan values") from None
    if not matches_plan:
        raise ValueError("plan does not match the canonical Dispatch Handoff design")
    return plan


def _coordinates(row: dict[str, Any], cell: Cell) -> None:
    if not _config_matches(row, cell):
        raise ValueError("cell coordinates do not match the planned grid")


def _counts(cells: list[dict[str, Any]]) -> dict[str, int]:
    measured = sum(cell["status"] == "measured" for cell in cells)
    return {
        "planned": len(cells),
        "measured": measured,
        "unmeasured": len(cells) - measured,
    }


def _check_counts(document: dict[str, Any], cells: list[dict[str, Any]]) -> None:
    if not _config_matches(document, _counts(cells)):
        raise ValueError("planned/measured/unmeasured counts do not match the cells")


def _reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"revision": report["revision"], "available": list(report["available"])}
        for report in reports
    ]


def _world(dossier: dict[str, Any]) -> dict[str, Any]:
    """Project only scorer-validated fields. Never copy a raw case or object."""
    settings = dossier["settings"]
    return {
        "schema": dossier["schema"],
        "complete": dossier["complete"],
        "settings": {
            "schema": settings["schema"],
            "condition": settings["condition"],
            "seed": settings["seed"],
            "episodes": settings["episodes"],
        },
        "cases": [
            {
                "id": case["id"],
                "utilities": {bay: case["utilities"][bay] for bay in BAYS},
                "before": list(case["before"]),
                "after": list(case["after"]),
                "reports": None
                if case["reports"] is None
                else _reports(case["reports"]),
                "visible": _reports(case["visible"]),
                "chosen": case["chosen"],
            }
            for case in dossier["cases"]
        ],
    }


def _fingerprint(manifest: dict[str, Any]) -> str:
    latent = [
        {key: case[key] for key in ("id", "utilities", "before", "after")}
        for case in manifest["cases"]
    ]
    return _sha256(_canonical(latent))


def _seat_name(plan: Plan, cell: Cell, role: str) -> str:
    if role == "inventory" and cell["condition"] == "pooled":
        return "nop"
    return "oracle" if plan["policy"] == "oracle" else "seatlab-dispatch-handoff"


def _record_trial_id(row: dict[str, Any], ids: set[str]) -> None:
    trial_id = _trial_id({"id": row["trial_id"]})
    if trial_id in ids:
        raise ValueError("duplicate native trial UUID")
    ids.add(trial_id)


def _check_seats(seats: object, plan: Plan, cell: Cell) -> None:
    by_role = _object(seats, "seats", ROLES)
    route = plan["hosted_route"]
    for role in ROLES:
        seat = _object(
            by_role[role],
            "public seat",
            (
                "agent_name",
                "agent_version",
                "under_test",
                "model_info",
                "served_route",
                "cost_usd",
                *TOKENS,
            ),
        )
        name = _seat_name(plan, cell, role)
        if (
            seat["agent_name"] != name
            or seat["agent_version"] != "1.0.0"
            or seat["under_test"] is not True
        ):
            raise ValueError("seat identity does not match the planned policy")
        if route is None or name == "nop":
            if seat["model_info"] is not None or seat["served_route"] is not None:
                raise ValueError("a deterministic seat cannot declare a model route")
        else:
            if seat["model_info"] != {"name": route["model"], "provider": "openrouter"}:
                raise ValueError("native model does not match the planned route")
            served = _object(
                seat["served_route"],
                "served route",
                ("model", "provider", "provider_slug", "provider_binding"),
            )
            if served["model"] not in (
                route["model"],
                route["model"].removesuffix(":free"),
            ):
                raise ValueError("served model does not match the planned route")
            if served["provider_binding"] != _provider_binding(
                served["provider"], served["provider_slug"], route["provider"]
            ):
                raise ValueError("inconsistent provider binding")
        for key in TOKENS:
            count = seat[key]
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("invalid per-seat token count")
        cached, total = seat["n_cache_tokens"], seat["n_input_tokens"]
        if cached is not None and total is not None and cached > total:
            raise ValueError("per-seat cache tokens exceed input tokens")
        cost = seat["cost_usd"]
        if cost is not None and (
            type(cost) not in (int, float) or not 0 <= cost < math.inf
        ):
            raise ValueError("invalid per-seat cost")
        if route is not None and cost not in (None, 0):
            raise ValueError("nonzero cost in a free hosted panel")


def _measured(
    measurement: object, plan: Plan, cell: Cell, ids: set[str], paired: dict[int, str]
) -> None:
    row = _object(
        measurement,
        "measurement",
        (
            "trial_id",
            "native_sha256",
            "score",
            "reward",
            "fingerprint",
            "seats",
            "manifest",
            "manifest_sha256",
        ),
    )
    _record_trial_id(row, ids)
    pins = _object(
        row["native_sha256"],
        "native SHA-256 pins",
        ("result", "artifact_manifest", "world_manifest"),
    )
    if not all(map(_digest, pins.values())):
        raise ValueError("invalid native SHA-256 pin")
    manifest = _object(row["manifest"], "public manifest")
    score = score_manifest(manifest)
    if manifest != _world(manifest):
        raise ValueError("public manifest has fields outside the allowlist")
    if manifest["settings"] != _scenario(plan, cell):
        raise ValueError("public manifest settings do not match the planned cell")
    if row["manifest_sha256"] != _sha256(_canonical(manifest)):
        raise ValueError("public manifest SHA-256 does not match")
    fingerprint = _fingerprint(manifest)
    if row["fingerprint"] != fingerprint:
        raise ValueError("latent scenario fingerprint does not match")
    if paired.setdefault(cell["seed"], fingerprint) != fingerprint:
        raise ValueError("latent scenarios differ within a paired seed")
    if _canonical(row["score"]) != _canonical(score):
        raise ValueError("public score does not match the recomputed score")
    reward = row["reward"]
    if (
        type(reward) not in (int, float)
        or reward != score["optimal"] / score["episodes"]
    ):
        raise ValueError("shared reward does not match the recomputed score")
    _check_seats(row["seats"], plan, cell)


def _exit_code(value: object) -> bool:
    return type(value) is int and -255 <= value <= 255


def _http_status(value: object) -> bool:
    return type(value) is int and 100 <= value <= 599 and value != 200


def _unmeasured(row: dict[str, Any], plan: Plan, cell: Cell, ids: set[str]) -> None:
    _object(
        row,
        "unmeasured cell",
        (*cell, "status", "absence", "process", "native_attempt"),
    )
    absence = row["absence"]
    if absence not in ABSENCES:
        raise ValueError("unknown absence category")
    process = _object(row["process"], "process receipt", ("state", "returncode"))
    state, code = process["state"], process["returncode"]
    if state not in PROCESS_STATES or (
        not _exit_code(code) if state == "exited" else code is not None
    ):
        raise ValueError("invalid process receipt")
    attempt = row["native_attempt"]
    if attempt is None:
        if (
            (absence == "not_attempted" and state != "no_receipt")
            or (absence == "launch_failed") != (state == "launch_failed")
            or (absence == "native_failed") != (state == "exited" and code != 0)
        ):
            raise ValueError("absence does not match the process receipt")
        return
    if absence not in ("native_failed", "incomplete_dossier"):
        raise ValueError("absence does not match the native attempt")
    attempt = _object(attempt, "native attempt", ("trial_id", "native_sha256", "seats"))
    _record_trial_id(attempt, ids)
    pins = _object(
        attempt["native_sha256"],
        "native attempt SHA-256 pins",
        ("result", "artifact_manifest", "world_manifest"),
    )
    if not _digest(pins["result"]) or any(
        pins[key] is not None and not _digest(pins[key])
        for key in ("artifact_manifest", "world_manifest")
    ):
        raise ValueError("invalid native attempt SHA-256 pin")
    seats = _object(attempt["seats"], "native attempt seats", ROLES)
    for role in ROLES:
        seat = _object(
            seats[role], "native attempt seat", ("model_turns", "http_status")
        )
        turns, status = seat["model_turns"], seat["http_status"]
        if (turns is not None or status is not None) and (
            plan["policy"] != "hosted" or _seat_name(plan, cell, role) == "nop"
        ):
            raise ValueError("a deterministic seat cannot report hosted telemetry")
        if turns is not None and (type(turns) is not int or turns not in (0, 1)):
            raise ValueError("invalid native model turn count")
        if status is not None and not _http_status(status):
            raise ValueError("invalid native HTTP status")


def verify_bundle(bundle: object) -> dict[str, str | int]:
    """Recompute portable checks without reading any native directories."""
    raw = _object(bundle, "bundle", ("schema", "panels"))
    if raw["schema"] != BUNDLE_SCHEMA:
        raise ValueError("unsupported bundle schema")
    panels = _array(raw["panels"], "panels")
    if not panels:
        raise ValueError("expected at least one panel")
    totals = {"planned": 0, "measured": 0, "unmeasured": 0}
    names: set[str] = set()
    ids: set[str] = set()
    for value in panels:
        panel = _object(
            value,
            "public panel",
            ("name", "plan", "cells", "planned", "measured", "unmeasured"),
        )
        name = _panel_name(panel["name"])
        if name in names:
            raise ValueError("duplicate panel name")
        names.add(name)
        plan = _plan(panel["plan"])
        rows = _array(panel["cells"], "public panel cells")
        if len(rows) != len(plan["cells"]):
            raise ValueError("panel cells do not match the planned grid")
        paired: dict[int, str] = {}
        for value, cell in zip(rows, plan["cells"], strict=True):
            row = _object(value, "public cell")
            _coordinates(row, cell)
            common = (*cell, "status")
            if row.get("status") == "measured":
                _object(row, "measured cell", (*common, "measurement"))
                _measured(row["measurement"], plan, cell, ids, paired)
            elif row.get("status") == "unmeasured":
                _unmeasured(row, plan, cell, ids)
            else:
                raise ValueError("unknown cell status")
        _check_counts(panel, rows)
        for key in totals:
            totals[key] += panel[key]
    return {"schema": VERIFICATION_SCHEMA, "panels": len(panels), **totals}


def _local(root: Path, relative: str) -> Path:
    path = root / relative
    if path.resolve() != path:
        raise ValueError("panel inputs must not use symlinks")
    return path


def _optional_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _process(trial: Path, run: dict[str, Any]) -> dict[str, Any]:
    content = _optional_bytes(_local(trial.parent.parent, "receipt.json"))
    receipt_error = run.get("receipt_error")
    if content is None:
        state, code, launch = "no_receipt", None, None
    else:
        try:
            receipt = _object(_decode(content, "process receipt"), "process receipt")
            code, launch = receipt.get("returncode"), receipt.get("launch_error")
            if _exit_code(code) and launch is None:
                state = "exited"
            elif code is None and isinstance(launch, str) and launch:
                state = "launch_failed"
            else:
                raise ValueError("invalid process receipt")
        except ValueError:
            state, code, launch = "invalid_receipt", None, None
    if state == "invalid_receipt":
        consistent_error = isinstance(receipt_error, str) and bool(receipt_error)
    else:
        consistent_error = receipt_error is None
    if (
        type(run.get("returncode")) is not type(code)
        or run.get("returncode") != code
        or run.get("launch_error") != launch
        or not consistent_error
    ):
        raise ValueError("process receipt differs from the runs index")
    return {"state": state, "returncode": code}


def _native_artifact_manifest(content: bytes) -> str:
    entries = _array(_decode(content, "native artifact manifest"), "artifact manifest")
    expected = {
        "source": "/logs/world/handoff.json",
        "destination": WORLD,
        "type": "file",
        "service": None,
    }
    matches = []
    for value in entries:
        entry = _object(value, "native artifact entry")
        if (
            entry.get("source") == expected["source"]
            or entry.get("destination") == WORLD
        ):
            matches.append(entry)
    if len(matches) != 1 or not _config_matches(matches[0], expected):
        raise ValueError(
            "native artifact manifest does not identify one collected dossier"
        )
    status = matches[0].get("status")
    if status not in ("ok", "failed", "empty", "skipped"):
        raise ValueError("invalid world collection status")
    return status


def _absence(
    trial: Path, process: dict[str, Any], native: dict[str, Any] | None
) -> str:
    if native is not None:
        seats = native.get("seat_results")
        if (
            isinstance(native.get("exception_info"), dict)
            or native.get("reward_valid") is False
            or (
                isinstance(seats, list)
                and any(
                    isinstance(seat, dict)
                    and isinstance(seat.get("exception_info"), dict)
                    for seat in seats
                )
            )
        ):
            return "native_failed"
        if not _local(trial, WORLD).is_file() or not _local(trial, ARTIFACTS).is_file():
            return "incomplete_dossier"
        raise ValueError("completed native dossier was not indexed as measured")
    if process["state"] == "launch_failed":
        return "launch_failed"
    if process["returncode"] not in (None, 0):
        return "native_failed"
    if trial.exists():
        return "incomplete_dossier"
    if trial.parent.parent.exists():
        return "missing_native_output"
    return "not_attempted"


def _hosted_metadata(role: str) -> dict[str, str]:
    return {
        "schema": "seatlab.seat/v1",
        "policy": "hosted",
        "role": role,
        "decision_source": "model",
    }


def _failure_seats(
    native: dict[str, Any], plan: Plan, cell: Cell
) -> dict[str, dict[str, int | None]]:
    seats: dict[str, dict[str, int | None]] = {
        role: {"model_turns": None, "http_status": None} for role in ROLES
    }
    records = native.get("seat_results")
    if records is None:
        return seats
    seen: set[str] = set()
    for value in _array(records, "native failure seats"):
        seat = _object(value, "native failure seat")
        role = seat.get("seat")
        if role not in ROLES or role in seen or seat.get("under_test") is not True:
            raise ValueError("invalid native failure seat identity")
        seen.add(role)
        name = _seat_name(plan, cell, role)
        route = plan["hosted_route"] if name == "seatlab-dispatch-handoff" else None
        model = (
            None
            if route is None
            else {"name": route["model"], "provider": "openrouter"}
        )
        info = seat.get("agent_info")
        if info is not None and not _config_matches(
            info, {"name": name, "version": "1.0.0", "model_info": model}
        ):
            raise ValueError("native failure agent does not match the planned policy")
        context = seat.get("agent_result")
        meta = context.get("metadata") if isinstance(context, dict) else None
        if (
            route is None
            or not isinstance(meta, dict)
            or not _config_matches(meta, _hosted_metadata(role))
        ):
            continue
        turns, requested = meta.get("model_turns"), meta.get("requested_model")
        if type(turns) is not int or turns not in (0, 1):
            raise ValueError("invalid native model turn count")
        if requested != route["model"] and (turns == 1 or requested is not None):
            raise ValueError("native requested model does not match the planned route")
        if meta.get("provider_slug") not in (None, route["provider"]):
            raise ValueError("native provider slug contradicts the planned route")
        seats[role]["model_turns"] = turns
        exception = seat.get("exception_info")
        if (
            isinstance(exception, dict)
            and exception.get("exception_type") == "_Unmeasured"
        ):
            message = exception.get("exception_message")
            if isinstance(message, str) and (
                match := re.fullmatch(
                    r"OpenRouter request failed \(HTTP ([1-5][0-9]{2})\)", message
                )
            ):
                status = int(match[1])
                if _http_status(status):
                    seats[role]["http_status"] = status
    return seats


def _native_attempt(
    trial: Path,
    plan: Plan,
    cell: Cell,
    result: bytes,
    native: dict[str, Any],
) -> dict[str, Any]:
    scenario_path = _local(trial.parent.parent, f"dispatch-handoff/{SCENARIO}")
    scenario = _decode(scenario_path.read_bytes(), "staged scenario")
    if _canonical(scenario) != _canonical(_scenario(plan, cell)):
        raise ValueError("staged scenario differs from the planned cell")
    artifacts = _optional_bytes(_local(trial, ARTIFACTS))
    artifact_status = (
        None if artifacts is None else _native_artifact_manifest(artifacts)
    )
    world = _optional_bytes(_local(trial, WORLD))
    if world is not None:
        dossier = _object(
            _decode(world, "native world dossier"), "native world dossier"
        )
        if dossier.get("schema") != "seatlab.world/v1" or not _config_matches(
            dossier.get("settings"), _scenario(plan, cell)
        ):
            raise ValueError("native world settings differ from the planned cell")
    if artifact_status == "ok" and world is None:
        raise ValueError("native artifact manifest claims a missing dossier")
    return {
        "trial_id": _trial_id(native),
        "native_sha256": {
            "result": _sha256(result),
            "artifact_manifest": None if artifacts is None else _sha256(artifacts),
            "world_manifest": None if world is None else _sha256(world),
        },
        "seats": _failure_seats(native, plan, cell),
    }


def _served_route(
    native_seat: dict[str, Any], role: str, plan: Plan
) -> dict[str, Any] | None:
    route = plan["hosted_route"]
    if route is None or native_seat["agent_info"]["name"] == "nop":
        return None
    meta = native_seat["agent_result"].get("metadata")
    expected = {
        **_hosted_metadata(role),
        "model_turns": 1,
        "requested_model": route["model"],
    }
    if not _config_matches(meta, expected):
        raise ValueError("native hosted seat metadata does not match the planned route")
    provider, slug = meta.get("provider"), meta.get("provider_slug")
    return {
        "model": meta.get("served_model"),
        "provider": provider,
        "provider_slug": slug,
        "provider_binding": _provider_binding(provider, slug, route["provider"]),
    }


def _native_config(native: dict[str, Any], expected_config: dict[str, Any]) -> None:
    version = native.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("unsupported native trial result schema")
    if (
        native.get("task_name") != TASK
        or native.get("trial_name") != expected_config["trial_name"]
        or not _config_matches(native.get("config"), expected_config)
    ):
        raise ValueError(
            "native task or config does not match the planned cell or policy"
        )
    config = native["config"]
    if (
        config.get("source_trial") is not None
        or config.get("install_only", False) is not False
        or config.get("extra_instructions", []) != []
        or config.get("extra_instruction_paths", []) != []
    ):
        raise ValueError("native trial overrides the registered execution")


def _export_measurement(
    trial: Path,
    plan: Plan,
    cell: Cell,
    run: dict[str, Any],
    result: bytes,
    native: dict[str, Any],
) -> dict[str, Any]:
    world_path = _local(trial, WORLD)
    artifact_path = _local(trial, ARTIFACTS)
    world_bytes = world_path.read_bytes()
    _decode(world_bytes, "native world dossier")
    artifact_bytes = artifact_path.read_bytes()
    if _native_artifact_manifest(artifact_bytes) != "ok":
        raise ValueError("native artifact collection did not succeed")
    try:
        evidence, dossier = read_trial_with_world(trial)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise ValueError("native evidence validation failed") from None
    if (
        (trial / "result.json").read_bytes() != result
        or world_path.read_bytes() != world_bytes
        or artifact_path.read_bytes() != artifact_bytes
    ):
        raise ValueError("native files changed during export")
    if (
        not _config_matches(run.get("evidence"), evidence)
        or evidence["seed"] != cell["seed"]
        or evidence["condition"] != cell["condition"]
        or evidence["score"]["episodes"] != plan["episodes"]
    ):
        raise ValueError("native evidence differs from the receipt or plan")
    native_seats = {seat["seat"]: seat for seat in native["seat_results"]}
    seats = {}
    for role, seat in evidence["seats"].items():
        model = seat["model_info"]
        seats[role] = {
            "agent_name": seat["agent_name"],
            "agent_version": seat["agent_version"],
            "under_test": seat["under_test"],
            "model_info": None
            if model is None
            else {"name": model["name"], "provider": model["provider"]},
            "served_route": _served_route(native_seats[role], role, plan),
            "n_input_tokens": seat["n_input_tokens"],
            "n_cache_tokens": seat["n_cache_tokens"],
            "n_output_tokens": seat["n_output_tokens"],
            "cost_usd": seat["cost_usd"],
        }
    manifest = _world(dossier)
    return {
        "trial_id": evidence["trial_id"],
        "native_sha256": {
            "result": _sha256(result),
            "artifact_manifest": _sha256(artifact_bytes),
            "world_manifest": _sha256(world_bytes),
        },
        "score": evidence["score"],
        "reward": native["verifier_result"]["rewards"]["reward"],
        "fingerprint": evidence["fingerprint"],
        "seats": seats,
        "manifest": manifest,
        "manifest_sha256": _sha256(_canonical(manifest)),
    }


def _export_panel(name: str, root: Path) -> dict[str, Any]:
    plan = _plan(_decode(_local(root, "plan.json").read_bytes(), "native plan"))
    runs = _object(
        _decode(_local(root, "runs.json").read_bytes(), "native runs index"),
        "native runs index",
    )
    if runs.get("schema") != RUNS_SCHEMA:
        raise ValueError("unsupported native runs index schema")
    rows = _array(runs.get("cells"), "native runs index cells")
    if len(rows) != len(plan["cells"]):
        raise ValueError("runs index cells do not match the planned grid")
    cells = []
    for value, cell in zip(rows, plan["cells"], strict=True):
        run = _object(value, "native run cell")
        _coordinates(run, cell)
        config = _trial_config(root, plan, cell)
        relative = (
            (Path(config["trials_dir"]) / config["trial_name"])
            .relative_to(root)
            .as_posix()
        )
        if run.get("trial_path") != relative:
            raise ValueError("run index path does not match the planned cell")
        trial = _local(root, relative)
        result = _optional_bytes(_local(trial, "result.json"))
        native = None
        if result is not None:
            native = _object(_decode(result, "native result"), "native result")
            _native_config(native, config)
        process = _process(trial, run)
        status = run.get("status")
        if status == "measured":
            if result is None or native is None or run.get("error") is not None:
                raise ValueError("measured cell lacks matching native output")
            measurement = _export_measurement(trial, plan, cell, run, result, native)
            cells.append({**cell, "status": status, "measurement": measurement})
        elif status == "unmeasured" and "evidence" in run and run["evidence"] is None:
            absence = _absence(trial, process, native)
            attempt = (
                _native_attempt(trial, plan, cell, result, native)
                if result is not None and native is not None
                else None
            )
            cells.append(
                {
                    **cell,
                    "status": status,
                    "absence": absence,
                    "process": process,
                    "native_attempt": attempt,
                }
            )
        else:
            raise ValueError("invalid native run status or evidence")
    _check_counts(runs, cells)
    return {"name": name, "plan": plan, "cells": cells, **_counts(cells)}


def export_bundle(panels: Iterable[tuple[str, Path]]) -> dict[str, Any]:
    """Re-read native panels, then return only public, verified study fields."""
    roots: dict[str, Path] = {}
    for label, root in panels:
        name = _panel_name(label)
        if name in roots:
            raise ValueError("duplicate panel name")
        root = root.resolve()
        if root in roots.values():
            raise ValueError("duplicate panel directory")
        roots[name] = root
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "panels": [_export_panel(name, root) for name, root in sorted(roots.items())],
    }
    verify_bundle(bundle)
    return bundle


def _panel_arg(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep or not path:
        raise argparse.ArgumentTypeError("expected NAME=DIR")
    return name, Path(path).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export public data from native panels")
    export.add_argument(
        "--panel", type=_panel_arg, action="append", required=True, metavar="NAME=DIR"
    )
    verify = commands.add_parser(
        "verify", help="check a bundle without its native inputs"
    )
    verify.add_argument("bundle", type=Path, metavar="BUNDLE")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            value = export_bundle(args.panel)
        else:
            value = verify_bundle(_decode(args.bundle.read_bytes(), "public bundle"))
        print(_canonical(value).decode("utf-8"))
    except (OSError, RuntimeError):
        parser.exit(1, "cannot read stable study inputs\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
