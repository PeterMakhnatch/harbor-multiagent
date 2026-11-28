import hashlib
import json
import os
from pathlib import Path

import pytest

from examples.seatlab.convention_crossplay import loader
from examples.seatlab.convention_crossplay.records import Team
from examples.seatlab.coupled_retention import strategy


def _reference(path: Path, raw: bytes) -> dict[str, str]:
    path.write_bytes(raw)
    return {"file": path.name, "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def cohort(tmp_path):
    east = _reference(tmp_path / "east.json", strategy.table("east", {b"e": "A"}))
    west = _reference(tmp_path / "west.json", strategy.table("west", {b"w": "B"}))
    other = _reference(tmp_path / "other.json", strategy.table("west", {b"w": "C"}))
    roster = {
        "schema": loader.SCHEMA,
        "teams": [
            {"name": "first", "east": east, "west": west},
            {"name": "second", "east": dict(east), "west": other},
        ],
    }
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster))
    return path, roster


def _save(path, roster):
    path.write_text(json.dumps(roster))


def _reject_roster(path):
    with pytest.raises(ValueError) as exc:
        loader.load(str(path))
    assert str(exc.value) == "invalid crossplay roster or pinned strategy"
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_load_preserves_order_and_exact_rules_with_shared_role(cohort):
    path, roster = cohort
    east = {strategy.digest(b"e"): "A"}
    west = {strategy.digest(b"w"): "B"}
    other = {strategy.digest(b"w"): "C"}
    assert loader.load(str(path)) == [
        Team("first", east, west),
        Team("second", east, other),
    ]
    roster["teams"][1]["west"] = dict(roster["teams"][0]["west"])
    _save(path, roster)
    assert loader.load(str(path)) == [
        Team("first", east, west),
        Team("second", east, west),
    ]


def test_raw_byte_pin_detects_semantically_identical_change(cohort):
    path, _ = cohort
    role = path.with_name("east.json")
    raw = role.read_bytes()
    changed = raw + b"\n"
    assert strategy.load(raw, "east") == strategy.load(changed, "east")
    role.write_bytes(changed)
    _reject_roster(path)


def test_role_mismatch_with_correct_pin(cohort):
    path, roster = cohort
    roster["teams"][0]["east"] = roster["teams"][0]["west"]
    _save(path, roster)
    _reject_roster(path)


@pytest.mark.parametrize(
    "raw_transform",
    [
        lambda raw: raw.replace(b'"schema":', b'"schema":"wrong","schema":', 1),
        lambda raw: raw.replace(b'"name":', b'"name":"wrong","name":', 1),
        lambda raw: raw.replace(b'"file":', b'"file":"wrong","file":', 1),
        lambda raw: raw + b"{}",
        lambda raw: b"\xff" + raw,
    ],
)
def test_rejects_malformed_or_duplicate_roster_json(cohort, raw_transform):
    path, _ = cohort
    path.write_bytes(raw_transform(path.read_bytes()))
    _reject_roster(path)


def test_rejects_duplicate_strategy_fields_even_with_correct_pin(cohort):
    path, roster = cohort
    role = path.with_name("east.json")
    raw = role.read_bytes().replace(b'"role":"east"', b'"role":"west","role":"east"')
    roster["teams"][0]["east"] = _reference(role, raw)
    roster["teams"][1]["east"] = dict(roster["teams"][0]["east"])
    _save(path, roster)
    _reject_roster(path)


@pytest.mark.parametrize(
    "name", ["", ".", "..", "has space", "slash/name", "é", "x" * 65]
)
def test_rejects_unsafe_names(cohort, name):
    path, roster = cohort
    roster["teams"][0]["name"] = name
    _save(path, roster)
    _reject_roster(path)


def test_rejects_duplicate_names_and_out_of_range_cohort(cohort):
    path, roster = cohort
    original = roster["teams"]
    roster["teams"] = [original[0], dict(original[1], name=original[0]["name"])]
    _save(path, roster)
    _reject_roster(path)
    for size in (0, 1, 13):
        roster["teams"] = [
            dict(original[0], name=f"team{index}") for index in range(size)
        ]
        _save(path, roster)
        _reject_roster(path)
    roster["teams"] = [dict(original[0], name=f"team{index}") for index in range(12)]
    _save(path, roster)
    assert len(loader.load(str(path))) == 12


@pytest.mark.parametrize(
    "name", [".", "..", "./east.json", "../east.json", "sub/east.json", "bad\\name"]
)
def test_rejects_non_basename_references(cohort, name):
    path, roster = cohort
    roster["teams"][0]["east"]["file"] = name
    _save(path, roster)
    _reject_roster(path)


def test_rejects_absolute_and_escaping_references(cohort):
    path, roster = cohort
    role = path.with_name("east.json")
    outside = path.parent / "outside"
    outside.mkdir()
    external = outside / role.name
    external.write_bytes(role.read_bytes())
    nested = path.parent / "nested"
    nested.mkdir()
    for filename in ("east.json", "west.json", "other.json"):
        (nested / filename).write_bytes(path.with_name(filename).read_bytes())
    roster_path = nested / path.name
    _save(roster_path, roster)
    assert loader.load(str(roster_path)) == loader.load(str(path))
    for name in (str(external), "../outside/east.json"):
        roster["teams"][0]["east"]["file"] = name
        _save(roster_path, roster)
        _reject_roster(roster_path)


@pytest.mark.parametrize("pin", ["", "sha256:" + "A" * 64, "sha256:" + "0" * 64, None])
def test_rejects_invalid_or_wrong_pins(cohort, pin):
    path, roster = cohort
    roster["teams"][0]["east"]["sha256"] = pin
    _save(path, roster)
    _reject_roster(path)


@pytest.mark.parametrize("kind", ["missing", "symlink", "fifo", "directory"])
@pytest.mark.parametrize("target", ["roster.json", "east.json"])
def test_never_reads_non_regular_files(cohort, kind, target):
    path, _ = cohort
    target_path = path.with_name(target)
    original = target_path.with_suffix(".original")
    target_path.rename(original)
    if kind == "symlink":
        target_path.symlink_to(original.name)
    elif kind == "fifo":
        os.mkfifo(target_path)
    elif kind == "directory":
        target_path.mkdir()
    _reject_roster(path)


def test_checks_roster_and_strategy_size_bounds(cohort):
    path, roster = cohort
    raw = path.read_bytes()
    path.write_bytes(raw.ljust(loader.MAX_FILE_BYTES, b" "))
    assert len(loader.load(str(path))) == 2
    path.write_bytes(raw.ljust(loader.MAX_FILE_BYTES + 1, b" "))
    _reject_roster(path)
    role = path.with_name("east.json")
    raw = role.read_bytes().ljust(loader.MAX_FILE_BYTES + 1, b" ")
    roster["teams"][0]["east"] = _reference(role, raw)
    _save(path, roster)
    _reject_roster(path)


def test_rejects_stdin_and_empty_path():
    for path in ("-", ""):
        _reject_roster(path)
