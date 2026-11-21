import json
from hashlib import sha256
from typing import Any

from ..attention import protocol as attention
from ..decision_cross import audit, inputs
from ..turn_fork import protocol as fork
from ..turn_fork.packet import REPETITIONS, SEEDS, _digest, _object

SCHEMA = "seatlab.peer-update-panel/v1"
_ARMS = ("solo-r1", "draft-r1", "solo-r2", "draft-r2")
_SOURCE_IDS = tuple(f"s{seed}-r{rep}" for seed in SEEDS for rep in REPETITIONS)
_ROW_FIELDS = (
    "index",
    "source_id",
    "obs_id",
    "sample_id",
    "target",
    "arm",
    "prompt_sha256",
)
type _Document = dict[str, Any]


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


def _text_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def panel_digest(panel: _Document) -> str:
    return _text_digest(_json(panel) + "\n")


def _schedule(index: int) -> tuple[str, str]:
    if type(index) is not int or not 0 <= index < len(_ARMS) * len(_SOURCE_IDS):
        raise ValueError("invalid peer-update row index")
    round_index, source_index = divmod(index, len(_SOURCE_IDS))
    return _SOURCE_IDS[source_index], _ARMS[(round_index + source_index) % len(_ARMS)]


def _select(
    board: _Document, source: _Document
) -> tuple[_Document, dict[str, int], str]:
    observation = min(
        (
            obs
            for obs in board["observations"]
            if obs["source_id"] == source["source_id"]
        ),
        key=lambda obs: obs["prompt_sha256"],
        default=None,
    )
    if observation is None:
        raise ValueError("registered source has no dispatcher observation")
    opened = attention._open_pair(observation["opened"])
    names = attention.aliases(source["cases"], source["seed"], source["repetition"])
    return observation, names, min(names.keys() - set(opened))


def _direct_rows(
    packet: _Document, board: _Document, digest: str, report: _Document
) -> dict[str, _Document]:
    checked = audit.verify(packet, board, digest, report)
    if checked["transport"] != "direct":
        raise ValueError("peer drafts require the direct receiver report")
    return {row["obs_id"]: row for row in checked["rows"] if row["draw"] == 0}


def _render(
    base: str,
    source: _Document,
    names: dict[str, int],
    target: str,
    arm: str,
    peer: _Document,
) -> str:
    case = next(case for case in source["cases"] if case["id"] == names[target])
    revision = 1 if arm.endswith("r1") else 2
    report = case["reports"][revision - 1]
    if type(report["revision"]) is not int or report["revision"] != revision:
        raise ValueError("invalid inventory report revision")
    inventory = {
        "alias": target,
        "revision": report["revision"],
        "available": report["available"],
    }
    text = base + "\n\nAdditional inventory report:\n" + _json(inventory)
    if arm.startswith("draft-"):
        choices = {
            action["id"]: action["choice"] for action in peer["outcome"]["actions"]
        }
        draft = {alias: choices[case_id] for alias, case_id in names.items()}
        text += "\n\nPeer draft:\n" + _json(draft)
    return text


def _source_plan(
    packet: _Document,
    board: _Document,
    source: _Document,
    peers: dict[str, _Document],
) -> tuple[_Document, dict[str, str | None]]:
    obs, names, target = _select(board, source)
    peer = peers[obs["obs_id"]]
    available = peer["state"] == "complete"
    metadata = {
        "source_id": source["source_id"],
        "obs_id": obs["obs_id"],
        "peer_run": peer["run"],
        "peer_response_sha256": peer["turn"]["response_sha256"] if available else None,
        "target": target,
        "opened": list(obs["opened"]),
        "available": available,
    }
    if not available:
        return metadata, dict.fromkeys(_ARMS)
    base = inputs.prompt(packet, obs)
    digests: dict[str, str | None] = {
        arm: _text_digest(_render(base, source, names, target, arm, peer))
        for arm in _ARMS
    }
    return metadata, digests


def _row(
    index: int, source_id: str, obs_id: str, target: str, arm: str, digest: str | None
) -> _Document:
    return {
        "index": index,
        "source_id": source_id,
        "obs_id": obs_id,
        "sample_id": f"{source_id}:{arm}",
        "target": target,
        "arm": arm,
        "prompt_sha256": digest,
    }


def build(
    packet: _Document,
    board: _Document,
    board_digest: str,
    direct_receiver_report: _Document,
) -> _Document:
    peers = _direct_rows(packet, board, board_digest, direct_receiver_report)
    stock = inputs.source_index(packet)
    if stock.keys() != set(_SOURCE_IDS):
        raise ValueError("peer-update requires the 12 registered native sources")
    plans = {
        source_id: _source_plan(packet, board, stock[source_id], peers)
        for source_id in _SOURCE_IDS
    }
    rows = []
    for index in range(len(plans) * len(_ARMS)):
        source_id, arm = _schedule(index)
        source, digests = plans[source_id]
        rows.append(
            _row(
                index, source_id, source["obs_id"], source["target"], arm, digests[arm]
            )
        )
    return {
        "schema": SCHEMA,
        "board_sha256": board_digest,
        "direct_report_sha256": panel_digest(direct_receiver_report),
        "sources": [source for source, _ in plans.values()],
        "rows": rows,
    }


def _bound_row(
    packet: _Document, board: _Document, row: _Document
) -> tuple[_Document, _Document, dict[str, int]]:
    _object(row, *_ROW_FIELDS)
    index = row["index"]
    source_id, arm = _schedule(index)
    source = inputs.source_index(packet).get(source_id)
    if source is None:
        raise ValueError("invalid peer-update source")
    obs, names, target = _select(board, source)
    digest = row["prompt_sha256"]
    if row != _row(index, source_id, obs["obs_id"], target, arm, digest):
        raise ValueError("invalid peer-update row binding")
    _digest(digest)
    return source, obs, names


def prompt(
    packet: _Document, board: _Document, direct_report: _Document, row: _Document
) -> str:
    peers = _direct_rows(packet, board, panel_digest(board), direct_report)
    source, obs, names = _bound_row(packet, board, row)
    peer = peers[obs["obs_id"]]
    if peer["state"] != "complete":
        raise ValueError("peer draw 0 is unavailable")
    base = inputs.prompt(packet, obs)
    text = _render(base, source, names, row["target"], row["arm"], peer)
    if _text_digest(text) != row["prompt_sha256"]:
        raise ValueError("invalid peer-update prompt commitment")
    return text


def grade(
    packet: _Document, board: _Document, row: _Document, answer: str
) -> _Document:
    source, obs, names = _bound_row(packet, board, row)
    inputs.prompt(packet, obs)
    outcome = fork.grade(source, attention._open_pair(obs["opened"]), answer)
    actions = outcome["actions"]
    target = next(action for action in actions if action["id"] == names[row["target"]])
    return {"actions": actions, "target": target}
