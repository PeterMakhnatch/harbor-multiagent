import importlib.util
import json
import sys
from hashlib import sha256
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

TASK = Path(__file__).resolve().parents[3] / "examples/tasks/cramped-room-parity"
ACTIONS = (([0, -1], "interact"), ("interact", [0, 0]))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _view(tick):
    return {"tick": tick, "state": {"onions": tick, "note": "crème"}}


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _digest(view):
    return sha256(_canonical(view)).hexdigest()


def _native_row(tick, actions):
    return {
        "tick": tick,
        "joint_action": actions,
        "state": _view(tick)["state"],
        "reward": 0,
        "sparse_reward_by_agent": [0, 0],
        "shaped_reward_by_agent": [0, 0],
        "soup_delivery_by_agent": [False, False],
    }


class _FakeKitchen:
    def __init__(self):
        self.tick = 0
        self.state = _view(0)["state"]

    def observe(self):
        return {"tick": self.tick, "state": self.state}

    def step(self, actions):
        self.tick += 1
        # Mutate in place to catch any receipt serialized after the transition.
        self.state["onions"] = self.tick
        return _native_row(self.tick, actions)


@pytest.fixture
def world(monkeypatch, tmp_path):
    simulator = "overcooked_ai_py.mdp"
    modules = {
        "overcooked_ai_py": {},
        simulator: {},
        f"{simulator}.actions": {
            "Action": SimpleNamespace(
                INTERACT="interact", MOTION_ACTIONS=((0, -1), (0, 0))
            ),
        },
        f"{simulator}.overcooked_mdp": {"OvercookedGridworld": object},
    }
    with monkeypatch.context() as imports:
        for name, symbols in modules.items():
            stub = ModuleType(name)
            vars(stub).update(symbols)
            imports.setitem(sys.modules, name, stub)
        module = _load("seatlab_test_world", TASK / "environment/kitchen_world.py")
    monkeypatch.setattr(module, "Kitchen", _FakeKitchen)
    monkeypatch.setattr(module, "HORIZON", len(ACTIONS))
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "kitchen.jsonl")
    monkeypatch.setattr(module, "ACK_LOG_PATH", tmp_path / "seatlab-acks.jsonl")
    return module


def _server(world):
    with patch.object(world.HTTPServer, "__init__", return_value=None):
        return world.KitchenServer()


def _request(world, server, route, body=None):
    if body is None:
        request = f"GET {route} HTTP/1.0\r\n\r\n".encode()
    else:
        head = f"POST {route} HTTP/1.0\r\nContent-Length: {len(body)}\r\n\r\n"
        request = head.encode() + body
    reply = BytesIO()
    connection = Mock()
    connection.makefile.return_value = BytesIO(request)
    connection.sendall.side_effect = reply.write
    world.KitchenHandler(connection, ("127.0.0.1", 1), server)
    headers, data = reply.getvalue().split(b"\r\n\r\n", 1)
    return HTTPStatus(int(headers.split(b" ", 2)[1])), data


def _action_payload(tick, action, digest):
    return _canonical({"tick": tick, "action": action, "view_sha256": digest})


def _post(world, server, seat, tick, action, digest):
    status, data = _request(
        world,
        server,
        f"/action/chef{seat}",
        _action_payload(tick, action, digest),
    )
    return status, json.loads(data)


@pytest.mark.parametrize(
    "body",
    [
        b'{"tick":0,"action":"interact"}',
        b'{"tick":0,"action":"interact","view_sha256":null}',
        b'{"tick":0,"action":"interact","view_sha256":123}',
        _action_payload(0, "interact", "a" * 63),
        _action_payload(0, "interact", "a" * 64 + "\n"),
        _action_payload(0, "interact", "A" * 64),
        _action_payload(0, "interact", "g" * 64),
        _action_payload(True, "interact", _digest(_view(0))),
        _canonical(
            {
                "tick": 0,
                "action": "interact",
                "view_sha256": _digest(_view(0)),
                "extra": 0,
            }
        ),
        b'{"tick":1,' + _action_payload(0, "interact", _digest(_view(0)))[1:],
    ],
)
def test_rejects_malformed_commitments_without_consuming_seat(world, body):
    server = _server(world)
    status, _ = _request(world, server, "/action/chef1", body)
    assert status == HTTPStatus.BAD_REQUEST
    assert _post(world, server, 1, 0, "interact", _digest(_view(0))) == (
        HTTPStatus.OK,
        {"tick": 0},
    )
    assert not world.LOG_PATH.exists()
    assert not world.ACK_LOG_PATH.exists()


