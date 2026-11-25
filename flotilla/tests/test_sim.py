"""Seeded schedule sweeps run the actual referee handlers and portable policies."""

import json

from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
SEED = 7836225211339347


def test_pooling_sweep_and_replay():
    for instance in range(8):
        for schedule in range(8):
            for arm in ("solo", "talk"):
                result = SIM.simulate("ledger", arm, instance, "careful", SEED, schedule)
                assert result["reward"] == 1
                assert result["state"]["frozen"]
                assert [e["seq"] for e in result["events"]] == list(range(1, len(result["events"]) + 1))
                assert result == SIM.simulate("ledger", arm, instance, "careful", SEED, schedule)
            result = SIM.simulate("ledger", "talk", instance, "nop", SEED, schedule)
            assert result["reward"] == 0
            assert not result["state"]["submissions"]
    orders = [SIM.simulate("ledger", "talk", 0, "careful", SEED, schedule)["events"] for schedule in range(8)]
    assert len({json.dumps(events, sort_keys=True) for events in orders}) > 1


def test_simulation_refuses_moves_after_end(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_LOGS", str(tmp_path))
    monkeypatch.setenv("FLOTILLA_CONDITION", "talk")
    monkeypatch.setenv("FLOTILLA_SEATS", "p1,p2")
    monkeypatch.setenv("FLOTILLA_TOKENS", "p1:token1,p2:token2")
    monkeypatch.setenv("FLOTILLA_ADMIN", "admin-token")
    monkeypatch.setenv("FLOTILLA_TASK_JSON", str(tmp_path / "missing.json"))
    world = load(FLOTILLA / "world" / "world.py")
    sim = SIM.Simulation(world, SIM.Clock(), SEED, 1)
    sim.finish()
    for path, body in (("/submit", {"answer": {"total": 1}}), ("/messages", {"to": "p2", "text": "late"})):
        assert sim.request("p1", SIM.POLICY.Request("POST", path, body))[0] == 409
    assert not world.STATE["messages"] and not world.STATE["submissions"]
    assert [e["kind"] for e in sim.events][-2:] == ["late", "late"]
