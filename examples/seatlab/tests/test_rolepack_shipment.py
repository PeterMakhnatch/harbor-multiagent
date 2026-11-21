import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.rolepack import protocol, shipment_source
from examples.seatlab.turn_fork.protocol import decide_prompt

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_RECEIVER = "decision-cross-direct-live.json"
_CALENDAR_ID = "cal-rx-w00-d0-r0-blind"


@pytest.fixture(scope="module")
def public() -> tuple[dict[str, Any], dict[str, Any], bytes]:
    packet = json.loads((_RESULTS / "turn-fork-packet.json").read_bytes())
    board = json.loads((_RESULTS / "decision-cross-board.json").read_bytes())
    return packet, board, (_RESULTS / _RECEIVER).read_bytes()


def test_export_uses_exact_minimum_prompt_and_first_direct_actions(
    public: tuple[dict[str, Any], dict[str, Any], bytes],
) -> None:
    packet, board, raw = public
    records = shipment_source.export(_RESULTS)
    sources = {f"ship-{source['source_id']}": source for source in packet["sources"]}
    assert len(records) == len(sources) == 12
    assert [record["id"] for record in records] == sorted(sources)
    draw_zero = {
        row["obs_id"]: row for row in json.loads(raw)["rows"] if row["draw"] == 0
    }
    reverse_board = dict(board, observations=board["observations"][::-1])
    for record in records:
        source = sources[record["id"]]
        observations = sorted(
            (
                obs
                for obs in board["observations"]
                if obs["source_id"] == source["source_id"]
            ),
            key=lambda obs: obs["prompt_sha256"],
        )
        chosen = observations[0]
        assert shipment_source._select(reverse_board, source["source_id"]) == chosen
        assert record["task"] == "shipment"
        assert record["world"] == f"s{source['seed']}"
        opened = (chosen["opened"][0], chosen["opened"][1])
        assert record["prompt"] == decide_prompt(source, opened)
        assert record["prompt_sha256"] == chosen["prompt_sha256"]
        assert sha256(record["prompt"].encode()).hexdigest() == chosen["prompt_sha256"]
        expected = draw_zero[chosen["obs_id"]]
        choices = {row["id"]: row["choice"] for row in expected["outcome"]["actions"]}
        names = aliases(source["cases"], source["seed"], source["repetition"])
        assert record["peer"] == {
            "answer": {
                "choices": {alias: choices[case_id] for alias, case_id in names.items()}
            },
            "run": expected["run"],
            "source_sha256": sha256(raw).hexdigest(),
            "response_sha256": expected["turn"]["response_sha256"],
        }


def test_missing_selected_peer_leaves_all_four_rows_unavailable(
    tmp_path: Path, public: tuple[dict[str, Any], dict[str, Any], bytes]
) -> None:
    _, board, raw = public
    observations = [
        obs for obs in board["observations"] if obs["source_id"] == "s6501-r1"
    ]
    assert len(observations) > 1
    selected = min(observations, key=lambda obs: obs["prompt_sha256"])
    report = json.loads(raw)
    draw = next(
        row
        for row in report["rows"]
        if row["obs_id"] == selected["obs_id"] and row["draw"] == 0
    )
    assert draw["state"] == "complete"
    draw.update(state="incomplete", outcome=None)
    results = tmp_path.resolve()
    for name in (
        "turn-fork-packet.json",
        "turn-fork-direct-live.json",
        "turn-fork-subscription-live.json",
    ):
        shutil.copyfile(_RESULTS / name, results / name)
    (results / _RECEIVER).write_bytes(json.dumps(report, indent=2).encode() + b"\n")
    records = shipment_source.export(results)
    assert len(records) == 12
    assert [record["id"] for record in records if record["peer"] is None] == [
        "ship-s6501-r1"
    ]
    missing = next(record for record in records if record["id"] == "ship-s6501-r1")
    assert missing["prompt_sha256"] == selected["prompt_sha256"]
    rows = protocol.plan({"schema": "seatlab.rolepack/v1", "records": records})
    assert {
        (row["draw"], row["arm"], row["prompt_sha256"])
        for row in rows
        if row["record_id"] == missing["id"]
    } == {(draw, arm, None) for draw in (0, 1) for arm in ("original", "peer")}
    new_sha256 = sha256((results / _RECEIVER).read_bytes()).hexdigest()
    assert all(
        record["peer"]["source_sha256"] == new_sha256
        for record in records
        if record["peer"] is not None
    )


def _calendar_record() -> dict[str, Any]:
    prompt = "Choose a time. Reply with a JSON object containing slot."
    return {
        "id": _CALENDAR_ID,
        "task": "calendar",
        "world": "s6501",
        "prompt": prompt,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "peer": {
            "answer": {"slot": 1},
            "run": "calendar-first-draw",
            "source_sha256": "1" * 64,
            "response_sha256": None,
        },
        "rubric": {
            "field": "slot",
            "scalar": True,
            "items": [{"key": "slot", "valid": [1, 2], "optimal": [2]}],
        },
    }


def _unclaimed_report(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "seatlab.rolepack-report/v1",
        "pack_sha256": protocol.digest(pack),
        "rows": [
            dict(row, state="unclaimed", turn=None, outcome=None)
            for row in protocol.plan(pack)
        ],
    }


@pytest.mark.parametrize("foreign_id", (_CALENDAR_ID, "ship-s6501-r1"))
def test_check_rejects_a_report_row_bound_to_a_different_task_or_source(
    tmp_path: Path, foreign_id: str
) -> None:
    records = shipment_source.export(_RESULTS)
    pack = {"schema": "seatlab.rolepack/v1", "records": [*records, _calendar_record()]}
    report = _unclaimed_report(pack)
    row = next(row for row in report["rows"] if row["record_id"] == "ship-s6501-r0")
    row["record_id"] = foreign_id
    with pytest.raises(ValueError, match="differs from its planned task or source"):
        shipment_source.check(_RESULTS, pack, report, tmp_path / "unused-native-study")


def test_check_rejects_peer_proposals_swapped_within_a_world(tmp_path: Path) -> None:
    records = shipment_source.export(_RESULTS)
    first, second = records[:2]
    assert first["world"] == second["world"]
    first["peer"], second["peer"] = second["peer"], first["peer"]
    pack = {"schema": "seatlab.rolepack/v1", "records": records}
    with pytest.raises(ValueError, match="differ from the registered source records"):
        shipment_source.check(
            _RESULTS, pack, _unclaimed_report(pack), tmp_path / "unused-native-study"
        )