def test_joint_ticks_log_pre_action_views_in_either_arrival_order(
    world, monkeypatch, tmp_path
):
    native_log = b"".join(
        _canonical(_native_row(tick + 1, actions)) + b"\n"
        for tick, actions in enumerate(ACTIONS)
    )
    receipt_log = b"".join(
        _canonical(
            {
                "tick": tick,
                "view": _view(tick),
                "ack_sha256": {
                    "chef0": _digest(_view(tick)),
                    "chef1": _digest(_view(tick)),
                },
            }
        )
        + b"\n"
        for tick in range(len(ACTIONS))
    )
    for first in (0, 1):
        second = 1 - first
        directory = tmp_path / f"chef{first}-first"
        monkeypatch.setattr(world, "LOG_PATH", directory / "kitchen.jsonl")
        monkeypatch.setattr(world, "ACK_LOG_PATH", directory / "seatlab-acks.jsonl")
        server = _server(world)
        for tick, actions in enumerate(ACTIONS):
            status, data = _request(world, server, "/state")
            assert status == HTTPStatus.OK
            assert data == json.dumps(_view(tick)).encode()
            digest = _digest(json.loads(data))
            conflict = (HTTPStatus.CONFLICT, {"tick": tick})
            assert (
                _post(world, server, second, tick + 1, actions[second], digest)
                == conflict
            )
            assert _post(world, server, first, tick, actions[first], digest) == (
                HTTPStatus.OK,
                {"tick": tick},
            )
            assert _post(world, server, first, tick, [0, 0], digest) == conflict
            bad_digest = _digest(_view(tick - 1))
            assert _post(world, server, second, tick, [0, 0], bad_digest) == conflict
            assert _request(world, server, "/state") == (HTTPStatus.OK, data)
            assert not world.LOG_PATH.exists()
            assert not world.ACK_LOG_PATH.exists()

            assert _post(world, server, second, tick, actions[second], digest) == (
                HTTPStatus.OK,
                {"tick": tick + 1},
            )
            next_digest = _digest(_view(tick + 1))
            assert _post(world, server, second, tick, actions[second], next_digest) == (
                HTTPStatus.CONFLICT,
                {"tick": tick + 1},
            )
        assert world.LOG_PATH.read_bytes() == native_log
        assert world.ACK_LOG_PATH.read_bytes() == receipt_log
        assert not list(directory.glob("*.tmp"))


class _Response(BytesIO):
    status = HTTPStatus.OK


@pytest.mark.parametrize(
    ("mode", "seat", "actions"),
    [("tape", "chef1", [[1, 0], "interact"]), ("wait", "chef0", [[0, 0], [0, 0]])],
)
def test_policy_commits_to_full_matched_get_response(
    monkeypatch, tmp_path, mode, seat, actions
):
    policy = _load("seatlab_test_policy", TASK / "solution/policy.py")
    views = [_view(0), _view(1)]
    views[1]["state"]["note"] = "from the actual response"
    reads = iter([views[0], _view(0), views[1], _view(2)])
    posts = []

    def urlopen(request, *, timeout):
        assert timeout > 0
        if isinstance(request, str):
            assert request == f"{policy.WORLD}/state"
            data = json.dumps(next(reads), ensure_ascii=False, indent=2).encode()
            return _Response(data)
        assert request.full_url == f"{policy.WORLD}/action/{seat}"
        assert request.get_method() == "POST"
        posts.append(json.loads(request.data))
        return _Response()

    monkeypatch.setenv("SEAT", seat)
    monkeypatch.setenv("KITCHEN_POLICY", mode)
    monkeypatch.setattr(policy, "JOINT_ACTIONS", policy.JOINT_ACTIONS[:2])
    monkeypatch.setattr(policy, "FINAL_TICK", len(views))
    monkeypatch.setattr(policy, "urlopen", urlopen)
    monkeypatch.setattr(policy, "Path", lambda _: tmp_path / "trajectory.json")
    monkeypatch.setattr(
        policy, "time", SimpleNamespace(monotonic=lambda: 0, sleep=lambda _: None)
    )
    policy.main()

    assert posts == [
        {"tick": tick, "action": action, "view_sha256": _digest(view)}
        for tick, (action, view) in enumerate(zip(actions, views, strict=True))
    ]
    assert next(reads, None) is None


def test_policy_does_not_submit_a_nonfinite_view(monkeypatch):
    policy = _load("seatlab_test_policy", TASK / "solution/policy.py")
    urlopen = Mock(return_value=_Response(b'{"tick":0,"state":{"value":NaN}}'))
    monkeypatch.setenv("SEAT", "chef0")
    monkeypatch.setattr(policy, "urlopen", urlopen)

    with pytest.raises(ValueError):
        policy.main()

    urlopen.assert_called_once()
    assert urlopen.call_args.args == (f"{policy.WORLD}/state",)
