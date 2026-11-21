import os
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.rolepack import (
    calendar_source,
    native_subset,
    protocol,
    shipment_source,
)

_RESULTS = Path(__file__).resolve().parents[1] / "results"


def _public(name: str) -> protocol.Document:
    return protocol.loads((_RESULTS / name).read_bytes())


@pytest.mark.parametrize("task", ["calendar", "shipment"])
@pytest.mark.parametrize("alteration", ["source", "world", "role", "input", "rubric"])
def test_alternate_peer_does_not_allow_changes_to_original_evidence(task, alteration):
    original = [
        r for r in _public("rolepack-observations.json")["records"] if r["task"] == task
    ]
    frozen = next(
        r for r in _public("rolepack-from-inspect.json")["records"] if r["task"] == task
    )
    changed = deepcopy(frozen)
    if alteration == "source":
        changed["id"] += "-another-case"
    elif alteration == "world":
        changed["world"] = "foreign-world"
    elif alteration == "role":
        changed["task"] = "a-different-role"
    elif alteration == "input":
        changed["prompt"] += "\nNew instruction"
        changed["prompt_sha256"] = sha256(changed["prompt"].encode()).hexdigest()
    else:
        item = changed["rubric"]["items"][0]
        item["optimal"] = [next(x for x in item["valid"] if x not in item["optimal"])]
    # Both records remain valid portable role records. Only the first has the enrolled evidence.
    for record in (frozen, changed):
        protocol.validate({"schema": protocol.SCHEMA, "records": [record]})
    matched = native_subset.select_registered_subset([frozen], original, task=task)
    assert matched[frozen["id"]]["peer"] != frozen["peer"]
    with pytest.raises(ValueError):
        native_subset.select_registered_subset([changed], original, task=task)


def test_regrade_new_peer_with_original_calendar():
    if not (path := os.environ.get("SEATLAB_CALENDAR_ROOT")):
        pytest.skip("set SEATLAB_CALENDAR_ROOT for the pinned original task")
    pack, report = (
        _public("rolepack-from-inspect.json"),
        _public("rolepack-import-review-live.json"),
    )
    root = Path(path).absolute()
    with pytest.raises(ValueError):
        calendar_source.check(root, pack, report)
    assert calendar_source.check(root, pack, report, allow_repeer=True) == {
        "sources_checked": 3,
        "actions_checked": 12,
        "optimal_actions": 0,
    }


def test_regrade_new_peer_with_original_shipment_worlds():
    if not (path := os.environ.get("SEATLAB_NATIVE_SHIPMENT_ROOT")):
        pytest.skip("set SEATLAB_NATIVE_SHIPMENT_ROOT for native Harbor artifacts")
    pack, report = (
        _public("rolepack-from-inspect.json"),
        _public("rolepack-import-review-live.json"),
    )
    data = shipment_source.check(
        _RESULTS, pack, report, Path(path).absolute(), allow_repeer=True
    )
    assert data["source_teams"] == 6
    assert data["native_original"]["episodes"] == 36
    assert [(v["actions"], v["optimal"]) for v in data["conditional"].values()] == [
        (72, 66),
        (72, 66),
    ]
