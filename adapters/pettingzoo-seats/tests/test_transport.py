"""File-handshake tests. Threads share a temporary mailbox and the real rps preset."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import actor  # noqa: E402
import world  # noqa: E402
from wire import read_json, write_json_atomic  # noqa: E402

SPEC = {
    "schema": "seatlab.pz-world/v1",
    "api": "parallel",
    "factory": "pettingzoo.classic.rps_v2:parallel_env",
    "kwargs": {"max_cycles": 6},
    "roles": {"p0": "player_0", "p1": "player_1"},
    "seed": 19,
    "max_world_steps": 128,
}


def _thread(target, *args) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            target(*args)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    return thread, errors


def _join(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(40)
        assert not thread.is_alive()


def _no_report(exchange: Path, result: Path) -> None:
    assert not result.exists()
    for role in SPEC["roles"]:
        assert not (exchange / role / "in" / "done.json").exists()


def test_rps_handshake_keeps_each_seat_private(tmp_path: Path) -> None:
    exchange, result = tmp_path / "exchange", tmp_path / "pettingzoo.json"
    audits = {role: tmp_path / role / "pz_actions.jsonl" for role in SPEC["roles"]}
    policies = {"p0": "low", "p1": "rps-beat-last"}
    world_thread, world_errors = _thread(world.run, SPEC, exchange, result, 30)
    seats = [
        _thread(
            actor.run,
            policies[role],
            exchange / role / "in",
            exchange / role / "out",
            audits[role],
            30,
        )
        for role in SPEC["roles"]
    ]
    _join([world_thread, *(thread for thread, _errors in seats)])
    assert world_errors == []
    assert [errors for _thread, errors in seats] == [[], []]
    report = read_json(result)
    assert report["schema"] == "seatlab.pz-native/v1"
    assert set(report["returns"]) == set(SPEC["roles"])
    assert [row["step"] for row in report["steps"]] == list(
        range(1, len(report["steps"]) + 1)
    )
    for role, audit in audits.items():
        assert read_json(exchange / role / "in" / "done.json") == {"done": True}
        parsed = [
            json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()
        ]
        expected = [
            {
                "step": row["step"],
                "view_sha256": row["decisions"][role]["view_sha256"],
                "action": row["decisions"][role]["action"],
            }
            for row in report["steps"]
            if role in row["decisions"]
        ]
        assert parsed == expected
        request = read_json(exchange / role / "in" / "request.json")
        assert set(request) == {"step", "view", "view_sha256"}
        assert set(request["view"]) == {"observation", "legal_actions"}


def _when_requested(exchange: Path, write) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        path = exchange / "p0" / "in" / "request.json"
        if path.exists():
            write(read_json(path))
            return
        time.sleep(0.05)
    raise AssertionError("request did not appear")


def test_bad_reply_leaves_no_report(tmp_path: Path) -> None:
    exchange, result = tmp_path / "exchange", tmp_path / "pettingzoo.json"
    server, errors = _thread(world.run, SPEC, exchange, result, 10)

    def _bad(req: dict) -> None:
        write_json_atomic(
            exchange / "p0" / "out" / "action.json",
            {
                "step": req["step"],
                "view_sha256": "0" * 64,
                "action": req["view"]["legal_actions"][0],
            },
        )

    client, client_errors = _thread(_when_requested, exchange, _bad)
    _join([server, client])
    assert client_errors == []
    assert errors and isinstance(errors[0], ValueError)
    _no_report(exchange, result)


def test_future_step_leaves_no_report(tmp_path: Path) -> None:
    exchange, result = tmp_path / "exchange", tmp_path / "pettingzoo.json"
    server, errors = _thread(world.run, SPEC, exchange, result, 10)

    def _future(req: dict) -> None:
        write_json_atomic(
            exchange / "p0" / "out" / "action.json",
            {"step": req["step"] + 1, "view_sha256": req["view_sha256"], "action": 0},
        )

    client, client_errors = _thread(_when_requested, exchange, _future)
    _join([server, client])
    assert client_errors == []
    assert errors and isinstance(errors[0], ValueError)
    _no_report(exchange, result)


def test_timeout_writes_nothing(tmp_path: Path) -> None:
    exchange, result = tmp_path / "exchange", tmp_path / "pettingzoo.json"
    with pytest.raises(TimeoutError):
        world.run(SPEC, exchange, result, 0.2)
    _no_report(exchange, result)


def test_actor_refuses_bad_digest_and_policies(tmp_path: Path) -> None:
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    missing = {"observation": 3, "legal_actions": [0, 1, 2]}
    write_json_atomic(
        inbox / "request.json", {"step": 1, "view": missing, "view_sha256": "ab" * 32}
    )
    with pytest.raises(ValueError, match="digest"):
        actor.run("low", inbox, outbox, None, 2)
    assert not (outbox / "action.json").exists()
    ordered = {"observation": 1, "legal_actions": [2, 0, 1]}
    assert actor.choose("low", ordered) == 2
    assert actor.choose("high", ordered) == 1
    assert actor.choose("rps-copy", ordered) == 1
    assert actor.choose("rps-beat-last", ordered) == 2
    assert actor.choose("rps-copy", missing) == 0
    assert actor.choose("rps-beat-last", missing) == 0
