import asyncio
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from importlib import import_module
from pathlib import Path
from typing import override

from examples.seatlab.exhibit_owner import RelayOwner
from examples.seatlab.exhibit_saved import packets
from examples.seatlab.exhibit_turn_receipt import reconcile
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from tests.unit.examples.test_exhibit_relay_owner import listener, serve_once

TASK = Path(__file__).resolve().parents[3] / "examples/tasks/exhibit-relay"
ROLES = ("loader", "registrar", "depot", "loader")
PHASES = ("request", "registrar", "depot", "dispatch")
MODE = "author-masked"


class NativeSeat(SeatEnvironment):
    def __init__(self, root: Path, role: str):
        self.service = role
        self.endpoint = str(root / "s" / role / "rpc.sock")

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        assert cwd is None and env is None and user is None
        prefix, script, *args = shlex.split(command)
        assert (prefix, script) == ("python3", "/app/relay.py")
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-BS",
                "-c",
                "import sys, relay; relay.SOCKET = sys.argv.pop(1); raise SystemExit(relay.main())",
                self.endpoint,
                *args,
            ],
            cwd=TASK / "environment/seat",
            capture_output=True,
            text=True,
            timeout=min(timeout_sec or 8, 8),
        )
        return ExecResult(
            return_code=result.returncode, stdout=result.stdout, stderr=result.stderr
        )


def scripted_owner(payload):
    view = payload["view"]
    phase = view["phase"]
    if phase == "request":
        assert len(view["briefs"]) == view["nominate"] == 4
        return {
            "slips": sorted((b["id"] for b in view["briefs"]), reverse=True),
            "op": "request",
        }
    if phase == "registrar":
        briefs = sorted(view["briefs"], key=lambda b: b["id"])
        notes = []
        for index, brief in enumerate(briefs[:2]):
            cards = [
                card
                for card in view["cards"]
                if card["key"] in brief["items"]
                and card["start"] <= brief["epoch"] <= card["end"]
            ]
            assert len(cards) == 2
            weight = brief["weight"] if index == 0 else 1 + brief["weight"] % 9
            notes.append(
                {
                    "weight": weight,
                    "keys": sorted((c["value"] for c in cards), reverse=True),
                    "epoch": brief["epoch"],
                }
            )
        return {"notes": notes, "ids": [], "op": "file"}
    if phase == "depot":
        return {"op": "file", "ids": [c["id"] for c in view["cards"][: view["quota"]]]}
    assert phase == "dispatch"
    return {
        "op": "commit",
        "routes": {item: view["bays"][0] for item in view["order"]["items"]},
    }


async def run_team(owners, seats, contexts, instructions):
    for role, owner in owners.items():
        await owner.setup(seats[role])
    async with asyncio.timeout(10):
        async with asyncio.TaskGroup() as group:
            for role, owner in owners.items():
                group.create_task(
                    owner.run(instructions[role], seats[role], contexts[role])
                )


def test_authored_owner_native_socket_receipts(monkeypatch):
    """Local wire integration only; no container isolation, provider identity, team harness or benchmark execution."""
    monkeypatch.syspath_prepend(str(TASK / "environment"))
    from exhibit_relay.cases import make_case
    from exhibit_relay.engine import Session
    from exhibit_relay.notes import audit_notes

    Family = import_module("examples.tasks.exhibit-relay.environment.serve").Family
    instructions = {
        role: (TASK / "instructions" / f"{role}.md").read_text(encoding="utf-8")
        for role in dict.fromkeys(ROLES)
    }
    session = Session(make_case(23, "coupling", layout="a"), MODE)
    with (
        tempfile.TemporaryDirectory(prefix="xa-", dir="/tmp") as directory,
        ExitStack() as stack,
    ):
        root = Path(directory).resolve()
        stack.enter_context(Family(root / "s", root / "c", session))
        sockets = [listener(stack, root, phase) for phase in PHASES]
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=4))
        futures = [pool.submit(serve_once, sock, scripted_owner) for sock in sockets]
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
        seats = {role: NativeSeat(root, role) for role in instructions}
        contexts = {role: AgentContext() for role in instructions}
        asyncio.run(run_team(owners, seats, contexts, instructions))
        turns = [future.result(timeout=2) for future in futures]
        cut = session.snapshot()

    payloads, actions, stamps = map(list, zip(*turns, strict=True))
    assert [(p["view"]["role"], p["view"]["phase"]) for p in payloads] == list(
        zip(ROLES, PHASES, strict=True)
    )
    assert [p["instruction"] for p in payloads] == [
        instructions[role] for role in ROLES
    ]
    assert [p["previous_action"] for p in payloads[:3]] == [None, None, None]
    previous = payloads[3]["previous_action"]
    assert list(previous) == list(actions[0]) == ["slips", "op"]
    assert previous == actions[0]
    assert previous["slips"] == cut["events"][0]["action"]["slips"][::-1]
    metadata = {role: ctx.metadata for role, ctx in contexts.items()}
    for role, entry in metadata.items():
        assert entry is not None
        indices = [i for i, turn in enumerate(ROLES) if turn == role]
        assert entry["role"] == role and entry["phase"] == "finished"
        assert entry["accepted_requests"] == [stamps[i] for i in indices]
        assert entry["accepted_actions"] == [actions[i] for i in indices]
    assert reconcile(cut, metadata, instructions) == [
        {"seq": index, "role": role, "phase": phase, "request": stamp}
        for index, (role, phase, stamp) in enumerate(
            zip(ROLES, PHASES, stamps, strict=True), 1
        )
    ]
    registrar = cut["events"][1]["action"]
    expected_notes = sorted(
        ({**note, "keys": sorted(note["keys"])} for note in actions[1]["notes"]),
        key=lambda note: (note["epoch"], note["keys"]),
    )
    assert registrar["notes"] == expected_notes and registrar["ids"] == []
    audit = audit_notes(
        cut["case"], previous["slips"], registrar["ids"], registrar["notes"]
    )
    assert audit["accurate_positive_weight_count"] == 1
    assert audit["inaccurate_positive_weight_count"] == 1
    assert audit["missing_true_pair_count"] == 2
    assert audit["claimed_unfiled_true_pair_count"] == 2
    depot_view = payloads[2]["view"]
    assert depot_view["bundles"] == [{**note, "weight": 0} for note in expected_notes]
    assert depot_view["briefs"] == depot_view["registrar_file"] == []
    frames, receipt = packets(cut, instructions["depot"])
    selected = frames[MODE]
    assert {
        "bytes": len(selected),
        "sha256": hashlib.sha256(selected).hexdigest(),
    } == stamps[2]
    assert {key: receipt["packets"][MODE][key] for key in stamps[2]} == stamps[2]
    shown = json.loads(
        json.loads(frames["authored"])["prompt"].split("\nInput JSON: ", 1)[1]
    )
    assert shown["view"]["bundles"] == expected_notes
