import json
import os
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.convention_crossplay import ballot_cohort, loader
from examples.seatlab.coupled_retention import strategy


def _save(path: Path, receipt: dict[str, Any]) -> None:
    path.write_bytes((json.dumps(receipt, sort_keys=True) + "\n").encode("ascii"))


def _receipt(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _enroll(root: Path, name: str) -> list[str]:
    entry = [name]
    for role in ("east", "west"):
        directory = root / name / role
        directory.mkdir(parents=True)
        table = strategy.table(role, {b"observation": "A"})
        (directory / "strategy.json").write_bytes(table)
        receipt = {
            "schema": "seatlab.role-ballot-result/v1",
            "role": role,
            "declared_source": name,
            "deck_sha256": loader.digest(f"deck:{name}:{role}".encode()),
            "reply_shard_sha256": [loader.digest(f"replies:{name}:{role}".encode())],
            "counts": {
                "expected": 1,
                "represented": 1,
                "valid": 1,
                "missing": 0,
                "duplicate_ids": 0,
                "stale_ids": 0,
                "invalid_ids": 0,
                "unknown_rows": 0,
            },
            "complete": True,
            "table_sha256": loader.digest(table),
            "scope": "One recorded choice per exact role observation, not verified first attempts, provenance, or live team rewards.",
        }
        _save(directory / "evidence.json", receipt)
        entry.append(str(directory))
    return entry


@pytest.fixture
def entries(tmp_path):
    return [_enroll(tmp_path, name) for name in ("first", "second")]


def _reject(entries):
    with pytest.raises(ValueError) as exc:
        ballot_cohort.load(entries)
    assert str(exc.value) == "invalid recorded-team cohort or role evidence"
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_keeps_distinct_recorded_teams_with_identical_choices(entries):
    teams, metadata = ballot_cohort.load(entries)
    assert [team.name for team in teams] == ["first", "second"]
    assert teams[0].east == teams[1].east == {strategy.digest(b"observation"): "A"}
    assert teams[0].west == teams[1].west == teams[0].east
    pins = [
        [
            name,
            loader.digest((Path(east) / "evidence.json").read_bytes()),
            loader.digest((Path(west) / "evidence.json").read_bytes()),
        ]
        for name, east, west in entries
    ]
    assert metadata == {
        "unit": "recorded_team",
        "cohort_sha256": loader.digest(
            b"seatlab.recorded-team-cohort/v1\x00"
            + json.dumps(pins, separators=(",", ":")).encode("ascii")
        ),
        "teams": [
            {"name": name, "east_evidence_sha256": east, "west_evidence_sha256": west}
            for name, east, west in pins
        ],
    }


def test_exact_evidence_bytes_and_cohort_order_change_pins(entries):
    teams, metadata = ballot_cohort.load(entries)
    reversed_teams, reordered = ballot_cohort.load(entries[::-1])
    assert reversed_teams == teams[::-1]
    assert reordered["cohort_sha256"] != metadata["cohort_sha256"]
    assert reordered["teams"] == metadata["teams"][::-1]
    evidence = Path(entries[0][1]) / "evidence.json"
    evidence.write_bytes(evidence.read_bytes() + b" ")
    same_teams, changed = ballot_cohort.load(entries)
    assert same_teams == teams
    assert changed["cohort_sha256"] != metadata["cohort_sha256"]
    assert (
        changed["teams"][0]["east_evidence_sha256"]
        != metadata["teams"][0]["east_evidence_sha256"]
    )
    assert (
        changed["teams"][0]["west_evidence_sha256"]
        == metadata["teams"][0]["west_evidence_sha256"]
    )
    assert changed["teams"][1] == metadata["teams"][1]


def test_stale_table_bytes_rejected_even_for_same_choices(entries):
    table = Path(entries[0][1]) / "strategy.json"
    table.write_bytes(table.read_bytes() + b"\n")
    _reject(entries)


def test_table_role_must_match_even_with_correct_pin(entries):
    directory = Path(entries[0][1])
    table = (Path(entries[0][2]) / "strategy.json").read_bytes()
    (directory / "strategy.json").write_bytes(table)
    evidence = directory / "evidence.json"
    receipt = _receipt(evidence)
    receipt["table_sha256"] = loader.digest(table)
    _save(evidence, receipt)
    _reject(entries)


def test_rejects_role_or_source_swaps(entries):
    name, east, west = entries[0]
    _reject([[name, west, east], entries[1]])
    _reject([[name, entries[1][1], west], entries[1]])


@pytest.mark.parametrize("same_role", [True, False])
def test_rejects_reused_deck_even_with_distinct_declared_source(entries, same_role):
    evidence = Path(entries[1][1]) / "evidence.json"
    receipt = _receipt(evidence)
    other = _receipt(Path(entries[0][1 if same_role else 2]) / "evidence.json")
    receipt["deck_sha256"] = other["deck_sha256"]
    _save(evidence, receipt)
    _reject(entries)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("expected", 2),
        ("represented", 0),
        ("valid", 0),
        ("valid", True),
        ("valid", 1.0),
        ("missing", 1),
        ("duplicate_ids", 1),
        ("stale_ids", 1),
        ("invalid_ids", 1),
        ("unknown_rows", 1),
        ("unknown_rows", False),
        ("extra", 0),
    ],
)
def test_rejects_partial_or_malformed_counts(entries, key, value):
    evidence = Path(entries[0][1]) / "evidence.json"
    receipt = _receipt(evidence)
    receipt["counts"][key] = value
    _save(evidence, receipt)
    _reject(entries)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema", "seatlab.role-ballot-result/v2"),
        ("complete", 1),
        ("complete", False),
        ("deck_sha256", "sha256:" + "A" * 64),
        ("reply_shard_sha256", []),
        ("reply_shard_sha256", ["invalid"]),
        ("reply_shard_sha256", ["sha256:" + "f" * 64, "sha256:" + "0" * 64]),
        ("reply_shard_sha256", ["sha256:" + "0" * 64] * 33),
        ("scope", []),
        ("scope", "x" * 513),
        ("extra", "private prompt"),
    ],
)
def test_rejects_invalid_receipt_shape(entries, key, value):
    evidence = Path(entries[0][1]) / "evidence.json"
    receipt = _receipt(evidence)
    receipt[key] = value
    _save(evidence, receipt)
    _reject(entries)


