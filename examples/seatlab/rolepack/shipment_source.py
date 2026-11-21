from hashlib import sha256
from pathlib import Path
from typing import Any

from ..attention.protocol import aliases
from ..decision_cross import audit, inputs
from ..decision_cross.stock import _native_sources
from ..turn_fork import packet as checkpoint
from . import protocol

_PACKET_SHA256 = "158b218350a788a3aab116e7ea61780930c63a46a470efb6bf13d3b0e7177fb9"
_BOARD_SHA256 = "60b976b6666acde139605d14a855b9d21b66da8d94343d05a029a585b49d4c7b"
_RECEIVER_REPORT = "decision-cross-direct-live.json"
_MAX_RECEIVER_BYTES = 256 * 1024
_BAYS = ("A", "B", "C", "D")
_TASK = "shipment"
_STATES = ("unavailable", "unclaimed", "incomplete", "complete")


def _select(board: dict[str, Any], source_id: str) -> dict[str, Any]:
    chosen = min(
        (obs for obs in board["observations"] if obs["source_id"] == source_id),
        key=lambda obs: obs["prompt_sha256"],
        default=None,
    )
    if chosen is None:
        raise ValueError("registered shipment source has no dispatch observation")
    return chosen


def _record(
    packet: dict[str, Any],
    source: dict[str, Any],
    obs: dict[str, Any],
    draw: dict[str, Any],
    report_sha256: str,
) -> dict[str, Any]:
    cases = source["cases"]
    names = aliases(cases, source["seed"], source["repetition"])
    peer = None
    if draw["state"] == "complete":
        choices = {
            action["id"]: action["choice"] for action in draw["outcome"]["actions"]
        }
        peer = {
            "answer": {
                "choices": {alias: choices[case_id] for alias, case_id in names.items()}
            },
            "run": draw["run"],
            "source_sha256": report_sha256,
            "response_sha256": draw["turn"]["response_sha256"],
        }
    items = []
    for alias, case_id in names.items():
        case = cases[case_id]
        optimal = max(case["after"], key=case["utilities"].__getitem__)
        items.append({"key": alias, "valid": list(_BAYS), "optimal": [optimal]})
    return {
        "id": f"ship-{source['source_id']}",
        "task": _TASK,
        "world": f"s{source['seed']}",
        "prompt": inputs.prompt(packet, obs),
        "prompt_sha256": obs["prompt_sha256"],
        "peer": peer,
        "rubric": {"field": "choices", "scalar": False, "items": items},
    }


