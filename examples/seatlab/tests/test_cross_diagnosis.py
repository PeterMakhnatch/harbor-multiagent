import json
from collections.abc import Callable
from hashlib import sha256
from itertools import product
from typing import Any
from uuid import UUID

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import audit, diagnosis, inputs
from examples.seatlab.turn_fork import packet as native_packet
from examples.seatlab.turn_fork import protocol
from examples.seatlab.visibility.clarify_run import _encode

type _Document = dict[str, Any]


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _packet(slots: list[tuple[int, int]]) -> _Document:
    upstream = {"prompt_sha256": _hash("prompt"), "response_sha256": _hash("reply")}
    return native_packet._validate(
        {
            "schema": native_packet.SCHEMA,
            "source_report_sha256": _hash("native report"),
            "sources": [
                {
                    "source_id": f"s{seed}-r{rep}",
                    "seed": seed,
                    "repetition": rep,
                    "trial_id": str(UUID(int=2 * seed + rep)),
                    "world_sha256": _hash(str(seed)),
                    "upstream": {"inventory": upstream, "brief": upstream},
                    "card": "CCCCCA",
                    "cases": [
                        {
                            "id": case,
                            "utilities": {"A": 4, "B": 1, "C": 3, "D": 2},
                            "reports": [
                                {"revision": 1, "available": ["A", "B"]},
                                {
                                    "revision": 2,
                                    "available": ["B", "C"] if case < 5 else ["A", "B"],
                                },
                            ],
                            "after": ["B", "C"] if case < 5 else ["A", "B"],
                        }
                        for case in range(6)
                    ],
                }
                for seed, rep in slots
            ],
        }
    )


def _aliases(source: _Document) -> tuple[list[str], str]:
    names = aliases(source["cases"], source["seed"], source["repetition"])
    return [name for name, case in names.items() if case != 5], next(
        name for name, case in names.items() if case == 5
    )


def _board(packet: _Document, *, varied_source: str) -> tuple[_Document, str]:
    sources = inputs.source_index(packet)
    observations: dict[str, _Document] = {}
    selections: list[_Document] = []
    for seed, rep, sender, arm in product(
        native_packet.SEEDS,
        native_packet.REPETITIONS,
        ("direct", "commands"),
        ("aligned", "repeat"),
    ):
        source_id = f"s{seed}-r{rep}"
        obs_id = None
        if source_id in sources:
            source = sources[source_id]
            changed, _ = _aliases(source)
            use_second = source_id == varied_source and (sender, arm) == (
                "commands",
                "repeat",
            )
            a, b = changed[2:4] if use_second else changed[:2]
            opened = (a, b)
            digest = _hash(protocol.decide_prompt(source, opened))
            obs_id = f"{source_id}-{digest[:12]}"
            observations[obs_id] = {
                "source_id": source_id,
                "obs_id": obs_id,
                "prompt_sha256": digest,
                "opened": list(opened),
            }
        selections.append(
            {
                "source_id": source_id,
                "sender": sender,
                "arm": arm,
                "obs_id": obs_id,
                "sender_response_sha256": _hash(obs_id) if obs_id else None,
            }
        )
    board = {
        "schema": "seatlab.decision-cross-board/v1",
        "packet_sha256": sha256(_encode(packet)).hexdigest(),
        "direct_report_sha256": _hash("direct senders"),
        "commands_report_sha256": _hash("command senders"),
        "observations": sorted(
            observations.values(), key=lambda o: (o["source_id"], o["prompt_sha256"])
        ),
        "selections": selections,
    }
    return board, sha256(_encode(board)).hexdigest()


def _report(
    packet: _Document,
    board: _Document,
    digest: str,
    reply: Callable[[_Document, _Document], dict[str, str] | None],
) -> _Document:
    sources = inputs.source_index(packet)
    observations = {o["obs_id"]: o for o in board["observations"]}
    rows = []
    for draw in inputs.draws(board):
        source = sources[draw["source_id"]]
        obs = observations[draw["obs_id"]]
        choices = reply(source, draw)
        turn = outcome = None
        if choices is not None:
            text = json.dumps({"choices": choices})
            outcome = protocol.grade(source, tuple(obs["opened"]), text)
            turn = {
                "status": "received",
                "prompt_sha256": draw["prompt_sha256"],
                "response_sha256": _hash(text),
                "usage": None,
            }
        rows.append(
            {
                **draw,
                "run": inputs.run_id(digest, draw["obs_id"], draw["draw"]),
                "state": "complete" if turn is not None else "unclaimed",
                "turn": turn,
                "outcome": outcome,
            }
        )
    return audit.verify(
        packet,
        board,
        digest,
        {
            "schema": "seatlab.decision-cross-report/v1",
            "board_sha256": digest,
            "transport": "commands",
            "rows": rows,
        },
    )


def _truth(source: _Document) -> dict[str, str]:
    changed, steady = _aliases(source)
    return dict.fromkeys(changed, "C") | {steady: "A"}


def _wrong(source: _Document, _: _Document) -> dict[str, str]:
    changed, steady = _aliases(source)
    return dict.fromkeys([*changed, steady], "D")


