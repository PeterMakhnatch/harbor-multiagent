from copy import deepcopy
from itertools import product
from typing import Any

import pytest

from examples.seatlab.decision_boundary.core import World
from examples.seatlab.joint_handoffs import world
from examples.seatlab.joint_handoffs.packet_policy import candidates, policy_values

BAYS = ["bay-h", "bay-c", "bay-a", "bay-f", "bay-b", "bay-g", "bay-d", "bay-e"]
CRATES = ["crate-p", "crate-k", "crate-a", "crate-x", "crate-b", "crate-c", "crate-d"]
Map = tuple[str, str]
Record = dict[str, Any]
KEYS = [(r, b) for r, b in product(world.MAPS, world.MAPS)]
OO, SO, OS, SS = KEYS[0], KEYS[2], KEYS[1], KEYS[3]
WINNERS = {OO: "2:7", SO: "7:2", OS: "7:2", SS: "2:7"}


def _card(ident: str, key: str, value: str) -> Record:
    return {"id": ident, "key": key, "value": value, "start": 3, "end": 9}


def _final(registry: str, bay: str) -> Record:
    crates = ["crate-p", "crate-k"][:: -1 if registry == "swapped" else 1]
    bays = ["bay-h", "bay-c"][:: -1 if bay == "swapped" else 1]
    return {
        "role": "loader",
        "phase": "dispatch",
        "turn": "loader",
        "order": {"id": "brief-2", "epoch": 5, "items": ["ex-z", "ex-m"]},
        "bays": list(BAYS),
        "crates": list(CRATES),
        "registrar_file": [
            _card("r-9", "ex-z", crates[0]),
            _card("r-4", "ex-m", crates[1]),
        ],
        "depot_file": [
            _card("d-7", "crate-p", bays[0]),
            _card("d-2", "crate-k", bays[1]),
        ],
    }


def _rows(rewarded: dict[Map, set[str]]) -> dict[Map, World]:
    rows = {}
    for key, labels in rewarded.items():
        scores = {f"{left}:{right}": 0 for left, right in product(range(8), repeat=2)}
        scores.update({label: 1 for label in labels})
        rows[key] = World("/".join(key), 1, b"packet", scores)
    return rows


def _finals() -> dict[Map, Record]:
    return {key: _final(*key) for key in KEYS}


def _arms(report: Record) -> Record:
    return {
        channel: (arm["rules"], arm["candidate_actions"], arm["value"])
        for channel, arm in report["arms"].items()
    }


def test_labels_follow_sorted_names_not_view_order():
    finals = _finals()
    for key, label in WINNERS.items():
        assert candidates(world.delivery(finals[key], "both")) == ("chains", [label])
        rule, labels = candidates(world.delivery(finals[key], "depot"))
        assert rule == "destinations" and sorted(labels) == ["2:7", "7:2"]
        for channel in ("neither", "registrar"):
            rule, labels = candidates(world.delivery(finals[key], channel))
            assert rule == "public" and len(labels) == 56
            assert "2:2" not in labels and "0:1" in labels and "1:0" in labels


def test_values_average_the_given_rows():
    rewarded = {
        OO: {"2:7", "0:1", "2:2"},
        SO: {"7:2", "2:7"},
        OS: {"7:2"},
        SS: {"2:7", "3:4", "5:6"},
    }
    report = policy_values(_finals(), _rows(rewarded))
    assert _arms(report) == {
        "neither": (["public"], 224, "1/28"),
        "registrar": (["public"], 224, "1/28"),
        "depot": (["destinations"], 8, "5/8"),
        "both": (["chains"], 4, "1"),
    }
    assert report["model_calls"] == report["random_samples"] == 0


def test_changed_rows_change_values():
    rewarded = {
        OO: {"2:7", "0:1", "2:2"},
        SO: {"7:2", "2:7", "4:0"},
        OS: {"7:2", "2:7"},
        SS: {"7:2", "3:4", "5:6"},
    }
    assert _arms(policy_values(_finals(), _rows(rewarded))) == {
        "neither": (["public"], 224, "5/112"),
        "registrar": (["public"], 224, "5/112"),
        "depot": (["destinations"], 8, "3/4"),
        "both": (["chains"], 4, "3/4"),
    }


def _collide(view: Record) -> None:
    view["depot_file"][1]["value"] = view["depot_file"][0]["value"]


def _stranger(view: Record) -> None:
    view["registrar_file"][0]["key"] = "ex-q"


def _detached(view: Record) -> None:
    view["depot_file"][0]["key"] = "crate-a"


def _stale(view: Record) -> None:
    view["depot_file"][0]["start"] = 6


def _mixed(view: Record) -> None:
    view["depot_file"][0]["value"] = "?????"


def _wide(view: Record) -> None:
    view["order"]["items"].append("ex-b")


@pytest.mark.parametrize(
    "mutate", [_collide, _stranger, _detached, _stale, _mixed, _wide]
)
def test_unmatched_views_are_rejected(mutate):
    finals = _finals()
    mutate(finals[OS])
    rows = _rows({key: {label} for key, label in WINNERS.items()})
    with pytest.raises(ValueError):
        policy_values(finals, rows)


def test_unmatched_rows_are_rejected():
    rows = _rows({key: {label} for key, label in WINNERS.items()})
    short = deepcopy(rows)
    del short[SS].scores["0:0"]
    missing = {key: row for key, row in rows.items() if key != SS}
    for bad in (short, missing):
        with pytest.raises(ValueError):
            policy_values(_finals(), bad)
