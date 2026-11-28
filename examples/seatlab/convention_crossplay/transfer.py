import json
import tempfile
from pathlib import Path
from typing import Any

from ...seat_checkpoints.materialize import sync_directory
from ...ship_inflight.materialize import publish
from ..coupled_retention import strategy
from ..decision_boundary.inputs import read_input
from ..exhibit_enroll import target, write_private
from . import loader
from .records import Game, Team, team_name

ROLES = ("east", "west")


def inputs(teams: list[list[str]]) -> list[tuple[str, bytes, bytes]]:
    if not 2 <= len(teams) <= 12:
        raise ValueError("the roster needs 2 to 12 teams")

    def read_role(path: str) -> bytes:
        if path == "-":
            raise ValueError("expected a role table file")
        raw = read_input(path)
        if len(raw) > strategy.MAX_TABLE_BYTES:
            raise ValueError("role table is too large")
        return raw

    return [
        (team_name(name), read_role(east), read_role(west))
        for name, east, west in teams
    ]


def native_inputs(game: Game, teams: list[Team]) -> list[tuple[str, bytes, bytes]]:
    packets = ({c.east for c in game[2]}, {c.west for c in game[2]})
    result = []
    for team in teams:
        east = strategy.table(
            "east", {p: team.east[strategy.digest(p)] for p in packets[0]}
        )
        west = strategy.table(
            "west", {p: team.west[strategy.digest(p)] for p in packets[1]}
        )
        result.append((team.name, east, west))
    return result


def _stage(root: Path, teams: list[tuple[str, bytes, bytes]]) -> bytes:
    entries: list[dict[str, Any]] = []
    for i, (name, east, west) in enumerate(teams):
        entry: dict[str, Any] = {"name": team_name(name)}
        for role, raw in zip(ROLES, (east, west), strict=True):
            if len(raw) > strategy.MAX_TABLE_BYTES:
                raise ValueError("role table is too large")
            name_on_disk = f"t{i:02}-{role}.json"
            write_private(root / name_on_disk, raw)
            entry[role] = {"file": name_on_disk, "sha256": loader.digest(raw)}
        entries.append(entry)
    return (
        json.dumps(
            {"schema": loader.SCHEMA, "teams": entries},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def export(out: Path, teams: list[tuple[str, bytes, bytes]]) -> dict[str, str | int]:
    if not 2 <= len(teams) <= 12:
        raise ValueError("the roster needs 2 to 12 teams")
    out = target(out)
    with tempfile.TemporaryDirectory(
        prefix=".seatlab-crossplay-", dir=out.parent
    ) as stage:
        root = Path(stage) / "teams"
        root.mkdir(mode=0o700)
        manifest = _stage(root, teams)
        write_private(root / "roster.json", manifest)
        enrollees = loader.load(str(root / "roster.json"), loader.digest(manifest))
        seen = {
            (frozenset(t.east.items()), frozenset(t.west.items())) for t in enrollees
        }
        if len(seen) != len(enrollees):
            raise ValueError("multiple names for one convention")
        sync_directory(root)
        publish(root, out)
        sync_directory(out.parent)
    return {"teams": len(teams), "roster_sha256": loader.digest(manifest)}
