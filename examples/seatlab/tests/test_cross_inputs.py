import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import inputs
from examples.seatlab.turn_fork import packet as packet_format
from examples.seatlab.turn_fork import protocol
from examples.seatlab.turn_fork.__main__ import _envelope, _grid
from examples.seatlab.visibility.clarify_run import _encode
from examples.seatlab.visibility.deepseek import REVISION


type _Document = dict[str, Any]
type _Slot = tuple[str, str, str]
type _Opened = tuple[str, str]

_FIRST = "s6501-r0"
_SECOND = "s6501-r1"
_SHARED = ("Q2", "Q4")
_PLAYED: dict[_Slot, tuple[_Opened, str | None]] = {
    (_FIRST, "direct", "aligned"): (_SHARED[::-1], "B"),
    (_FIRST, "direct", "shifted"): (("Q5", "Q6"), "D"),
    (_FIRST, "direct", "repeat"): (_SHARED, "D"),
    (_FIRST, "commands", "aligned"): (_SHARED[::-1], "D"),
    (_FIRST, "commands", "repeat"): (("Q1", "Q3"), "B"),
    (_SECOND, "direct", "aligned"): (("Q1", "Q4"), "D"),
    (_SECOND, "direct", "repeat"): (("Q3", "Q6"), None),
    (_SECOND, "commands", "repeat"): (_SHARED, "B"),
}
_USAGE = {
    "served_model": REVISION,
    "input_tokens": 12,
    "cache_tokens": 3,
    "output_tokens": 4,
}


@dataclass(frozen=True)
class _Checkpoint:
    packet: _Document
    path: Path
    digest: str
    direct: Path
    commands: Path


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _source(rep: int) -> _Document:
    return {
        "source_id": f"s6501-r{rep}",
        "seed": 6501,
        "repetition": rep,
        "trial_id": str(UUID(int=1 + rep)),
        "world_sha256": _hash("world-6501"),
        "upstream": {
            role: {
                "prompt_sha256": _hash(f"{rep}/{role}/prompt"),
                "response_sha256": _hash(f"{rep}/{role}/response"),
            }
            for role in ("inventory", "brief")
        },
        "card": ("AAABCD", "DCBAAA")[rep],
        "cases": [
            {
                "id": case_id,
                "utilities": dict(zip("ABCD", range(1, 5), strict=True)),
                "reports": [
                    {"revision": 1, "available": ["A", "B"]},
                    {"revision": 2, "available": ["B", "C"]},
                ],
                "after": ["A" if case_id % 2 == 0 else "C", "D"],
            }
            for case_id in range(6)
        ],
    }


def _turn(
    digest: str,
    row: _Document,
    transport: str,
    stage: str,
    text: str,
    reply: str | None,
) -> _Document:
    return {
        "stage": stage,
        "role": "inventory" if stage == "push" else "dispatch",
        "run": protocol.run_id(digest, row["source_id"], row["arm"], stage),
        "prompt_sha256": _hash(text),
        "response_sha256": _hash(reply) if reply is not None else None,
        "usage": dict(_USAGE) if reply is not None and transport == "direct" else None,
        "status": "callback_error" if reply is None else "received",
    }


def _rows(packet: _Document, digest: str, transport: str) -> list[_Document]:
    by_source = {source["source_id"]: source for source in packet["sources"]}
    rows = []
    for cell in _grid():
        source_id, arm = cell["source_id"], cell["arm"]
        source = by_source.get(source_id)
        row = dict(
            cell,
            state="no_source" if source is None else "unclaimed",
            turns=[],
            outcome=None,
        )
        rows.append(row)
        played = _PLAYED.get((source_id, transport, arm))
        if source is None or played is None:
            continue
        opened, bay = played
        push = json.dumps({"open": opened}, indent=2)
        push += "\n\t" if transport == "direct" else "\n "
        opened = protocol.parse_push(push)
        decide = None
        if bay is not None:
            names = aliases(source["cases"], source["seed"], source["repetition"])
            decide = json.dumps({"choices": dict.fromkeys(names, bay)})
        row.update(
            state="incomplete" if decide is None else "complete",
            turns=[
                _turn(
                    digest,
                    cell,
                    transport,
                    "push",
                    protocol.push_prompt(source, arm),
                    push,
                ),
                _turn(
                    digest,
                    cell,
                    transport,
                    "decide",
                    protocol.decide_prompt(source, opened),
                    decide,
                ),
            ],
            outcome=None if decide is None else protocol.grade(source, opened, decide),
        )
    return rows


