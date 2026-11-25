import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

from examples.seatlab.exhibit_owner import _VIEW_KEYS, RelayOwner
from examples.seatlab.physical_dispatch import certificate, policies, stage
from examples.seatlab.physical_dispatch.route_evidence import SCHEMA
from examples.seatlab.physical_dispatch.team_history import replay_team
from harbor.models.agent.context import AgentContext
from tests.unit.examples import test_exhibit_relay_authored_owner_sockets as authored
from tests.unit.examples.test_exhibit_relay_owner import listener, serve_once
from tests.unit.examples.test_seat_physical_server import _rpc, _server

SEED = 10
MAP = "swapped"
ROLES = authored.ROLES
PHASES = authored.PHASES


def free_choice(payload):
    view = payload["view"]
    if view["phase"] == "request":
        ids = sorted((brief["id"] for brief in view["briefs"]), reverse=True)
        return {"slips": ids[:2], "op": "request"}
    if view["phase"] in ("registrar", "depot"):
        return {"ids": [], "op": "file"}
    bay = max(view["bays"])
    items = view["order"]["items"]
    return {"routes": {item: bay for item in reversed(items)}, "op": "commit"}


def test_free_team_native_wire(tmp_path, monkeypatch):
    task = tmp_path.resolve() / "task"
    digest = stage.materialize(task)["stage_digest"]
    monkeypatch.setattr(authored, "TASK", task)
    base, maps = certificate.native_pair(SEED)
    case = maps[MAP]
    assert case["depot"] != maps["original"]["depot"]
    assert case["briefs"] == base["briefs"]
    instructions = {
        role: (task / "instructions" / f"{role}.md").read_text(encoding="utf-8")
        for role in dict.fromkeys(ROLES)
    }
    contexts = {role: AgentContext() for role in instructions}
    with (
        _server(task, SEED, MAP) as server,
        tempfile.TemporaryDirectory(prefix="fw-", dir="/tmp") as directory,
        ExitStack() as stack,
    ):
        root = Path(directory).resolve()
        sockets = [listener(stack, root, phase) for phase in PHASES]
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=4))
        futures = [pool.submit(serve_once, sock, free_choice) for sock in sockets]
        owners = {
            role: RelayOwner(
                root,
                role=role,
                owner_sockets=[
                    str(root / phase)
                    for phase, turn in zip(PHASES, ROLES, strict=True)
                    if turn == role
                ],
            )
            for role in instructions
        }
        seats = {role: authored.NativeSeat(root, role) for role in instructions}
        for role, seat in seats.items():
            seat.endpoint = str(server / "relay" / role / "rpc.sock")
        asyncio.run(authored.run_team(owners, seats, contexts, instructions))
        turns = [future.result(timeout=2) for future in futures]
        cut = _rpc(server, "control", {"op": "freeze"})
    assert stage.check_stage(task) == digest

    payloads, actions, stamps = map(list, zip(*turns, strict=True))
    views = [payload["view"] for payload in payloads]
    assert all(
        payload.keys() == {"instruction", "view", "previous_action"}
        for payload in payloads
    )
    assert all(view.keys() == _VIEW_KEYS for view in views)
    assert [(v["role"], v["phase"], v["turn"]) for v in views] == [
        (role, phase, role) for role, phase in zip(ROLES, PHASES, strict=True)
    ]
    assert [p["instruction"] for p in payloads] == [instructions[r] for r in ROLES]
    assert len(set(instructions.values())) == 3

    request, registrar, depot, dispatch = views
    nominated = actions[0]["slips"]
    order = next(b for b in case["briefs"] if b["id"] == case["realized"])
    assert len(nominated) == 2 and nominated != sorted(nominated)
    assert policies.nominate(request)["slips"] != nominated
    assert actions[1] == actions[2] == {"ids": [], "op": "file"}
    assert policies.follow(dispatch) is None
    assert sorted(actions[3]["routes"]) == sorted(order["items"])
    assert len(set(actions[3]["routes"].values())) == 1
    assert request["briefs"] == dispatch["briefs"] == case["briefs"]
    assert registrar["briefs"] == sorted(
        (b for b in case["briefs"] if b["id"] in nominated), key=lambda b: b["id"]
    )
    assert depot["briefs"] == request["cards"] == dispatch["cards"] == []
    assert registrar["cards"] == case["registrar"]
    assert depot["cards"] == case["depot"]
    assert all(
        v["agenda"] == v["registrar_file"] == v["depot_file"] == [] for v in views
    )
    assert [v["order"] for v in views] == [None, None, None, order]

    assert [p["previous_action"] for p in payloads[:3]] == [None, None, None]
    previous = payloads[3]["previous_action"]
    assert previous == actions[0] and list(previous) == ["slips", "op"]
    assert previous["slips"] == nominated

    assert cut["case"] == case and cut["mode"] == "linked"
    assert [event["action"] for event in cut["events"]] == [
        {"op": "request", "slips": sorted(nominated)},
        {"op": "file", "ids": []},
        {"op": "file", "ids": []},
        {"op": "commit", "routes": actions[3]["routes"]},
    ]
    metadata = {role: context.metadata for role, context in contexts.items()}
    loader_meta = metadata["loader"]
    assert loader_meta is not None
    assert [list(a) for a in loader_meta["accepted_actions"]] == [
        ["slips", "op"],
        ["routes", "op"],
    ]
    replayed, evidence = replay_team(cut, metadata, instructions)
    assert replayed == [
        {"seq": index, "role": role, "phase": phase, "request": stamp}
        for index, (role, phase, stamp) in enumerate(
            zip(ROLES, PHASES, stamps, strict=True), 1
        )
    ]
    assert len(replayed) == 4
    assert evidence is not None
    assert {
        k: evidence[k]
        for k in (
            "schema",
            "delivered_chain_items",
            "legal_actions",
            "card_functions",
            "generator_bijections",
        )
    } == {
        "schema": SCHEMA,
        "delivered_chain_items": 0,
        "legal_actions": 64,
        "card_functions": {
            "compatible_routes": 64,
            "choice": "compatible_not_entailed",
        },
        "generator_bijections": {"compatible_routes": 56, "choice": "contradicted"},
    }
    result = certificate.grade(cut)
    assert result["status"] == "complete" and result["reward"] == 0
    assert result["chains_supported"] == result["dispatch_supported"] == 0
