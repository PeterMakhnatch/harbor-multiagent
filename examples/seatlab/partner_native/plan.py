import os
import secrets
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from ...joint_reopen import design, study, support
from ...native_evidence import Record
from ...native_study import (
    DIGEST,
    NATIVE_DEFAULTS,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    native,
    record_bytes,
    require,
    task_files_digest,
    task_identity,
)
from ...seat_checkpoints.materialize import sync_directory
from ...seat_handoffs.native_records import _parse
from ...ship_inflight.materialize import publish
from ..convention_crossplay.records import team_name
from ..exhibit_enroll import digest, target, write_private
from ..partner_switch import engine
from ..partner_switch.planio import _directory, _read

SCHEMA = "seatlab.partner-native/v1"
TASK = {
    **support.TASK,
    "digest": "sha256:b1139fff1518ef5a21b321eaeacd3bc56466d475b85d41f2ec6c7dccc3c99cef",
}
MODES = ("scripted-same", "scripted-different", "native-rosters")
CONTROL_NAMES = ("alpha", "beta")
MAX_NAMES = 5
CASES = 6
MAX_MANIFEST_BYTES = 32 << 10
MAX_REGISTRATION_BYTES = study.MAX_REGISTRATION_BYTES
MANIFEST_FIELDS = frozenset({"schema", "start", "names", "mode", "registration_sha256"})


def _names(value: object) -> list[str]:
    names = [team_name(name) for name in as_list(value, "names", 2, MAX_NAMES)]
    require(len(set(names)) == len(names), "duplicate team name")
    return names


def _pair(east: int, west: int) -> str:
    return f"p{east:02}-{west:02}"


def _pairs(count: int) -> list[tuple[int, int]]:
    return [(east, west) for east in range(count) for west in range(count)]


def _control_member(role: str, mode: str, index: int, active: str) -> dict[str, Any]:
    direction = "reverse" if mode == "scripted-different" and index == 1 else "forward"
    return {
        "seat": role,
        "import_path": study.OWNER_IMPORT_PATH,
        "skip_model_parsing": True,
        "model_name": f"seatlab-switch/{direction}-{active}",
        **deepcopy(NATIVE_DEFAULTS),
    }


def _cohort(start: int) -> tuple[tuple[int, ...], str]:
    as_int(start, "start", 0, study.MAX_SEED)
    cases = engine.cases(start)
    seeds = tuple(case.seed for case in cases)
    require(len(seeds) == len(set(seeds)) == CASES, "incomplete native seed cohort")
    return seeds, "".join(cases[0].active)


def _validate(manifest: object, registration: object) -> None:
    fields = as_record(manifest, "manifest", MANIFEST_FIELDS)
    require(fields["schema"] == SCHEMA, "invalid partner-native schema")
    names = _names(fields["names"])
    mode = as_choice(fields["mode"], "mode", MODES)
    as_text(fields["registration_sha256"], "registration pin", DIGEST)
    if mode != "native-rosters":
        require(tuple(names) == CONTROL_NAMES, "invalid control team names")
    seeds, active = _cohort(fields["start"])
    checked = study.validate_registration(registration)
    require(
        checked["task"] == TASK, "registration does not name the pinned native task"
    )
    count = len(names)
    pairs = _pairs(count)
    teams = checked["teams"]
    require(set(teams) == {_pair(i, j) for i, j in pairs}, "invalid pair teams")
    diagonal = [teams[_pair(i, i)] for i in range(count)]
    for i, members in enumerate(diagonal):
        require(
            [member["seat"] for member in members] == list(engine.ROLES),
            "invalid role order",
        )
        if mode != "native-rosters":
            expected_members = [
                _control_member(role, mode, i, active) for role in engine.ROLES
            ]
            require(members == expected_members, "invalid control roster")
    for i, j in pairs:
        require(
            teams[_pair(i, j)] == [diagonal[i][0], diagonal[j][1]],
            "pair role configuration differs from its diagonal",
        )
    expected = {
        (_pair(i, j), seed, "both", "deliver") for i, j in pairs for seed in seeds
    }
    cells = checked["cells"]
    actual = {(c["team"], c["seed"], c["fixture"], c["channel"]) for c in cells}
    require(
        len(cells) == len(expected) and actual == expected,
        "invalid native cell layout",
    )