def test_duplicate_keys_rejected_even_with_identical_values(entries):
    evidence = Path(entries[0][1]) / "evidence.json"
    evidence.write_bytes(
        evidence.read_bytes().replace(
            b'"complete": true', b'"complete": true, "complete": true'
        )
    )
    _reject(entries)


def test_duplicate_names_and_cohort_bounds(entries):
    _reject(entries[:1])
    _reject(entries * 7)
    _reject([entries[0], [entries[0][0], *entries[1][1:]]])
    _reject([["unsafe/name", *entries[0][1:]], entries[1]])


@pytest.mark.parametrize("leaf", ["strategy.json", "evidence.json"])
@pytest.mark.parametrize(
    "kind", ["missing", "symlink", "fifo", "directory", "oversize"]
)
def test_rejects_unsafe_adjacent_files(entries, leaf, kind):
    path = Path(entries[0][1]) / leaf
    original = path.with_suffix(".saved")
    path.rename(original)
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "oversize":
        path.write_bytes(b"x" * (loader.MAX_FILE_BYTES + 1))
    _reject(entries)


@pytest.mark.parametrize("suffix", ["", "/", "/."])
def test_rejects_symlink_output_directory(entries, tmp_path, suffix):
    link = tmp_path / "linked"
    link.symlink_to(entries[0][1], target_is_directory=True)
    entries[0][1] = str(link) + suffix
    _reject(entries)
