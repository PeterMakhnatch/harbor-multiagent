import asyncio
import json
import shlex
import socket
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from typing import Any, override

import pytest

from examples.seatlab.exhibit_wire import encode_request, render_turn
from examples.seatlab.physical_dispatch import certificate, policies, stage, worlds
from examples.seatlab.physical_dispatch.receiver import DispatchReceiver
from examples.seatlab.physical_dispatch.receiver_plan import prepare_receivers
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

MAPS = ("original", "swapped")
FILINGS = ("other", "relevant")
ARMS = tuple(f"{world}-{filing}" for world in MAPS for filing in FILINGS)
ROLES = ("loader", "registrar", "depot")
SEED = 10
INSTRUCTION = (stage.SOURCE_DIR / "instructions/loader.md").read_text(encoding="utf-8")


class SessionSeat(SeatEnvironment):
    def __init__(self, session: Any, role: str) -> None:
        self.session = session
        self.service = role
        self.actions: list[dict[str, Any]] = []

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
        python, script, op, *args = shlex.split(command)
        assert (python, script) == ("python3", "/app/relay.py")
        if op in ("view", "wait"):
            for _ in range(1000):
                view = self.session.view(self.service)
                if op == "view" or view["phase"] == args[0]:
                    break
                await asyncio.sleep(0.005)
            else:
                raise AssertionError(f"Phase not reached: {args}")
        else:
            if op == "request":
                action = {"op": op, "slips": args}
            elif op == "file":
                action = {"op": op, "ids": args}
            else:
                assert op == "commit"
                action = {"op": op, "routes": dict(pair.split("=", 1) for pair in args)}
            view = self.session.apply(self.service, action)
            self.actions.append(action)
        return ExecResult(
            return_code=0,
            stdout=json.dumps(view, sort_keys=True, separators=(",", ":")),
        )


def listener(stack: ExitStack, path: Path) -> socket.socket:
    sock = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
    sock.bind(str(path))
    path.chmod(0o600)
    sock.listen(2)
    sock.settimeout(8)
    return sock


def no_connection(sock: socket.socket) -> None:
    sock.setblocking(False)
    with pytest.raises(BlockingIOError):
        sock.accept()


def receive_once(
    sock: socket.socket, path: Path, expected: bytes, action: dict[str, Any]
) -> bytes:
    with sock.accept()[0] as conn:
        path.unlink()
        sock.close()
        conn.settimeout(5)
        with conn.makefile("rb") as stream:
            line = stream.readline(65537)
        assert line == expected
        conn.sendall((json.dumps({"text": json.dumps(action)}) + "\n").encode())
    return line


def probe_arm(root: Path, map_name: str, filing: str) -> bytes:
    base = certificate.make_case(SEED, "fork", layout="draw")
    assert base["realized"] == min(brief["id"] for brief in base["briefs"])
    case = worlds.select(base, map_name)
    prefix, view = certificate._prefix(deepcopy(case), filing)
    expected = encode_request(render_turn(INSTRUCTION, view, prefix[0]))
    action = policies.follow(view)
    if filing == "other":
        assert action is None
        action = {
            "op": "commit",
            "routes": {item: min(view["bays"]) for item in view["order"]["items"]},
        }
    assert action is not None
    session = certificate.Session(case, "linked")
    path = root / f"{map_name}-{filing}"
    with ExitStack() as stack:
        sock = listener(stack, path)
        agents = [
            DispatchReceiver(root, role="loader", owner_socket=str(path)),
            DispatchReceiver(root, role="registrar"),
            DispatchReceiver(root, role="depot", filing=filing),
        ]
        seats = [SessionSeat(session, role) for role in ROLES]
        contexts = [AgentContext() for _ in ROLES]
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
        future = pool.submit(receive_once, sock, path, expected, action)

        async def run() -> None:
            for agent, seat in zip(agents, seats, strict=True):
                await agent.setup(seat)
            await asyncio.wait_for(
                asyncio.gather(
                    *(
                        agent.run(
                            INSTRUCTION
                            if role == "loader"
                            else f"Instruction for {role}",
                            seat,
                            context,
                        )
                        for role, agent, seat, context in zip(
                            ROLES, agents, seats, contexts, strict=True
                        )
                    )
                ),
                timeout=10,
            )

        asyncio.run(run())
        request = future.result(timeout=2)
    assert [seat.actions for seat in seats] == [
        [prefix[0], action],
        [prefix[1]],
        [prefix[2]],
    ]
    assert [event["action"] for event in session.snapshot()["events"]] == [
        *prefix,
        action,
    ]
    result = certificate.grade(session.snapshot())
    assert result["status"] == "complete"
    assert result["reward"] == (1 if filing == "relevant" else 0)
    return request