def build(
    start: int, teams: list[tuple[str, Path]] | None, control: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    require((teams is None) != (control is None), "select exactly one roster mode")
    seeds, active = _cohort(start)
    if teams is None:
        mode = "scripted-" + as_choice(control, "control", ("same", "different"))
        names = list(CONTROL_NAMES)
        raws = [
            [_control_member(role, mode, i, active) for role in engine.ROLES]
            for i in range(len(names))
        ]
    else:
        mode = "native-rosters"
        require(2 <= len(teams) <= MAX_NAMES, "select 2..5 explicit teams")
        names = _names([name for name, _ in teams])
        raws = [design._read_participants(path) for _, path in teams]
    harbor = native()
    rosters = [design._normalize_team(harbor, raw) for raw in raws]
    for roster in rosters:
        roster.sort(key=lambda member: member["seat"])
    before = task_files_digest(study.TASK_DIR, TASK)
    task = task_identity(harbor, study.TASK_DIR, study.TASK_NAME)
    require(
        task == TASK,
        "the installed fork does not package the registered role instructions",
    )
    require(
        before == task_files_digest(study.TASK_DIR, TASK),
        "task changed during registration",
    )
    count = len(names)
    paired = {
        _pair(i, j): deepcopy([rosters[i][0], rosters[j][1]]) for i, j in _pairs(count)
    }
    conditions = [(pair, seed) for pair in paired for seed in seeds]
    secrets.SystemRandom().shuffle(conditions)
    tickets = design._draw_tickets(len(conditions))
    registration = {
        "schema": study.SCHEMA,
        "task": task,
        "teams": paired,
        "cells": [
            {
                "ticket": ticket,
                "team": pair,
                "seed": seed,
                "fixture": "both",
                "channel": "deliver",
            }
            for ticket, (pair, seed) in zip(tickets, conditions, strict=True)
        ],
    }
    manifest = {
        "schema": SCHEMA,
        "start": start,
        "names": names,
        "mode": mode,
        "registration_sha256": digest(record_bytes(registration)),
    }
    _validate(manifest, registration)
    return manifest, registration


def save(root: Path, manifest: Record, registration: Record) -> str:
    _validate(manifest, registration)
    raw_registration = record_bytes(registration)
    raw_manifest = record_bytes(manifest)
    require(
        len(raw_registration) <= MAX_REGISTRATION_BYTES,
        "registration exceeds size bound",
    )
    require(len(raw_manifest) <= MAX_MANIFEST_BYTES, "manifest exceeds size bound")
    require(
        manifest["registration_sha256"] == digest(raw_registration),
        "registration pin differs",
    )
    pin = digest(raw_manifest)
    try:
        out = target(root)
        with tempfile.TemporaryDirectory(
            prefix=".seatlab-partner-native-", dir=out.parent
        ) as temp:
            staged = Path(temp) / "study"
            staged.mkdir(mode=0o700)
            os.chmod(staged, 0o700)
            write_private(staged / "manifest.json", raw_manifest)
            write_private(staged / "registration.json", raw_registration)
            sync_directory(staged)
            publish(staged, out)
            sync_directory(out.parent)
    except OSError as error:
        raise ValueError("cannot publish partner-native plan") from error
    return pin


def load(root: Path, expected_pin: str) -> tuple[Record, Record]:
    try:
        as_text(expected_pin, "manifest pin", DIGEST)
        directory = _directory(root)
        try:
            raw_manifest = _read(directory, "manifest.json", MAX_MANIFEST_BYTES)
            require(digest(raw_manifest) == expected_pin, "manifest pin differs")
            manifest = as_record(_parse(raw_manifest), "manifest", MANIFEST_FIELDS)
            registration_pin = as_text(
                manifest["registration_sha256"], "registration pin", DIGEST
            )
            raw_registration = _read(
                directory, "registration.json", MAX_REGISTRATION_BYTES
            )
            require(
                digest(raw_registration) == registration_pin, "registration pin differs"
            )
            registration = _parse(raw_registration)
            _validate(manifest, registration)
            return manifest, registration
        finally:
            os.close(directory)
    except (OSError, ValueError, RecursionError) as error:
        raise ValueError("invalid partner-native plan or pin") from error
