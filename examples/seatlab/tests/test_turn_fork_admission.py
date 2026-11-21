import json
import sys
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.turn_fork import journal, protocol, run
from examples.seatlab.visibility import route_cli, spend
from examples.seatlab.visibility.clarify_run import StudyError


def _source_packet() -> dict[str, Any]:
    return {
        "schema": "seatlab.turn-fork-packet/v1",
        "source_report_sha256": "1" * 64,
        "sources": [
            {
                "source_id": "s6501-r0",
                "seed": 6501,
                "repetition": 0,
                "trial_id": "12345678-1234-1234-1234-123456789abc",
                "world_sha256": "2" * 64,
                "upstream": {
                    "inventory": {
                        "prompt_sha256": "3" * 64,
                        "response_sha256": "4" * 64,
                    },
                    "brief": {
                        "prompt_sha256": "5" * 64,
                        "response_sha256": "6" * 64,
                    },
                },
                "card": "ABACDB",
                "cases": [
                    {
                        "id": index,
                        "utilities": {"A": 4, "B": 1, "C": 2, "D": 3},
                        "reports": [
                            {"revision": 1, "available": ["B", "D"]},
                            {"revision": 2, "available": ["A", "C"]},
                        ],
                        "after": ["A", "C"],
                    }
                    for index in range(6)
                ],
            }
        ],
    }


def _digest(packet: dict[str, Any]) -> str:
    body = json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n"
    return sha256(body.encode()).hexdigest()


def _original_ledger(root: Path, digest: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    commitment = root / "turn-fork.sha256"
    commitment.write_text(digest + "\n", encoding="ascii")
    monkeypatch.setattr(run, "_COMMITMENT", commitment)
    ledger = root / "ledger"
    ledger.mkdir(0o700)
    spend.initialize(ledger, Decimal("2.00"), Decimal("0.20"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    return ledger


@pytest.fixture
def no_direct(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbid(*_args: object) -> Any:
        calls.append("direct")
        raise AssertionError("direct admission and generation must both stay offline")

    monkeypatch.setattr(route_cli, "_admit", forbid)
    monkeypatch.setattr(route_cli, "_direct", forbid)
    return calls


@pytest.mark.parametrize("stage,role", [("push", "inventory"), ("decide", "dispatch")])
def test_ledger_only_reservation_skips_both_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_direct: list[str],
    stage: str,
    role: str,
) -> None:
    packet = _source_packet()
    digest = _digest(packet)
    root = tmp_path.resolve()
    ledger = _original_ledger(root, digest, monkeypatch)
    study = root / "study"
    run.seal(study, digest, "direct", None, ledger)
    spend.reserve(
        protocol.run_id(digest, "s6501-r0", "aligned", stage),
        role,
        balance_usd=Decimal("2.00"),
    )
    rows = run.run_cells(packet, digest, study, range(0, 4, 3))
    assert [row["state"] for row in rows] == ["skipped_not_ready", "no_source"]
    assert no_direct == []
    assert journal.collect(study, digest, packet["sources"][0], "aligned")["state"] == (
        "unclaimed"
    )


def test_direct_seal_refuses_other_ledger_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_direct: list[str]
) -> None:
    packet = _source_packet()
    digest = _digest(packet)
    root = tmp_path.resolve()
    ledger = _original_ledger(root, digest, monkeypatch)
    study = root / "study"
    run.seal(study, digest, "direct", None, ledger)
    other_ledger = root / "other"
    other_ledger.mkdir(0o700)
    spend.initialize(other_ledger, Decimal("2.00"), Decimal("0.20"))
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(other_ledger))
    with pytest.raises(StudyError, match="original_spend_directory_required"):
        run.run_cells(packet, digest, study, range(1))
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    changed = deepcopy(packet)
    changed["sources"][0]["card"] = "DDDDDD"
    with pytest.raises(StudyError, match="packet_content_changed"):
        run.run_cells(changed, digest, study, range(1))
    with pytest.raises(StudyError):
        run.run_cells(changed, _digest(changed), study, range(1))
    with pytest.raises(StudyError, match="direct_packet_not_registered"):
        run.seal(root / "other-study", _digest(changed), "direct", None, ledger)
    monkeypatch.delenv("SEATLAB_SPEND_DIR")
    assert run.load(study, digest)["transport"] == "direct"
    assert journal.collect(study, digest, packet["sources"][0], "aligned")["state"] == (
        "unclaimed"
    )
    assert no_direct == []


def test_commands_stop_on_fresh_failure_and_cannot_reseal_as_direct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_direct: list[str]
) -> None:
    packet = _source_packet()
    digest = _digest(packet)
    root = tmp_path.resolve()
    ledger = _original_ledger(root, digest, monkeypatch)
    study = root / "study"
    config = root / "external.json"
    failure = (
        "import os, sys; "
        "assert not {'DEEPSEEK_API_KEY', 'SEATLAB_SPEND_DIR'} & os.environ.keys(); "
        'sys.stdin.readline(); print(\'{"text":"unparseable"}\')'
    )
    command = {"argv": [str(Path(sys.executable).resolve()), "-c", failure], "env": []}
    config.write_text(json.dumps({"reviewer": command, "decider": command}))
    run.seal(study, digest, "commands", config, None)
    with pytest.raises(StudyError, match="study_already_exists"):
        run.seal(study, digest, "direct", None, ledger)
    config.unlink()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "never-forward-to-a-command")
    rows = run.run_cells(packet, digest, study, range(3))
    assert rows == [
        {"index": 0, "source_id": "s6501-r0", "arm": "aligned", "state": "incomplete"}
    ]
    aligned = journal.collect(study, digest, packet["sources"][0], "aligned")
    assert [
        (turn["stage"], turn["status"], turn["usage"]) for turn in aligned["turns"]
    ] == [("push", "received", None)]
    shifted = journal.collect(study, digest, packet["sources"][0], "shifted")
    assert shifted["state"] == "unclaimed"
    second = run.run_cells(packet, digest, study, range(1))
    assert second[0]["state"] == "skipped_not_ready"
    assert not spend.report(ledger)["calls"]
    assert no_direct == []
