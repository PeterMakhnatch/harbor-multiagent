import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

from examples.seatlab.handoff.analysis import summarize
from examples.seatlab.handoff.episode import Turn, episode
from examples.seatlab.handoff.packet import Block, grid, parse


type _Row = dict[str, Any]


def _blocks() -> tuple[Block, ...]:
    cases = []
    for case, cluster in (("a", "c1"), ("b", "c2"), ("c", "c2"), ("stable", "c3")):
        changed = case != "stable"
        cases.append(
            {
                "id": case,
                "cluster": cluster,
                "records": [
                    {
                        "id": "P",
                        "old_version": "1",
                        "new_version": "2" if changed else "1",
                        "before": "Old" if changed else "Current",
                        "after": "Current",
                        "question": "Question P",
                        "old_best": "A" if case in ("a", "c") else "B",
                        "best": "B",
                    },
                    {
                        "id": "Q",
                        "old_version": "1",
                        "new_version": "1",
                        "before": "Unchanged",
                        "after": "Unchanged",
                        "question": "Question Q",
                        "old_best": "A",
                        "best": "A",
                    },
                ],
            }
        )
    raw = json.dumps(
        {
            "schema": "seatlab.handoff-input/v1",
            "brief": "Select a label.",
            "choices": ["A", "B"],
            "cases": cases,
        }
    ).encode()
    return grid(parse(raw))


def _row(block: Block, schedule: dict[str, str]) -> _Row:
    turns: list[_Row] = []

    def ask(turn: Turn) -> str | None:
        response = schedule.get(turn.stage)
        if response is not None:
            turns.append(
                {
                    "run": f"{block.key}-{turn.stage}",
                    "role": turn.role,
                    "status": "received",
                    "prompt_sha256": sha256(turn.prompt.encode()).hexdigest(),
                    "response_sha256": sha256(response.encode()).hexdigest(),
                    "usage": None,
                }
            )
        return response

    return {
        "block": block.key,
        "case": block.case.id,
        "cluster": block.case.cluster,
        "rep": block.rep,
        "fingerprint": block.fingerprint,
        "state": "claimed" if schedule else "unclaimed",
        "outcome": episode(block, ask),
        "turns": turns,
    }


def _right() -> str:
    return '{"choices":{"P":"B","Q":"A"}}'


def _wrong() -> str:
    return '{"choices":{"P":"A","Q":"A"}}'


def _split(bound: str, anonymous: str) -> dict[str, str]:
    return {
        "note": '{"note":"Read the stock."}',
        "bound-select": '{"read":"P"}',
        "anonymous-select": '{"read":"Q"}',
        "bound-decide": bound,
        "anonymous-decide": anonymous,
    }


def test_handoff_paired_clusters_keep_known_sides_and_shared_draws() -> None:
    blocks = _blocks()
    bad_select = _split(_wrong(), _right())
    bad_select["anonymous-select"] = '{"read":"Z"}'
    del bad_select["anonymous-decide"]
    shared = {
        "note": '{"note":"Read the stock."}',
        "bound-select": '{"read":"Q"}',
        "anonymous-select": '{"read":"Q"}',
    }
    schedules = (
        _split(_right(), _wrong()),
        bad_select,
        _split(_wrong(), _right()),
        _split(_right(), _right()),
        _split(_right(), "?"),
        shared,
        shared | {"both-decide": _right()},
        {"note": "?"},
    )
    rows = [
        _row(block, schedule) for block, schedule in zip(blocks, schedules, strict=True)
    ]
    report = summarize(blocks, list(reversed(rows)))
    primary = report["primary"]
    assert primary["kind"] == "bound_minus_anonymous"
    assert (primary["complete_pairs"], primary["scheduled_pairs"]) == (3, 6)
    assert primary["paired_complete_delta"] == 0.25
    assert primary["by_cluster"]["c1"]["paired_complete_delta"] == 1.0
    assert primary["by_cluster"]["c2"]["paired_complete_delta"] == -0.5
    assert (primary["worst_delta"], primary["best_delta"]) == (-0.125, 0.25)
    assert (primary["gain"], primary["loss"], primary["tie"]) == (1, 1, 1)
    controls = report["stable_controls"]
    assert set(controls["by_cluster"]) == {"c3"}
    assert (controls["complete_pairs"], controls["scheduled_pairs"]) == (1, 2)
    assert controls["paired_complete_delta"] == 0.0
    bound, anonymous = (report["arms"][policy] for policy in ("bound", "anonymous"))
    assert bound["changed_read"] == {
        "scheduled": 6,
        "selected": 6,
        "read_changed": 5,
        "rate_among_selected": 5 / 6,
    }
    assert bound["source_sensitive_changes"] == {
        "scheduled": 4,
        "observed": 3,
        "correct": 2,
        "accuracy": 2 / 3,
    }
    assert anonymous["statuses"]["invalid_select"] == 1
    assert anonymous["statuses"]["invalid_decide"] == 1
    assert anonymous["statuses"]["missing_decide"] == 1
    assert report["counts"]["shared_final_complete"] == 1
    assert report["counts"]["notes"] == {"missing": 0, "invalid": 1, "valid": 7}
    assert report["counts"]["selection_draws"] == {
        "neither_selected": 1,
        "one_selected": 1,
        "same_record": 2,
        "different_records": 4,
    }
    assert report["counts"]["physical_turns"]["route_decide"] == {
        "received": 24,
        "callback_error": 0,
    }


def test_handoff_unattempted_cluster_blocks_complete_delta() -> None:
    blocks = _blocks()
    schedules = {
        0: _split(_right(), _wrong()),
        1: {"note": '{"note":"Read the stock."}'},
    }
    rows = [_row(block, schedules.get(index, {})) for index, block in enumerate(blocks)]
    primary = summarize(blocks, rows)["primary"]
    assert (primary["clusters_with_pairs"], primary["enrolled_clusters"]) == (1, 2)
    assert primary["paired_complete_delta"] is None
    assert (primary["worst_delta"], primary["best_delta"]) == (-0.5, 1.0)
    stable = summarize(blocks[-2:], rows[-2:])["primary"]
    assert stable["scheduled_pairs"] == 0
    assert (
        stable["paired_complete_delta"]
        is stable["worst_delta"]
        is stable["best_delta"]
        is None
    )


def test_handoff_rejects_nonmatching_public_rows() -> None:
    blocks = _blocks()
    rows = [_row(block, _split(_right(), _wrong())) for block in blocks]
    bad_fingerprint = [rows[0] | {"fingerprint": "0" * 64}, *rows[1:]]
    bad_choice = deepcopy(rows)
    bad_choice[0]["outcome"]["arms"]["bound"]["choices"]["P"] = "C"
    false_unclaimed = [rows[0] | {"state": "unclaimed"}, *rows[1:]]
    for bad in (
        rows[:-1],
        [rows[0], *rows[:-1]],
        bad_fingerprint,
        bad_choice,
        false_unclaimed,
    ):
        try:
            summarize(blocks, bad)
        except ValueError:
            continue
        raise AssertionError("accepted a nonmatching public row")
