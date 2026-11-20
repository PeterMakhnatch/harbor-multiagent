"""Export bound first ballots and render blinded second ballots offline."""

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path

from . import analysis, contract
from .ballot_schedule import ARMS
from .schedule import ROLES, TASK_TREE, grid

_SCHEMA = "seatlab.committee-ballots/v1"
_SOURCE = tuple(
    cell for cell in grid() if cell["repetition"] == 0 and cell["arm"] == "novelty"
)
_SEATS = tuple(f"S{i}" for i in range(1, len(ROLES) + 1))
_PACKET_FIELDS = {"schema", "task_git_tree", "teams"}
_TEAM_FIELDS = {"cell_id", "map_index", "seats"}
_SEAT_FIELDS = {
    "seat",
    "share",
    "first",
    "source_prompt_sha256",
    "source_action_sha256",
}
_TURN_FIELDS = {
    "status",
    "requested_model",
    "served_model",
    "prompt_sha256",
    "response_sha256",
    "action_sha256",
}
_MAX_PACKET_BYTES = 16 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024


class InvalidBallot(ValueError):
    """The model response is not a valid blinded ballot for this seat."""


def _dossier(role: str) -> dict:
    # Native evidence binds the IDs. The codec projects the stock numeric facts,
    # not the original dossier prose or the world's reward.
    def fact(fact_id: str, kind: str) -> dict:
        option, points = analysis._FACTS[fact_id]
        return {
            "id": fact_id,
            "kind": kind,
            "option": option,
            "points": points,
            "text": "",
        }

    return {
        "seat": role,
        "options": list(analysis._OPTIONS),
        "rubric": "Choose the option with the highest net score.",
        "shared_facts": [fact(fid, "shared") for fid in analysis._SHARED],
        "private_facts": [fact(analysis._OWN[role], "private")],
    }


def _chat(team: dict) -> dict:
    _, aliases = contract._mapping(team["map_index"])
    ids = {alias: fid for fid, alias in aliases.items()}
    messages = []
    for seq, (role, seat) in enumerate(zip(ROLES, team["seats"], strict=True), 1):
        share = seat["share"]
        fid = ids.get(share) if isinstance(share, str) else None
        if fid is None or (fid not in analysis._SHARED and fid != analysis._OWN[role]):
            raise ValueError("invalid source share")
        option, points = analysis._FACTS[fid]
        # The original codec sorts submissions by fact alias and omits seq.
        text = analysis._canonical({"id": fid, "option": option, "points": points})
        messages.append({"seat": role, "text": text, "seq": seq})
    return {"messages": messages, "unique_speakers": list(ROLES)}


def _blinded(action: dict, index: int) -> dict:
    options, aliases = contract._mapping(index)
    return {
        "choice": options[action["choice"]],
        "cited": [aliases[fid] for fid in action["cited_facts"]],
    }


def _verify_team(value: object, expected: dict) -> None:
    team = contract._object(value, _TEAM_FIELDS, "source team")
    index, seats = team["map_index"], team["seats"]
    if (
        team["cell_id"] != expected["cell_id"]
        or type(index) is not int
        or index != expected["map_index"]
        or not isinstance(seats, list)
        or len(seats) != len(_SEATS)
    ):
        raise ValueError("invalid source team order or roster")
    for seat, label in zip(seats, _SEATS, strict=True):
        if contract._object(seat, _SEAT_FIELDS, "source seat")["seat"] != label:
            raise ValueError("invalid source seat order")

    chat = _chat(team)
    for role, seat in zip(ROLES, seats, strict=True):
        dossier = _dossier(role)
        source_prompt = contract.vote_prompt(dossier, chat, index)
        if seat["source_prompt_sha256"] != analysis._sha(source_prompt):
            raise ValueError("source vote prompt hash mismatch")
        actual = contract.vote_action(
            analysis._canonical(seat["first"]), dossier, chat, index
        )
        if seat["source_action_sha256"] != analysis._sha(analysis._canonical(actual)):
            raise ValueError("source vote action hash mismatch")


def _export_team(root: Path, scored: dict) -> dict:
    cell_id, index = scored["cell_id"], scored["map_index"]
    trial = analysis._path(
        root, f"cells/{cell_id}/trials/committee-{cell_id}", directory=True
    )
    actions = analysis._native_actions(trial)
    reason = "invalid native result"
    result = analysis._object(analysis._json(trial, "result.json", reason), reason)
    natives = analysis._roles(result.get("seat_results"), reason)
    _, aliases = contract._mapping(index)
    seats = []
    for role, reported in zip(ROLES, scored["seats"], strict=True):
        action = actions[role]["vote"]
        share = aliases[actions[role]["fact_id"]]
        first = _blinded(action, index)
        if share != reported["share"]["fact"] or first != reported["vote"]:
            raise ValueError("source projection mismatch")
        native = contract._object(
            natives[role]["agent_result"]["metadata"]["turns"]["vote"],
            _TURN_FIELDS,
            "native vote turn",
        )
        receipt = analysis._json(
            trial, f"seats/{role}/agent/committee-turns.json", "invalid vote sidecar"
        )["turns"]["vote"]
        # Both the context and sidecar must commit to the same accepted action.
        # The public verifier then recomputes both commitments from the packet.
        if receipt != (native | {"action": action}):
            raise ValueError("source vote sidecar mismatch")
        seats.append(
            {
                "seat": reported["seat"],
                "share": share,
                "first": first,
                "source_prompt_sha256": native["prompt_sha256"],
                "source_action_sha256": native["action_sha256"],
            }
        )
    return {"cell_id": cell_id, "map_index": index, "seats": seats}