@pytest.fixture
def checkpoint(tmp_path: Path) -> _Checkpoint:
    packet = {
        "schema": packet_format.SCHEMA,
        "source_report_sha256": _hash("native source fixture"),
        "sources": [_source(1), _source(0)],
    }
    path = tmp_path / "packet.json"
    raw = _encode(packet)
    path.write_bytes(raw)
    digest = sha256(raw).hexdigest()
    direct = tmp_path / "direct.json"
    commands = tmp_path / "commands.json"
    direct_report = _envelope(packet, digest, "direct", _rows(packet, digest, "direct"))
    command_report = _envelope(
        packet, digest, "commands", _rows(packet, digest, "commands")
    )
    direct.write_bytes((json.dumps(direct_report, indent=2) + "\n\n").encode("utf-8"))
    commands.write_bytes((" " + json.dumps(command_report)).encode("utf-8"))
    return _Checkpoint(packet, path, digest, direct, commands)


def _build(checkpoint: _Checkpoint) -> tuple[_Document, _Document, str]:
    return inputs.build(
        checkpoint.path, checkpoint.digest, checkpoint.direct, checkpoint.commands
    )


def _observation(source: _Document, opened: _Opened) -> _Document:
    source_id = source["source_id"]
    digest = _hash(protocol.decide_prompt(source, opened))
    return {
        "source_id": source_id,
        "obs_id": f"{source_id}-{digest[:12]}",
        "prompt_sha256": digest,
        "opened": list(opened),
    }


def test_board_joins_original_push_receipts_and_shares_only_within_source(checkpoint):
    packet, board, digest = _build(checkpoint)
    assert packet == checkpoint.packet
    assert board.keys() == {
        "schema",
        "packet_sha256",
        "direct_report_sha256",
        "commands_report_sha256",
        "observations",
        "selections",
    }
    assert board["schema"] == "seatlab.decision-cross-board/v1"
    assert board["packet_sha256"] == checkpoint.digest
    assert digest == sha256(_encode(board)).hexdigest()
    reports = {}
    for transport, path in (
        ("direct", checkpoint.direct),
        ("commands", checkpoint.commands),
    ):
        raw = path.read_bytes()
        reports[transport] = json.loads(raw)
        assert raw != _encode(reports[transport])
        assert board[f"{transport}_report_sha256"] == sha256(raw).hexdigest()
    second, first = packet["sources"]
    forward = protocol.decide_prompt(first, _SHARED)
    assert forward == protocol.decide_prompt(first, _SHARED[::-1])
    assert forward == protocol.decide_prompt(second, _SHARED)
    first_shared = _observation(first, _SHARED)
    first_commands_repeat = _observation(first, ("Q1", "Q3"))
    second_direct = _observation(second, ("Q1", "Q4"))
    second_shared = _observation(second, _SHARED)
    assert board["observations"] == sorted(
        [first_shared, first_commands_repeat, second_direct, second_shared],
        key=lambda obs: (obs["source_id"], obs["prompt_sha256"]),
    )

    completed = {
        (_FIRST, "direct", "aligned"): first_shared,
        (_FIRST, "direct", "repeat"): first_shared,
        (_FIRST, "commands", "aligned"): first_shared,
        (_FIRST, "commands", "repeat"): first_commands_repeat,
        (_SECOND, "direct", "aligned"): second_direct,
        (_SECOND, "commands", "repeat"): second_shared,
    }
    original_push_hashes = {
        (row["source_id"], transport, row["arm"]): row["turns"][0]["response_sha256"]
        for transport, report in reports.items()
        for row in report["rows"]
        if row["turns"]
    }
    assert len({original_push_hashes[slot] for slot in completed}) == len(completed)
    assert board["selections"] == [
        {
            "source_id": source_id,
            "sender": transport,
            "arm": arm,
            "obs_id": completed[slot]["obs_id"] if slot in completed else None,
            "sender_response_sha256": original_push_hashes[slot]
            if slot in completed
            else None,
        }
        for seed in range(6501, 6507)
        for rep in (0, 1)
        for source_id in (f"s{seed}-r{rep}",)
        for transport in ("direct", "commands")
        for arm in ("aligned", "repeat")
        for slot in ((source_id, transport, arm),)
    ]


