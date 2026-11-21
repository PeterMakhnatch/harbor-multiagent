import json
from decimal import Decimal
from pathlib import Path

import pytest

from examples.seatlab.visibility import (
    route_cli,
    route_memory,
    route_packet,
    route_protocol,
    route_run,
    seat_command,
    spend,
)

_REVIEW = '{"vote":"B","a":[0,0],"b":[0,0]}'


def _study(tmp_path: Path, workflow: str):
    study = (tmp_path / "study").resolve()
    study.mkdir(mode=0o700)
    digest = route_cli._COMMITMENT.read_text(encoding="ascii").strip()
    cases = route_packet.load(route_cli._PUBLIC, digest)
    if workflow == "delegate":
        blocks = route_memory.blocks()
        eligible = [route_memory.menu(cases, block) is not None for block in blocks]
    else:
        blocks = route_protocol.blocks()
        rounds = {(c.policy, c.round) for c in cases if c.reports is not None}
        eligible = [(b.policy, b.round) in rounds for b in blocks]

    def key(block: route_protocol.Block) -> str:
        return route_memory.key(block) if workflow == "delegate" else block.id

    index = next(i for i in range(len(eligible) - 1) if eligible[i] and eligible[i + 1])
    args = ["--workflow", workflow, "--study", str(study)]
    return (
        study,
        digest,
        index,
        blocks[index],
        key(blocks[index]),
        key(blocks[index + 1]),
        args,
    )


def _unexpected(*_args, **_kwargs):
    pytest.fail("transport must not be admitted or called")