def export_packet(root: Path, *, spend_dir: Path) -> dict:
    """Project the 6 preregistered source teams from their private archives."""
    try:
        info = root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("private original study directory required")
        report = analysis.analyze(root, spend_dir=spend_dir)
        if report["spend_evidence"] != "available":
            raise ValueError("private original spend evidence required")
        scored = {cell["cell_id"]: cell for cell in report["cells"]}
        excluded = {
            cell["cell_id"]: cell["reason"] for cell in report["excluded_cells"]
        }
        teams = []
        for expected in _SOURCE:
            cell_id = expected["cell_id"]
            if cell_id not in scored:
                reason = excluded[cell_id]
                raise ValueError(f"source team {cell_id} unavailable: {reason}")
            team = _export_team(root, scored[cell_id])
            _verify_team(team, expected)
            teams.append(team)
    except analysis._Excluded as exc:
        raise ValueError(exc.reason) from None
    except (KeyError, OSError, TypeError, RecursionError):
        raise ValueError("invalid original source evidence") from None
    return {"schema": _SCHEMA, "task_git_tree": TASK_TREE, "teams": teams}


def read_packet(path: Path, expected_sha256: str) -> dict:
    """Verify a trusted raw-byte commitment and the source ballot semantics."""
    if not isinstance(expected_sha256, str) or not analysis._DIGEST.fullmatch(
        expected_sha256
    ):
        raise ValueError("invalid expected packet SHA-256")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("invalid packet path")
            raw = stream.read(_MAX_PACKET_BYTES + 1)
    except OSError:
        raise ValueError("packet unreadable") from None
    if len(raw) > _MAX_PACKET_BYTES:
        raise ValueError("packet exceeds size limit")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("packet SHA-256 mismatch")
    packet = contract._read_json(raw.decode("utf-8"), _PACKET_FIELDS, "ballot packet")
    teams = packet["teams"]
    if (
        packet["schema"] != _SCHEMA
        or packet["task_git_tree"] != TASK_TREE
        or not isinstance(teams, list)
        or len(teams) != len(_SOURCE)
    ):
        raise ValueError("invalid ballot packet version, task, or team count")
    for team, expected in zip(teams, _SOURCE, strict=True):
        _verify_team(team, expected)
    return packet


def _team(packet: dict, map_index: int, seat_index: int) -> dict:
    contract._mapping(map_index)
    if type(seat_index) is not int or not 0 <= seat_index < len(ROLES):
        raise ValueError("seat index must be an integer from 0 through 3")
    team = packet["teams"][map_index]
    if team["map_index"] != map_index:
        raise ValueError("invalid packet map order")
    return team


def prompt(packet: dict, map_index: int, seat_index: int, arm: str) -> str:
    """Render a seat's original evidence and assigned peers from a verified packet."""
    if arm not in ARMS:
        raise ValueError("invalid peer ballot arm")
    team = _team(packet, map_index, seat_index)
    seats = team["seats"]
    original = contract.vote_prompt(_dossier(ROLES[seat_index]), _chat(team), map_index)
    evidence, instruction, response_format = original.rsplit("\n", 2)
    peers = [
        {
            "seat": seat["seat"],
            "choice": None if arm == "private" else seat["first"]["choice"],
            "cited": seat["first"]["cited"] if arm == "cited" else None,
        }
        for i, seat in enumerate(seats)
        if i != seat_index
    ]
    return "\n".join(
        (
            evidence,
            "Your first ballot: " + contract._compact_json(seats[seat_index]["first"]),
            "Peer ballots: " + contract._compact_json(peers),
            "Make a second decision. Peer ballots do not add facts to your evidence.",
            instruction,
            response_format,
        )
    )


def decode(packet: dict, map_index: int, seat_index: int, response_text: str) -> dict:
    """Decode a second ballot against that seat's evidence in a verified packet."""
    team = _team(packet, map_index, seat_index)
    dossier, chat = _dossier(ROLES[seat_index]), _chat(team)
    try:
        if (
            not isinstance(response_text, str)
            or len(response_text) > _MAX_RESPONSE_BYTES
            or len(response_text.encode("utf-8")) > _MAX_RESPONSE_BYTES
        ):
            raise ValueError("invalid response size or type")
        actual = contract.vote_action(response_text, dossier, chat, map_index)
    except ValueError:
        raise InvalidBallot("invalid second ballot") from None
    return _blinded(actual, map_index)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("study", type=Path)
    export.add_argument("--spend-dir", type=Path, required=True)
    for name in ("verify", "prompt", "decode"):
        command = commands.add_parser(name)
        command.add_argument("packet", type=Path)
        command.add_argument("--expect-sha256", required=True)
        if name != "verify":
            command.add_argument(
                "--map", type=int, choices=range(len(_SOURCE)), required=True
            )
            command.add_argument(
                "--seat", type=int, choices=range(1, len(ROLES) + 1), required=True
            )
        if name == "prompt":
            command.add_argument("--arm", choices=ARMS, required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            packet = export_packet(args.study, spend_dir=args.spend_dir)
            print(analysis._canonical(packet))
            return
        packet = read_packet(args.packet, args.expect_sha256)
        if args.command == "prompt":
            print(prompt(packet, args.map, args.seat - 1, args.arm))
        elif args.command == "decode":
            raw = sys.stdin.buffer.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise InvalidBallot("invalid second ballot")
            ballot = decode(packet, args.map, args.seat - 1, raw.decode("utf-8"))
            print(analysis._canonical(ballot))
    except UnicodeError:
        parser.error("invalid UTF-8 input")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
