import importlib
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PACKAGE = "examples.tasks.exhibit-relay"
ENVIRONMENT = (
    Path(__file__).resolve().parents[3] / "examples/tasks/exhibit-relay/environment"
)
sys.path.insert(0, str(ENVIRONMENT))
serve = importlib.import_module(f"{PACKAGE}.environment.serve")
sys.path.remove(str(ENVIRONMENT))
relay = importlib.import_module(f"{PACKAGE}.environment.seat.relay")
verify = importlib.import_module(f"{PACKAGE}.tests.verify")
cases = importlib.import_module(f"{PACKAGE}.environment.exhibit_relay.cases")
engine = importlib.import_module(f"{PACKAGE}.environment.exhibit_relay.engine")
ROLES = ("loader", "registrar", "depot")


class Seat:
    def __init__(self, role: str, path: Path, monkeypatch, capsys) -> None:
        self.role = role
        self.path = path
        self.monkeypatch = monkeypatch
        self.capsys = capsys

    def call(self, *args: str, rejected: bool = False) -> dict[str, Any]:
        with self.monkeypatch.context() as patch:
            patch.setattr(relay, "SOCKET", str(self.path))
            patch.setattr(sys, "argv", ["relay.py", *args])
            status = relay.main()
        output = self.capsys.readouterr()
        if rejected:
            assert status != 0
            assert not output.out
            assert bool(output.err)
            return {}
        assert status == 0
        assert not output.err
        view = json.loads(output.out)
        assert view["role"] == self.role
        assert set(view) == {
            "role",
            "phase",
            "turn",
            "quota",
            "nominate",
            "exhibits",
            "crates",
            "bays",
            "briefs",
            "cards",
            "agenda",
            "registrar_file",
            "depot_file",
            "order",
        }
        return view


def covering_value(cards: list[dict[str, Any]], key: str, epoch: int) -> str:
    matches = [
        card["value"]
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    assert len(matches) == 1
    return matches[0]


def full_information_dispatch(
    seats: dict[str, Seat],
) -> tuple[dict[str, Any], dict[str, str]]:
    loader, registrar, depot = (seats[role] for role in ROLES)
    initial = loader.call("view")
    assert initial["phase"] == "request"
    assert not initial["cards"] and initial["order"] is None
    registrar.call("file", rejected=True)
    depot.call("request", rejected=True)
    loader.call("request", *(brief["id"] for brief in initial["briefs"]))
    source = registrar.call("wait", "registrar")
    assert len(source["briefs"]) == len(initial["briefs"])
    assert source["order"] is None
    registrar.call("file", *(card["id"] for card in source["cards"]))
    store = depot.call("wait", "depot")
    assert not store["briefs"] and store["order"] is None
    assert bool(store["agenda"])
    depot.call("file", *(card["id"] for card in store["cards"]))
    dispatch = loader.call("wait", "dispatch")
    order = dispatch["order"]
    assert order is not None
    assert len(dispatch["registrar_file"]) == len(source["cards"])
    assert len(dispatch["depot_file"]) == len(store["cards"])
    routes = {}
    for exhibit in order["items"]:
        crate = covering_value(dispatch["registrar_file"], exhibit, order["epoch"])
        routes[exhibit] = covering_value(dispatch["depot_file"], crate, order["epoch"])
    return dispatch, routes


def check_native(module: ModuleType, logs: Path, outcome: str, capsys) -> None:
    status = module.main()
    output = capsys.readouterr()
    expected = {"correct": 1, "wrong": 0, "incomplete": None}[outcome]
    observation = json.loads((logs / "observation.json").read_text())
    assert observation["reward"] == expected
    assert observation["total_items"] == 2
    assert observation["mode"] == "open"
    assert "case" not in observation and "events" not in observation
    reward_path = logs / "reward.json"
    metrics = (
        "correct_items",
        "registrar_supported",
        "depot_supported",
        "chains_supported",
        "dispatch_supported",
    )
    if expected is None:
        assert status != 0
        assert observation["status"] == "incomplete"
        assert all(observation[key] is None for key in metrics)
        assert not reward_path.exists()
        assert not output.out
        assert bool(output.err)
    else:
        assert status == 0
        assert observation["status"] == "complete"
        assert observation["correct_items"] == 2 * expected
        assert observation["dispatch_supported"] == 2 * expected
        assert all(observation[key] == 2 for key in metrics[1:4])
        assert json.loads(reward_path.read_text()) == {"reward": expected}
        assert output.out.strip() == f"reward={expected}"
        assert not output.err


@pytest.mark.parametrize("profile", ["fork", "mesh"])
@pytest.mark.parametrize("outcome", ["correct", "wrong", "incomplete"])
def test_native_relay_transport(profile, outcome, monkeypatch, capsys) -> None:
    case = cases.make_case(731, profile)
    with tempfile.TemporaryDirectory(prefix="xr-", dir="/tmp") as directory:
        base = Path(directory)
        root, control, logs = base / "r", base / "c", base / "logs"
        seats = {
            role: Seat(role, root / role / "rpc.sock", monkeypatch, capsys)
            for role in ROLES
        }
        monkeypatch.setattr(verify, "CONTROL", str(control / "rpc.sock"))
        monkeypatch.setattr(verify, "LOGS", logs)
        monkeypatch.setattr(sys, "path", [str(ENVIRONMENT), *sys.path])
        with serve.Family(root, control, engine.Session(case, "open")):
            dispatch, routes = full_information_dispatch(seats)
            if outcome == "wrong":
                bays = dispatch["bays"]
                routes = {
                    exhibit: bays[(bays.index(bay) + 1) % len(bays)]
                    for exhibit, bay in routes.items()
                }
            arguments = tuple(f"{exhibit}={bay}" for exhibit, bay in routes.items())
            if outcome != "incomplete":
                assert seats["loader"].call("commit", *arguments)["phase"] == "done"
                assert seats["depot"].call("wait", "done")["phase"] == "done"
            check_native(verify, logs, outcome, capsys)
            frozen = verify.freeze()
            assert len(frozen["events"]) == (3 if outcome == "incomplete" else 4)
            seats["loader"].call("commit", *arguments, rejected=True)
            phase = "dispatch" if outcome == "incomplete" else "done"
            assert seats["loader"].call("view")["phase"] == phase
            if verify.freeze() != frozen:
                raise AssertionError("Repeated freeze changed the evidence")
        assert not any(seat.path.exists() for seat in seats.values())
        assert not (control / "rpc.sock").exists()