def _ledger(tmp_path: Path, monkeypatch) -> Path:
    ledger = (tmp_path / "spend").resolve()
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal("4"), Decimal("0.50"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    return ledger


def _reserve_block(workflow: str, block: route_protocol.Block) -> None:
    if workflow == "delegate":
        run, role = route_memory.run_id(block, "bound"), "route_decide"
    else:
        lens = route_protocol.lenses(block)[0]
        run, role = route_protocol.run_id(block, lens, "review"), "route_review"
    spend.reserve(run, role, balance_usd=Decimal("4"))


@pytest.mark.parametrize("workflow", ["review", "delegate"])
def test_closed_command_window_does_not_need_a_config(
    tmp_path, capsys, monkeypatch, workflow
):
    study, _, index, _, first, second, args = _study(tmp_path, workflow)
    start = ["--start", str(index)]
    assert route_cli.main(["demo", *args, *start]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "index": index,
        "block": first,
        "mode": "demo",
        "status": "complete",
    }
    claim = study / first / "claim.json"
    original = claim.read_bytes()

    assert route_cli.main(["pending", *args]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert index not in plan["ready"]
    assert index + 1 in plan["ready"]

    monkeypatch.setattr(seat_command, "load_commands", _unexpected)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", "must-not-use-ambient-ledger")
    assert route_cli.main(["commands", *args, *start, "--only-unclaimed"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "index": index,
        "block": first,
        "mode": "commands",
        "status": "skipped_not_ready",
    }
    assert route_cli.main(["demo", *args, *start]) == 1
    assert not capsys.readouterr().out
    assert claim.read_bytes() == original
    assert not (study / second).exists()


@pytest.mark.parametrize("workflow", ["review", "delegate"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_continuation_does_not_reopen_an_interrupted_block(
    tmp_path, capsys, monkeypatch, workflow, failure
):
    study, _, index, _, first, second, args = _study(tmp_path, workflow)
    bounds = ["--start", str(index), "--stop", str(index + 2)]
    calls = []

    def fail(prompt: str) -> str:
        calls.append(prompt)
        raise failure("private transport detail")

    monkeypatch.setattr(seat_command, "load_commands", lambda _: (fail, fail))
    initial = [
        "commands",
        *args,
        *bounds,
        "--only-unclaimed",
        "--config",
        str(tmp_path / "config"),
    ]
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            route_cli.main(initial)
    else:
        assert route_cli.main(initial) == 1
    captured = capsys.readouterr()
    assert "private transport detail" not in captured.err
    assert not captured.out
    assert len(calls) == 1
    assert not (study / second).exists()
    cell = study / first
    before = {path.name: path.read_bytes() for path in cell.iterdir()}
    assert "claim.json" in before

    assert route_cli.main(["demo", *args, *bounds, "--only-unclaimed"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(row["block"], row["status"]) for row in rows] == [
        (first, "skipped_not_ready"),
        (second, "complete"),
    ]
    assert (study / second / "claim.json").is_file()
    assert {path.name: path.read_bytes() for path in cell.iterdir()} == before


@pytest.mark.parametrize("workflow", ["review", "delegate"])
def test_direct_continuation_requires_original_ledger_even_for_closed_work(
    tmp_path, capsys, monkeypatch, workflow
):
    study, _, index, block, _, _, args = _study(tmp_path, workflow)
    ledger = _ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    _reserve_block(workflow, block)
    db = ledger / "direct-flash.sqlite3"
    before = db.read_bytes()
    monkeypatch.setattr(route_cli, "_admit", _unexpected)
    monkeypatch.setattr(route_cli, "_direct", _unexpected)
    direct = ["direct", *args, "--start", str(index), "--only-unclaimed"]

    monkeypatch.delenv("SEATLAB_SPEND_DIR")
    assert route_cli.main(direct) == 1
    assert not capsys.readouterr().out
    monkeypatch.setenv("SEATLAB_SPEND_DIR", ledger.name)
    assert route_cli.main(direct) == 1
    assert not capsys.readouterr().out
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(tmp_path / "absent"))
    assert route_cli.main(direct) == 1
    assert not capsys.readouterr().out

    assert route_cli.main(["pending", *args, "--spend-dir", str(ledger)]) == 0
    assert index not in json.loads(capsys.readouterr().out)["ready"]
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    assert route_cli.main(direct) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped_not_ready"
    assert db.read_bytes() == before
    assert not list(study.iterdir())


@pytest.mark.parametrize("workflow", ["review", "delegate"])
@pytest.mark.parametrize("race", ["same_packet", "foreign_packet", "second_held"])
def test_raced_claim_is_checked_again_after_direct_admission(
    tmp_path, capsys, monkeypatch, workflow, race
):
    study, digest, index, _, first, second, args = _study(tmp_path, workflow)
    ledger = _ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    calls = []

    def admit() -> None:
        if race == "second_held":
            blocks = (
                route_memory.blocks()
                if workflow == "delegate"
                else route_protocol.blocks()
            )
            _reserve_block(workflow, blocks[index + 1])
        monkeypatch.delenv("SEATLAB_SPEND_DIR")
        cell = study / first
        cell.mkdir(mode=0o700)
        claim = cell / "claim.json"
        claim.write_text(
            json.dumps(
                {
                    "schema": "seatlab.memory-claim/v1"
                    if workflow == "delegate"
                    else "seatlab.routed-claim/v1",
                    "packet_sha256": "0" * 64 if race == "foreign_packet" else digest,
                    "block": first,
                }
            ),
            encoding="utf-8",
        )
        claim.chmod(0o600)

    def respond(prompt: str, run: str, role: str) -> route_run.Answer:
        calls.append(run)
        if role == "route_review":
            return route_run.Answer(_REVIEW)
        if workflow == "delegate" and '"ask"' in prompt:
            return route_run.Answer('{"ask":"X"}')
        return route_run.Answer('{"choice":"A"}')

    monkeypatch.setattr(route_cli, "_admit", admit)
    monkeypatch.setattr(route_cli, "_direct", respond)
    direct = [
        "direct",
        *args,
        "--start",
        str(index),
        "--stop",
        str(index + 2),
        "--only-unclaimed",
    ]
    if race == "foreign_packet":
        assert route_cli.main(direct) == 1
        assert not capsys.readouterr().out
        assert not calls
        assert not (study / second).exists()
    else:
        assert route_cli.main(direct) == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        second_status = "skipped_not_ready" if race == "second_held" else "complete"
        assert [(row["block"], row["status"]) for row in rows] == [
            (first, "skipped_already_claimed"),
            (second, second_status),
        ]
        if race == "second_held":
            assert not calls
            assert not (study / second).exists()
        else:
            assert calls and all(run.startswith(second + "-") for run in calls)
    assert list((study / first).iterdir()) == [study / first / "claim.json"]