def test_receiver_prompt_releases_only_selected_revisions_and_checks_commitment(
    checkpoint,
):
    packet, board, _ = _build(checkpoint)
    sources = {source["source_id"]: source for source in packet["sources"]}
    for obs in board["observations"]:
        source = sources[obs["source_id"]]
        opened = tuple(obs["opened"])
        text = inputs.prompt(packet, obs)
        assert text == protocol.decide_prompt(source, opened)
        assert _hash(text) == obs["prompt_sha256"]
        payload = json.loads(text.split("\n", 1)[1])
        names = aliases(source["cases"], source["seed"], source["repetition"])
        assert payload == {
            "cases": [
                {
                    "alias": name,
                    "utilities": case["utilities"],
                    "revision_1": case["reports"][0]["available"],
                    **(
                        {"revision_2": case["reports"][1]["available"]}
                        if name in opened
                        else {}
                    ),
                }
                for name, native_id in names.items()
                for case in (source["cases"][native_id],)
            ]
        }
        with pytest.raises(ValueError):
            inputs.prompt(packet, dict(obs, opened=["Q5", "Q6"]))


def test_receiver_draws_follow_committed_order_and_have_distinct_run_ids(checkpoint):
    _, board, digest = _build(checkpoint)
    expected = [
        {
            "source_id": obs["source_id"],
            "obs_id": obs["obs_id"],
            "prompt_sha256": obs["prompt_sha256"],
            "draw": draw,
        }
        for draw in (0, 1)
        for obs in sorted(
            board["observations"],
            key=lambda obs: _hash(f"cross-order-v1/{draw}/{obs['obs_id']}"),
        )
    ]
    rows = inputs.draws(board)
    assert len(rows) == 8
    assert rows == [
        dict(expected_row, index=index) for index, expected_row in enumerate(expected)
    ]
    run_ids = [inputs.run_id(digest, row["obs_id"], row["draw"]) for row in rows]
    assert run_ids == [
        f"dx-{digest[:12]}-{row['obs_id']}-r{row['draw']}" for row in rows
    ]
    assert len(set(run_ids)) == len(rows)


def test_board_rejects_a_tampered_native_decide_prompt_receipt(checkpoint):
    report = json.loads(checkpoint.direct.read_bytes())
    row = next(row for row in report["rows"] if row["state"] == "complete")
    row["turns"][1]["prompt_sha256"] = row["turns"][0]["prompt_sha256"]
    checkpoint.direct.write_bytes(_encode(report))
    with pytest.raises(ValueError):
        _build(checkpoint)


@pytest.mark.parametrize("swapped_slot", ("direct", "commands"))
def test_board_rejects_a_verified_report_in_the_wrong_transport_slot(
    checkpoint, swapped_slot
):
    direct = checkpoint.commands if swapped_slot == "direct" else checkpoint.direct
    commands = checkpoint.direct if swapped_slot == "commands" else checkpoint.commands
    with pytest.raises(ValueError):
        inputs.build(checkpoint.path, checkpoint.digest, direct, commands)