def test_diagnosis_splits_stale_agreement_truth_and_weighted_reuse() -> None:
    packet = _packet([(6501, 0)])
    source = packet["sources"][0]
    changed, _ = _aliases(source)
    mapping = aliases(source["cases"], source["seed"], source["repetition"])
    source["cases"][mapping[changed[3]]]["reports"][1]["available"] = ["B", "D"]
    board, digest = _board(packet, varied_source="s6501-r0")
    first = board["selections"][0]["obs_id"]

    def reply(source: _Document, draw: _Document) -> dict[str, str] | None:
        if draw["draw"] == 1:
            return None
        changed, steady = _aliases(source)
        if draw["obs_id"] != first:
            return _truth(source)
        return dict(zip(changed, "CACAD", strict=True)) | {steady: "A"}

    report = _report(packet, board, digest, reply)
    error = next(
        row for row in report["rows"] if row["draw"] == 1 and row["obs_id"] == first
    )
    error["state"] = "incomplete"
    error["turn"] = {
        "status": "callback_error",
        "prompt_sha256": error["prompt_sha256"],
        "response_sha256": None,
        "usage": None,
    }
    result = diagnosis.describe(
        packet, board, digest, audit.verify(packet, board, digest, report)
    )
    unique = result["unique_prompt_draws"]
    weighted = result["logical_uses"]

    assert unique["categories"] == {
        "visible_truth_agree_chose_best": 4,
        "visible_truth_agree_chose_other": 1,
        "visible_truth_differ_chose_visible": 1,
        "visible_truth_differ_chose_truth": 5,
        "visible_truth_differ_chose_neither": 1,
    }
    assert unique["scheduled_visible_truth_agreements"] == 10
    assert unique["native_truth_accuracy"] == 9 / 12
    assert unique["visible_adherence"] == 5 / 12
    assert unique["missing_first_attempts"] == 2
    assert unique["incomplete_callback_error"] == 1
    assert unique["unclaimed"] == 1
    assert weighted["categories"] == {
        "visible_truth_agree_chose_best": 8,
        "visible_truth_agree_chose_other": 3,
        "visible_truth_differ_chose_visible": 3,
        "visible_truth_differ_chose_truth": 7,
        "visible_truth_differ_chose_neither": 3,
    }
    assert weighted["native_truth_accuracy"] == 15 / 24
    assert weighted["missing_first_attempts"] == 4
    assert weighted["incomplete_callback_error"] == 3
    assert weighted["no_sender_selection_draws"] == 88
    assert result["sources"] == {
        "registered": 12,
        "in_packet": 1,
        "scheduled": 1,
        "complete": 1,
    }
    assert result["worlds"] == {
        "registered": 6,
        "in_packet": 1,
        "scheduled": 1,
        "complete": 1,
    }


def test_compare_command_receivers_uses_equal_worlds_not_equal_unique_draws() -> None:
    packet = _packet(list(product(native_packet.SEEDS, native_packet.REPETITIONS)))
    board, digest = _board(packet, varied_source="s6501-r0")
    baseline = _report(packet, board, digest, _wrong)
    candidate = _report(
        packet,
        board,
        digest,
        lambda source, draw: (
            _truth(source) if source["seed"] == 6501 else _wrong(source, draw)
        ),
    )

    result = diagnosis.compare(packet, board, digest, baseline, candidate)
    change = result["equal_world_change"]

    assert change["estimate"] == 1 / 6
    assert change["registered_bounds"] == {"lower": 1 / 6, "upper": 1 / 6}
    assert change["fully_observed_worlds"] == 6
    assert result["candidate"]["unique_prompt_draws"]["native_truth_accuracy"] == 6 / 26
    assert result["paired"]["same_response_sha256"] == 20
    assert result["within_source"][0]["complete_pairs"] == 4
    assert result["within_source"][0]["same_actions"] == 0
    assert result["identical_public_evidence"] is False

    self_comparison = diagnosis.compare(packet, board, digest, baseline, baseline)
    assert self_comparison["equal_world_change"]["estimate"] == 0
    assert self_comparison["paired"]["same_response_sha256"] == 26
    assert self_comparison["identical_public_evidence"] is True
    assert "not independent replication" in self_comparison["evidence_caveat"]


def test_compare_missing_world_is_not_observed_zero_change() -> None:
    packet = _packet(list(product(native_packet.SEEDS, native_packet.REPETITIONS)))
    board, digest = _board(packet, varied_source="s6501-r0")
    baseline = _report(packet, board, digest, _wrong)
    candidate = _report(
        packet,
        board,
        digest,
        lambda source, draw: None if source["seed"] == 6501 else _wrong(source, draw),
    )

    result = diagnosis.compare(packet, board, digest, baseline, candidate)
    change = result["equal_world_change"]

    assert change["estimate"] is None
    assert change["matched_observed_estimate"] is None
    assert change["matched_worlds"] == 5
    assert change["fully_observed_worlds"] == 5
    assert change["registered_bounds"] == {"lower": 0, "upper": 1 / 6}
    assert result["paired"]["baseline_only"] == 6
    assert result["paired"]["candidate_only"] == 0
    assert result["candidate"]["unique_prompt_draws"]["native_truth_accuracy"] == 0
    assert result["candidate"]["logical_uses"]["missing_first_attempts"] == 16