def test_receiver_only_contacts_owner_at_final_dispatch() -> None:
    with tempfile.TemporaryDirectory(prefix="dispatch-", dir="/tmp") as directory:
        requests = {
            (map_name, filing): probe_arm(Path(directory).resolve(), map_name, filing)
            for map_name in MAPS
            for filing in FILINGS
        }
    assert requests["original", "other"] == requests["swapped", "other"]
    assert requests["original", "relevant"] != requests["swapped", "relevant"]
    assert len({len(packet) for packet in requests.values()}) == 1


def test_receiver_plan_prepares_four_native_inputs_without_connecting(
    tmp_path: Path,
) -> None:
    task = tmp_path.resolve() / "task"
    stage.materialize(task)
    before = stage.check_stage(task)
    out = tmp_path.resolve() / "inputs"
    with (
        tempfile.TemporaryDirectory(prefix="dispatch-", dir="/tmp") as directory,
        ExitStack() as stack,
    ):
        root = Path(directory).resolve()
        endpoints = {arm: str(root / arm) for arm in ARMS}
        sockets = [listener(stack, Path(path)) for path in endpoints.values()]
        assert len({Path(path).stat().st_ino for path in endpoints.values()}) == 4
        receipt = prepare_receivers(task, out, SEED, endpoints)
        assert receipt["status"] == "prepared_not_enrolled"
        assert receipt["executions"] == 0
        assert not (out / "trials").exists()
        for arm in ARMS:
            map_name, filing = arm.split("-")
            for suffix in ("config", "lock"):
                path = out / f"{arm}.{suffix}.json"
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
                record = json.loads(path.read_text(encoding="utf-8"))
                assert record["environment"]["env"] == {
                    "EXHIBIT_SEED": str(SEED),
                    "EXHIBIT_PROFILE": "fork",
                    "EXHIBIT_MODE": "linked",
                    "EXHIBIT_LAYOUT": "draw",
                    "EXHIBIT_BAY_MAP": map_name,
                }
                members = {member["seat"]: member for member in record["participants"]}
                assert set(members) == set(ROLES)
                assert all(
                    member.get("model_name") is None for member in members.values()
                )
                assert members["loader"]["kwargs"] == {
                    "role": "loader",
                    "owner_socket": endpoints[arm],
                }
                assert members["registrar"]["kwargs"] == {"role": "registrar"}
                assert members["depot"]["kwargs"] == {"role": "depot", "filing": filing}
        duplicate = {**endpoints, ARMS[1]: endpoints[ARMS[0]]}
        rejected = tmp_path.resolve() / "duplicate"
        with pytest.raises(ValueError):
            prepare_receivers(task, rejected, SEED, duplicate)
        assert not rejected.exists()
        nonfocal = certificate.make_case(7, "fork", layout="draw")
        assert nonfocal["realized"] != min(brief["id"] for brief in nonfocal["briefs"])
        rejected = tmp_path.resolve() / "nonfocal"
        with pytest.raises(ValueError):
            prepare_receivers(task, rejected, 7, endpoints)
        assert not rejected.exists()
        for sock in sockets:
            no_connection(sock)
    assert stage.check_stage(task) == before
