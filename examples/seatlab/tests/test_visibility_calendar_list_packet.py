import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import calendar_list_packet as packet
from examples.seatlab.visibility import calendar_packet as ancestor
from examples.seatlab.visibility import calendar_protocol as protocol
from examples.seatlab.visibility import calendar_replica_series, replica_io


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def test_load_public_inputs() -> None:
    cases = packet.load()
    assert [case.id for case in cases] == [
        f"rx-w{world:02d}-d{direction}-r{repetition}-{arm}"
        for world in (0, 4, 8)
        for direction in (0, 1)
        for repetition in (0, 1)
        for arm in ("blind", "peer")
    ]
    assert len({case.trial_id_sha256 for case in cases}) == 24
    assert all(isinstance(case.receiver, tuple) for case in cases)
    assert all(isinstance(case.sender, tuple) for case in cases)


@pytest.mark.parametrize(
    "change",
    (
        "swapped_roles",
        "altered_list",
        "boolean_slot",
        "swapped_cases",
        "missing_case",
        "duplicate_case",
        "extra_case_field",
        "extra_packet_field",
        "different_trial",
        "different_prompt",
    ),
)
def test_load_rejects_changed_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    value = json.loads(packet.PACKET.read_bytes())
    cases = value["cases"]
    match change:
        case "swapped_roles":
            row = next(row for row in cases if row["receiver"] != row["sender"])
            row["receiver"], row["sender"] = row["sender"], row["receiver"]
        case "altered_list":
            cases[0]["receiver"] = [] if cases[0]["receiver"] else [0]
        case "boolean_slot":
            cases[0]["receiver"] = [True]
        case "swapped_cases":
            cases[0], cases[1] = cases[1], cases[0]
        case "missing_case":
            cases.pop()
        case "duplicate_case":
            cases[1] = cases[0]
        case "extra_case_field":
            cases[0]["response_text"] = "not a public field"
        case "extra_packet_field":
            value["source"] = "not a public field"
        case "different_trial":
            cases[0]["trial_id_sha256"] = cases[1]["trial_id_sha256"]
        case "different_prompt":
            cases[0]["prompt_sha256"] = "0" * 64
    mutated = tmp_path / "packet.json"
    mutated.write_bytes(_json(value))
    monkeypatch.setattr(packet, "PACKET", mutated)

    with pytest.raises(ValueError):
        packet.load()


@pytest.mark.parametrize(
    ("module", "name"), ((packet, "_OUTCOMES"), (ancestor, "PACKET"))
)
def test_load_requires_pinned_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any, name: str
) -> None:
    modified = tmp_path / "parent.json"
    modified.write_bytes(getattr(module, name).read_bytes() + b"\n")
    monkeypatch.setattr(module, name, modified)

    with pytest.raises(ValueError, match="SHA-256"):
        packet.load()


_RUN = "rx-w00-d1-r0"
_Row = dict[str, Any]


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, _Row]:
    # Exercise source extraction with local receipts. The stock native reader
    # has its own tests; this fixture controls what it independently attests.
    study = tmp_path.resolve()
    turns = study / _RUN / "block" / "turns"
    turns.mkdir(parents=True)
    block = protocol.Block(0, 1, 0)
    tables = ancestor.load(ancestor.PACKET, ancestor.PACKET_SHA256)[0].tables
    receiver = (1, 17)
    bound_turns: dict[str, dict[str, str]] = {}
    arms: dict[str, _Row] = {}

    def receipt(run: str, role: str, seat: str, prompt: str, text: str) -> None:
        hashes = {"prompt_sha256": _sha(prompt), "response_sha256": _sha(text)}
        bound_turns[f"{run}:{role}"] = hashes
        (turns / f"{run}_{role}.json").write_bytes(
            _json(
                {
                    "schema": replica_io.TURN_SCHEMA,
                    "run": run,
                    "role": role,
                    "seat": seat,
                    "response_text": text,
                    **hashes,
                }
            )
        )

    receipt(
        _RUN,
        "cal_initial_p1",
        "p1",
        protocol.initial_prompt(tables[0]),
        ' {"starts": [1, 17]} ',
    )
    for arm, sender in (("blind", (2, 4)), ("peer", (6, 8))):
        run = f"{_RUN}-{arm}"
        feedback = receiver if arm == "peer" else None
        revision = protocol.recheck_prompt(tables[1], (0,), feedback)
        decision = protocol.decision_prompt(tables[0], receiver, sender)
        receipt(run, "cal_recheck", "p2", revision, protocol.feedback_wire(sender))
        receipt(run, "cal_decide", "p1", decision, '{"slot":2}')
        arms[arm] = {
            "slot": 2,
            "trial_id_sha256": _sha(run),
            "receiver_prompt_sha256": _sha(decision),
            "native_timeline_verified": True,
        }
    report = {"turns": bound_turns, "arms": arms}
    observed = deepcopy(report)
    series = {"status": "complete", "rows": [{"block": _RUN, "report": observed}]}
    monkeypatch.setattr(packet, "_BLOCKS", (block,))
    monkeypatch.setattr(packet, "_historical", lambda: {_RUN: report})
    monkeypatch.setattr(calendar_replica_series, "analyze", lambda _: series)
    return study, observed


def test_build_projects_accepted_lists_only(source: tuple[Path, _Row]) -> None:
    study, observed = source
    raw = packet._build(study)
    value = json.loads(raw)
    assert raw == _json(value)
    assert value["cases"][0] == {
        "id": f"{_RUN}-blind",
        "world": 0,
        "direction": 1,
        "repetition": 0,
        "arm": "blind",
        "receiver": [1, 17],
        "sender": [2, 4],
        "prompt_sha256": observed["arms"]["blind"]["receiver_prompt_sha256"],
        "trial_id_sha256": _sha(f"{_RUN}-blind"),
    }
    assert value["cases"][1]["sender"] == [6, 8]


@pytest.mark.parametrize(
    ("key", "replacement"),
    (
        ("role", "cal_initial_p2"),
        ("seat", "p1"),
        ("private", "extra"),
        ("response_text", '{"starts":[2,5]}'),
    ),
)
def test_build_checks_receipt_after_native_validation(
    source: tuple[Path, _Row], key: str, replacement: str
) -> None:
    study, _ = source
    turn = study / _RUN / "block" / "turns" / f"{_RUN}-blind_cal_recheck.json"
    receipt = json.loads(turn.read_bytes())
    receipt[key] = replacement
    turn.write_bytes(_json(receipt))

    with pytest.raises(ValueError, match="turn receipt mismatch"):
        packet._build(study)


@pytest.mark.parametrize(
    ("key", "replacement"),
    (("slot", 3), ("trial_id_sha256", "0" * 64), ("native_timeline_verified", False)),
)
def test_build_binds_native_decision_and_identity(
    source: tuple[Path, _Row], key: str, replacement: object
) -> None:
    study, observed = source
    observed["arms"]["blind"][key] = replacement

    with pytest.raises(ValueError, match="differs from the pinned outcomes"):
        packet._build(study)


def test_build_rejects_repeated_native_trial(source: tuple[Path, _Row]) -> None:
    study, observed = source
    blind, peer = (observed["arms"][arm] for arm in ("blind", "peer"))
    peer["trial_id_sha256"] = blind["trial_id_sha256"]

    with pytest.raises(ValueError, match="duplicate native calendar trial"):
        packet._build(study)
