import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import inputs
from examples.seatlab.peer_update import protocol
from examples.seatlab.turn_fork import packet as checkpoint
from examples.seatlab.turn_fork import protocol as fork
from examples.seatlab.visibility.deepseek import REVISION

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_ARMS = ("solo-r1", "draft-r1", "solo-r2", "draft-r2")
_USAGE = {
    "served_model": REVISION,
    "input_tokens": 13,
    "cache_tokens": 4,
    "output_tokens": 2,
}
type _Document = dict[str, Any]


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _canonical_digest(document: _Document) -> str:
    return _digest(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def _reply(draw: int) -> str:
    bays = ("ABCDAB", "DCBADC")[draw]
    return json.dumps({"choices": {f"Q{i}": bay for i, bay in enumerate(bays, 1)}})


def _prior_report(packet: _Document, board: _Document, digest: str) -> _Document:
    sources = inputs.source_index(packet)
    observations = {obs["obs_id"]: obs for obs in board["observations"]}
    rows = []
    for draw in inputs.draws(board):
        obs_id, number = draw["obs_id"], draw["draw"]
        reply = _reply(number)
        opened = tuple(observations[obs_id]["opened"])
        rows.append(
            draw
            | {
                "run": inputs.run_id(digest, obs_id, number),
                "state": "complete",
                "turn": {
                    "status": "received",
                    "prompt_sha256": draw["prompt_sha256"],
                    "response_sha256": _digest(reply),
                    "usage": _USAGE.copy(),
                },
                "outcome": fork.grade(sources[draw["source_id"]], opened, reply),
            }
        )
    return {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": "direct",
        "rows": rows,
    }


@pytest.fixture
def cohort() -> tuple[_Document, _Document, str, _Document]:
    packet = checkpoint.load(
        _RESULTS / "turn-fork-packet.json",
        (_RESULTS / "turn-fork.sha256").read_text().strip(),
    )
    board = json.loads((_RESULTS / "decision-cross-board.json").read_bytes())
    board["observations"].reverse()
    digest = _canonical_digest(board)
    return packet, board, digest, _prior_report(packet, board, digest)


def test_panel_uses_minimum_actual_prompt_commitment_and_rotates_arms(cohort) -> None:
    packet, board, digest, report = cohort
    panel = protocol.build(*cohort)
    sources = inputs.source_index(packet)
    assert len(sources) == 12
    scheduled_sources = sorted(sources)
    assert [source["source_id"] for source in panel["sources"]] == scheduled_sources
    for meta in panel["sources"]:
        source_id = meta["source_id"]
        source = sources[source_id]
        actual = min(
            (obs for obs in board["observations"] if obs["source_id"] == source_id),
            key=lambda obs: _digest(fork.decide_prompt(source, tuple(obs["opened"]))),
        )
        target = next(f"Q{i}" for i in range(1, 7) if f"Q{i}" not in actual["opened"])
        assert meta == {
            "source_id": source_id,
            "obs_id": actual["obs_id"],
            "peer_run": inputs.run_id(digest, actual["obs_id"], 0),
            "peer_response_sha256": _digest(_reply(0)),
            "target": target,
            "opened": actual["opened"],
            "available": True,
        }
    assert len(panel["rows"]) == 48
    for index, row in enumerate(panel["rows"]):
        round_index, source_index = divmod(index, 12)
        meta = panel["sources"][source_index]
        arm = _ARMS[(round_index + source_index) % 4]
        assert row == {
            "index": index,
            "source_id": meta["source_id"],
            "obs_id": meta["obs_id"],
            "sample_id": f"{meta['source_id']}:{arm}",
            "target": meta["target"],
            "arm": arm,
            "prompt_sha256": _digest(protocol.prompt(packet, board, report, row)),
        }
    assert panel["board_sha256"] == digest
    assert panel["direct_report_sha256"] == _canonical_digest(report)
    assert protocol.panel_digest(panel) == _canonical_digest(panel)


def test_missing_first_draw_does_not_use_another_draw_or_observation(cohort) -> None:
    packet, board, _, report = cohort
    panel = protocol.build(*cohort)
    selected = next(
        meta
        for meta in panel["sources"]
        if sum(obs["source_id"] == meta["source_id"] for obs in board["observations"])
        > 1
    )
    missing = next(
        row
        for row in report["rows"]
        if row["draw"] == 0 and row["obs_id"] == selected["obs_id"]
    )
    missing.update(state="incomplete", outcome=None)
    changed = protocol.build(*cohort)
    assert changed["sources"] == [
        meta | {"available": False, "peer_response_sha256": None}
        if meta == selected
        else meta
        for meta in panel["sources"]
    ]
    assert changed["rows"] == [
        row | {"prompt_sha256": None}
        if row["source_id"] == selected["source_id"]
        else row
        for row in panel["rows"]
    ]
    for row in changed["rows"]:
        if row["source_id"] == selected["source_id"]:
            with pytest.raises(ValueError):
                protocol.prompt(packet, board, report, row)


def test_prompts_add_only_the_actual_target_report_and_optional_peer_choices(
    cohort,
) -> None:
    packet, board, _, report = cohort
    panel = protocol.build(*cohort)
    sources = inputs.source_index(packet)
    observations = {obs["obs_id"]: obs for obs in board["observations"]}
    for row in panel["rows"]:
        source = sources[row["source_id"]]
        names = aliases(source["cases"], source["seed"], source["repetition"])
        observation = observations[row["obs_id"]]
        case = source["cases"][names[row["target"]]]
        revision_index = {"r1": 0, "r2": 1}[row["arm"][-2:]]
        parts = protocol.prompt(packet, board, report, row).split("\n\n")
        assert parts[0] == fork.decide_prompt(source, tuple(observation["opened"]))
        heading, inventory = parts[1].split("\n", 1)
        assert heading == "Additional inventory report:"
        assert json.loads(inventory) == {
            "alias": row["target"],
            **case["reports"][revision_index],
        }
        if row["arm"].startswith("draft-"):
            heading, draft = parts[2].split("\n", 1)
            assert heading == "Peer draft:"
            assert json.loads(draft) == json.loads(_reply(0))["choices"]
            assert len(parts) == 3
        else:
            assert len(parts) == 2


def test_prompt_rejects_mismatched_board_report_row_and_draft(cohort) -> None:
    packet, board, digest, report = cohort
    panel = protocol.build(*cohort)
    row = next(row for row in panel["rows"] if row["arm"] == "draft-r2")
    with pytest.raises(ValueError):
        protocol.build(packet, board, "0" * 64, report)
    with pytest.raises(ValueError):
        protocol.prompt(packet, board, report | {"board_sha256": "0" * 64}, row)
    for patch in (
        {"index": False},
        {"obs_id": "another-observation"},
        {"target": "Q9"},
        {"sample_id": "another-sample"},
        {"arm": "solo-r1"},
        {"prompt_sha256": "0" * 64},
    ):
        with pytest.raises(ValueError):
            protocol.prompt(packet, board, report, row | patch)

    peer = next(
        draw
        for draw in report["rows"]
        if draw["obs_id"] == row["obs_id"] and draw["draw"] == 0
    )
    obs = next(obs for obs in board["observations"] if obs["obs_id"] == row["obs_id"])
    source = inputs.source_index(packet)[row["source_id"]]
    peer["turn"]["response_sha256"] = _digest(_reply(1))
    peer["outcome"] = fork.grade(source, tuple(obs["opened"]), _reply(1))
    protocol.build(packet, board, digest, report)
    with pytest.raises(ValueError, match="prompt commitment"):
        protocol.prompt(packet, board, report, row)


def test_grade_uses_stock_truth_and_preserves_valid_infeasible_actions(cohort) -> None:
    packet, board, _, _ = cohort
    panel = protocol.build(*cohort)
    meta = panel["sources"][0]
    source = inputs.source_index(packet)[meta["source_id"]]
    names = aliases(source["cases"], source["seed"], source["repetition"])
    target = names[meta["target"]]
    case = source["cases"][target]
    infeasible = next(bay for bay in "ABCD" if bay not in case["after"])
    reply = json.dumps({"choices": dict.fromkeys(names, infeasible)})
    stock_actions = fork.grade(source, tuple(meta["opened"]), reply)["actions"]
    for row in panel["rows"]:
        if row["source_id"] != meta["source_id"]:
            continue
        assert protocol.grade(packet, board, row, reply) == {
            "actions": stock_actions,
            "target": {
                "id": target,
                "choice": infeasible,
                "optimal": False,
                "feasible": False,
            },
        }
        with pytest.raises(ValueError):
            protocol.grade(packet, board, row, '{"choices":{"Q1":"A"}}')


def test_canonical_digest_keeps_utf8_and_rejects_nonfinite_values() -> None:
    assert protocol.panel_digest({"z": 1, "a": "é"}) == _digest('{"a":"é","z":1}\n')
    with pytest.raises(ValueError):
        protocol.panel_digest({"z": float("nan")})