def _load(
    results: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    root = results.absolute()
    if ".." in root.parts or root != root.resolve(strict=True) or not root.is_dir():
        raise ValueError("use the physical shipment results directory")
    packet, board, digest = inputs.build(
        root / "turn-fork-packet.json",
        _PACKET_SHA256,
        root / "turn-fork-direct-live.json",
        root / "turn-fork-subscription-live.json",
    )
    if digest != _BOARD_SHA256:
        raise ValueError("shipment sender observations differ from the frozen register")
    raw = checkpoint._read_bytes(root / _RECEIVER_REPORT, _MAX_RECEIVER_BYTES)
    report = audit.verify(
        packet, board, digest, checkpoint._decode(raw, integers_only=False)
    )
    if report["transport"] != "direct":
        raise ValueError("shipment rolepack requires direct receiver draws")
    source_sha256 = sha256(raw).hexdigest()
    sources = inputs.source_index(packet)
    if len(sources) != len(checkpoint.SEEDS) * len(checkpoint.REPETITIONS):
        raise ValueError("shipment rolepack requires all 12 registered source teams")
    first = {row["obs_id"]: row for row in report["rows"] if row["draw"] == 0}
    records = []
    for source_id, source in sorted(sources.items()):
        obs = _select(board, source_id)
        records.append(
            _record(packet, source, obs, first[obs["obs_id"]], source_sha256)
        )
    return sources, records


def export(results: Path) -> list[dict[str, Any]]:
    return _load(results)[1]


def _shipment_rows(
    pack: dict[str, Any], report: dict[str, Any], digest: str
) -> list[dict[str, Any]]:
    header = checkpoint._object(report, "schema", "pack_sha256", "rows")
    plan = protocol.plan(pack)
    rows = header["rows"]
    if (
        header["schema"] != "seatlab.rolepack-report/v1"
        or header["pack_sha256"] != digest
        or not isinstance(rows, list)
        or len(rows) != len(plan)
    ):
        raise ValueError("shipment check requires a report bound to the full pack plan")
    shipped = []
    for raw, expected in zip(rows, plan, strict=True):
        row = checkpoint._object(raw, *expected, "state", "turn", "outcome")
        if (
            type(row["index"]) is not int
            or type(row["draw"]) is not int
            or any(row[key] != value for key, value in expected.items())
        ):
            raise ValueError(
                "rolepack report row differs from its planned task or source"
            )
        if expected["task"] != _TASK:
            continue
        state, turn = row["state"], row["turn"]
        prompt_sha256 = expected["prompt_sha256"]
        if (
            state not in _STATES
            or (state == "unavailable") != (prompt_sha256 is None)
            or (state == "complete") != (row["outcome"] is not None)
            or (state in ("unavailable", "unclaimed") and turn is not None)
            or (state == "complete" and turn is None)
        ):
            raise ValueError("shipment row state has inconsistent evidence")
        if turn is not None:
            hashes = checkpoint._object(turn, "prompt_sha256", "response_sha256")
            if hashes["prompt_sha256"] != prompt_sha256:
                raise ValueError("shipment response belongs to another prompt")
            checkpoint._digest(hashes["response_sha256"])
        shipped.append(row)
    return shipped


def _score_row(
    row: dict[str, Any],
    record: dict[str, Any],
    source: dict[str, Any],
    world: dict[str, Any],
) -> dict[str, Any]:
    from ..turn_fork.stock import _score_choices

    outcome = checkpoint._object(row["outcome"], "values", "correct")
    values, correct = outcome["values"], outcome["correct"]
    items = record["rubric"]["items"]
    if (
        not isinstance(values, list)
        or not isinstance(correct, list)
        or len(values) != len(items)
        or len(correct) != len(items)
        or any(type(value) is not str or value not in _BAYS for value in values)
        or any(type(flag) is not bool for flag in correct)
    ):
        raise ValueError("invalid shipment outcome")
    names = aliases(world["cases"], source["seed"], source["repetition"])
    ids = [names[item["key"]] for item in items]
    choices = dict(zip(ids, values, strict=True))
    actions = [
        {"id": case["id"], "choice": choices[case["id"]]} for case in world["cases"]
    ]
    score = _score_choices(world, actions)
    flags = {action["id"]: action["optimal"] for action in score["rows"]}
    if (
        correct != [flags[case_id] for case_id in ids]
        or score["episodes"] != len(values)
        or score["optimal"] != sum(correct)
    ):
        raise ValueError("shipment grades differ from native stock scores")
    return score


def check(
    results: Path,
    pack: dict[str, Any],
    report: dict[str, Any],
    native_study: Path,
) -> dict[str, Any]:
    pack = protocol.validate(pack)
    digest = protocol.digest(pack)
    sources, enrolled = _load(results)
    records = {record["id"]: record for record in enrolled}
    if records != {
        record["id"]: record for record in pack["records"] if record["task"] == _TASK
    }:
        raise ValueError("pack shipments differ from the registered source records")
    rows = _shipment_rows(pack, report, digest)
    worlds, original = _native_sources(sources, native_study)
    if worlds.keys() != sources.keys():
        raise ValueError("native shipment worlds do not cover the registered sources")
    sources_by_record = {f"ship-{source_id}": source_id for source_id in sources}
    arms = {
        arm: dict.fromkeys((*_STATES, "actions", "optimal", "feasible"), 0)
        for arm in ("original", "peer")
    }
    for row in rows:
        record_id = row["record_id"]
        counts = arms[row["arm"]]
        counts[row["state"]] += 1
        if row["state"] == "complete":
            source_id = sources_by_record[record_id]
            score = _score_row(
                row, records[record_id], sources[source_id], worlds[source_id]
            )
            counts["actions"] += score["episodes"]
            counts["optimal"] += score["optimal"]
            counts["feasible"] += score["feasible"]
    return {
        "schema": "seatlab.rolepack-shipment-stock/v1",
        "pack_sha256": digest,
        "measurement": "offline_rescore_not_new_native_rewards",
        "source_teams": len(worlds),
        "native_original": original,
        "conditional": arms,
    }
