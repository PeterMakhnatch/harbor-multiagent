import base64
import binascii
import json
import os
import stat
import sys
from typing import Any

from .core import BoundaryError, World

LIMIT = 16 * 1024 * 1024
SCHEMA = "seatlab.decision-worlds/v1"


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row = dict(pairs)
    if len(row) != len(pairs):
        raise BoundaryError("duplicate JSON member")
    return row


def census(data: bytes) -> tuple[int, list[World]]:
    if not 0 < len(data) <= LIMIT:
        raise BoundaryError("input outside the 16 MiB limit")
    root = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(root) is not dict
        or root.keys() != {"schema", "scale", "worlds"}
        or root["schema"] != SCHEMA
    ):
        raise BoundaryError("invalid finite world schema")
    rows = root["worlds"]
    if type(rows) is not list or not 0 < len(rows) <= 1024:
        raise BoundaryError("invalid finite world roster")
    worlds = []
    for row in rows:
        if type(row) is not dict or row.keys() != {
            "name",
            "mass",
            "scores",
            "observation_b64",
        }:
            raise BoundaryError("missing or extra finite world fields")
        encoded = row["observation_b64"]
        if type(encoded) is not str or len(encoded) > 4 * ((1024 * 1024 + 2) // 3):
            raise BoundaryError("invalid base64 observation")
        try:
            observation = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise BoundaryError("invalid base64 observation") from None
        if base64.b64encode(observation).decode("ascii") != encoded:
            raise BoundaryError("noncanonical base64 observation")
        worlds.append(World(row["name"], row["mass"], observation, row["scores"]))
    return root["scale"], worlds


def read_input(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read(LIMIT + 1)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= LIMIT:
            raise BoundaryError("expected a bounded regular file")
        data = handle.read(LIMIT + 1)
        if len(data) != info.st_size:
            raise BoundaryError("input size changed")
        return data
