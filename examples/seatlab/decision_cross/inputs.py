from hashlib import sha256
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..attention.protocol import _open_pair
from ..handoff.external_audit import _MAX_REPORT
from ..turn_fork import __main__ as turn_fork
from ..turn_fork import packet as packet_format
from ..turn_fork import protocol
from ..turn_fork.analysis import _source_index
from ..visibility.clarify_run import _encode

_SENDERS = ("direct", "commands")
_ARMS = ("aligned", "repeat")

type _Document = dict[str, Any]
type _Cell = tuple[str, str]


def source_index(packet: _Document) -> dict[str, _Document]:
    return _source_index(packet)


def _observation_id(source_id: str, digest: str) -> str:
    return f"{source_id}-{digest[:12]}"


def prompt(packet: _Document, obs: _Document) -> str:
    observation = packet_format._object(
        obs, "source_id", "obs_id", "prompt_sha256", "opened"
    )
    source_id = observation["source_id"]
    if not isinstance(source_id, str) or not isinstance(observation["opened"], list):
        raise ValueError("invalid decision-cross observation")
    source = source_index(packet).get(source_id)
    if source is None:
        raise ValueError("invalid decision-cross observation source")
    text = protocol.decide_prompt(source, _open_pair(observation["opened"]))
    digest = sha256(text.encode("utf-8")).hexdigest()
    if observation["prompt_sha256"] != digest or observation[
        "obs_id"
    ] != _observation_id(source_id, digest):
        raise ValueError("invalid decision-cross observation commitment")
    return text


def _verified_report(
    packet: _Document, packet_digest: str, path: Path, transport: str
) -> tuple[dict[_Cell, _Document], str]:
    try:
        raw = packet_format._read_bytes(path, _MAX_REPORT)
        digest = sha256(raw).hexdigest()
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "report.json"
            snapshot.write_bytes(raw)
            del raw
            report = turn_fork.verified(packet, packet_digest, snapshot)
        if report["transport"] != transport:
            raise ValueError
        rows = {
            (row["source_id"], row["arm"]): row
            for row in report["rows"]
            if row["arm"] in _ARMS and row["state"] == "complete"
        }
    except (OSError, ValueError, TypeError, LookupError, RecursionError, OverflowError):
        raise ValueError("invalid turn-fork report") from None
    return rows, digest


def _board_rows(
    sources: dict[str, _Document], reports: dict[str, dict[_Cell, _Document]]
) -> tuple[list[_Document], list[_Document]]:
    observations: dict[tuple[str, bytes], _Document] = {}
    committed_prompts: dict[str, bytes] = {}
    committed_ids: dict[str, str] = {}
    selections: list[_Document] = []
    for seed, repetition, sender, arm in product(
        packet_format.SEEDS, packet_format.REPETITIONS, _SENDERS, _ARMS
    ):
        source_id = f"s{seed}-r{repetition}"
        selection: _Document = {
            "source_id": source_id,
            "sender": sender,
            "arm": arm,
            "obs_id": None,
            "sender_response_sha256": None,
        }
        selections.append(selection)
        row = reports[sender].get((source_id, arm))
        if row is None:
            continue
        opened = row["outcome"]["opened"]
        text = protocol.decide_prompt(sources[source_id], _open_pair(opened))
        raw = text.encode("utf-8")
        digest = sha256(raw).hexdigest()
        if row["turns"][1]["prompt_sha256"] != digest:
            raise ValueError("invalid turn-fork report")
        obs_id = _observation_id(source_id, digest)
        if committed_prompts.setdefault(digest, raw) != raw:
            raise ValueError("final prompt digest collision")
        if committed_ids.setdefault(obs_id, digest) != digest:
            raise ValueError("observation ID collision")
        key = (source_id, raw)
        observation = observations.get(key)
        if observation is None:
            observations[key] = {
                "source_id": source_id,
                "obs_id": obs_id,
                "prompt_sha256": digest,
                "opened": opened,
            }
        elif opened < observation["opened"]:
            observation["opened"] = opened
        selection["obs_id"] = obs_id
        selection["sender_response_sha256"] = row["turns"][0]["response_sha256"]
    ordered = sorted(
        observations.values(), key=lambda obs: (obs["source_id"], obs["prompt_sha256"])
    )
    return ordered, selections


def build(
    packet_path: Path, packet_digest: str, direct_report: Path, commands_report: Path
) -> tuple[_Document, _Document, str]:
    packet = packet_format.load(packet_path, packet_digest)
    sources = source_index(packet)
    direct, direct_digest = _verified_report(
        packet, packet_digest, direct_report, "direct"
    )
    commands, commands_digest = _verified_report(
        packet, packet_digest, commands_report, "commands"
    )
    observations, selections = _board_rows(
        sources, {"direct": direct, "commands": commands}
    )
    board = {
        "schema": "seatlab.decision-cross-board/v1",
        "packet_sha256": packet_digest,
        "direct_report_sha256": direct_digest,
        "commands_report_sha256": commands_digest,
        "observations": observations,
        "selections": selections,
    }
    return packet, board, sha256(_encode(board)).hexdigest()


def draws(board: _Document) -> list[_Document]:
    rows: list[_Document] = []
    for draw in (0, 1):
        ordered = sorted(
            board["observations"],
            key=lambda obs, draw=draw: sha256(
                f"cross-order-v1/{draw}/{obs['obs_id']}".encode("utf-8")
            ).hexdigest(),
        )
        for obs in ordered:
            rows.append(
                {
                    "index": len(rows),
                    "source_id": obs["source_id"],
                    "obs_id": obs["obs_id"],
                    "prompt_sha256": obs["prompt_sha256"],
                    "draw": draw,
                }
            )
    return rows


def run_id(digest: str, obs_id: str, draw: int) -> str:
    return f"dx-{digest[:12]}-{obs_id}-r{draw}"
