import copy
from collections import Counter, defaultdict
from fractions import Fraction
from itertools import product
from pathlib import Path
from runpy import run_path

from examples.seatlab.exhibit_wire import encode_request, render_turn
from examples.seatlab.joint_handoffs import stage
from examples.seatlab.physical_dispatch import policies
from tests.unit.examples.test_seat_physical_server import (
    _native,
    _rpc,
    _seat_view,
    _server,
)

MAPS = ("original", "swapped")
CHANNELS = ("neither", "registrar", "depot", "both")
ROLES = ("registrar", "depot")


def _lookup(records, key, epoch):
    (value,) = [
        card["value"]
        for card in records
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    return value


def _route(order, registrar, depot):
    return {
        item: _lookup(depot, _lookup(registrar, item, order["epoch"]), order["epoch"])
        for item in sorted(order["items"])
    }


def _view_for(full, channel):
    expected = copy.deepcopy(full)
    for role in ROLES:
        if channel not in (role, "both"):
            for record in expected[role + "_file"]:
                value = record["value"]
                assert value.isascii() and "?" not in value
                record["value"] = "?" * len(value.encode())
    return expected


def _capture(task, seed, bay, channels, reference):
    _, grade = _native()
    instruction = (task / "instructions/loader.md").read_text()
    get = {"op": "view"}
    with _server(task, seed, bay) as root:
        first = _rpc(root, "loader", get)
        nomination = policies.nominate(first)
        _rpc(root, "loader", nomination)
        registrar = _rpc(root, "registrar", get)
        registered = policies.registrar(registrar)
        _rpc(root, "registrar", registered)
        depot = _rpc(root, "depot", get)
        filed = policies.depot(depot, "relevant")
        _rpc(root, "depot", filed)
        view = _seat_view(task, root, "loader")
        packet = encode_request(render_turn(instruction, view, nomination))
        action = reference(view)
        routes = action["routes"]
        assert set(routes.values()) <= set(view["bays"])
        if channels == "both":
            assert routes == _route(
                view["order"], view["registrar_file"], view["depot_file"]
            )
        done = _rpc(root, "loader", action)
        assert done == _rpc(root, "loader", get)
        cut = _rpc(root, "control", {"op": "freeze"})
    case = cut["case"]
    truth = _route(view["order"], case["registrar"], case["depot"])
    result = grade(cut)
    assert result["reward"] == int(routes == truth)
    assert result["status"] == "complete" and result["correct_items"] == sum(
        routes[key] == value for key, value in truth.items()
    )
    assert result["chains_supported"] == 2
    if channels == "both":
        assert result["reward"] == 1
    return (
        first,
        registrar,
        depot,
        view,
        packet,
        done,
        cut["events"][:3],
        tuple(truth.values()),
    )


def test_staged_service_distinguishes_joint_value_without_changing_physical_grade(
    tmp_path: Path, monkeypatch
):
    task = tmp_path / "warehouse"
    staged = stage.materialize(task)
    assert stage.check_stage(task) == staged["stage_digest"]
    make_case, _ = _native()
    for seed in range(7, 135):
        base = make_case(seed, "fork", layout="draw")
        if min(brief["id"] for brief in base["briefs"]) == base["realized"]:
            break
    else:
        raise AssertionError("no naturally eligible public draw")
    reference = run_path(str(task / "solution/policies.py"))["act"]
    runs = {}
    for registry, bay, channels in product(MAPS, MAPS, CHANNELS):
        monkeypatch.setenv("EXHIBIT_REGISTRY_MAP", registry)
        monkeypatch.setenv("EXHIBIT_CHANNELS", channels)
        runs[registry, bay, channels] = _capture(task, seed, bay, channels, reference)
    common = runs["original", "original", "both"]
    assert len({len(run[4]) for run in runs.values()}) == 1
    for (registry, bay, channels), run in runs.items():
        full = runs[registry, bay, "both"]
        assert run[0] == common[0] and run[6] == common[6]
        assert run[1] == runs[registry, "original", "both"][1]
        assert run[2] == runs["original", bay, "both"][2]
        assert run[3] == _view_for(full[3], channels)
        assert run[5] == _view_for(full[5], channels)
        assert run[7] == full[7]
    assert (
        runs["original", "original", "both"][1]
        != runs["swapped", "original", "both"][1]
    )
    assert (
        runs["original", "original", "both"][2]
        != runs["original", "swapped", "both"][2]
    )
    for channel, classes, bound in (
        ("neither", 1, Fraction(1, 2)),
        ("registrar", 2, Fraction(1, 2)),
        ("depot", 2, Fraction(1, 2)),
        ("both", 4, Fraction(1)),
    ):
        observed = defaultdict(Counter)
        for registry, bay in product(MAPS, MAPS):
            record = runs[registry, bay, channel]
            observed[record[4]][record[7]] += 1
        assert len(observed) == classes
        assert (
            Fraction(
                sum(max(frequencies.values()) for frequencies in observed.values()), 4
            )
            == bound
        )
