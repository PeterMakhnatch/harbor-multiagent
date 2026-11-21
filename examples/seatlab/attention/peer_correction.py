import json
import os
import stat
import sys
from dataclasses import dataclass
from typing import Any

from . import peer_order, protocol

_MAX_REPLY_BYTES = 32 * 1024
_RULES = (
    "For each marked shipment, choose the available bay with the highest utility. "
    "Higher numbers mean greater utility. You may retain or change the original "
    "proposed bay. If revision 2 is not shown, revision 1 can be stale. "
    'Reply with only JSON in the form {"choices":{...}}. '
    "Include exactly the marked aliases, each once. Choose a bay A, B, C, or D "
    "for each alias."
)


@dataclass(frozen=True, slots=True)
class _Prepared:
    marked: tuple[str, ...]
    free: dict[str, str]
    prompt: str | None


def _choices(text: str, aliases: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(text, str) or len(text) > _MAX_REPLY_BYTES:
        raise ValueError("Expected a bounded text reply")
    choices = protocol._reply(text, "choices")
    if not isinstance(choices, dict) or choices.keys() != set(aliases):
        raise ValueError("Expected exactly the required choice aliases")
    if any(
        not isinstance(bay, str) or bay not in protocol._BAYS
        for bay in choices.values()
    ):
        raise ValueError("Expected a bay A, B, C, or D")
    return {alias: choices[alias] for alias in aliases}


def _latest(row: dict[str, Any]) -> list[str]:
    return row.get("revision_2", row["revision_1"])


def _disputes(shown: str, original: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    peer_order.variant(shown, "report")
    rows = protocol._reply(shown.partition("\n")[2], "cases")
    choices = _choices(original, protocol._ALIASES)
    return choices, [row for row in rows if choices[row["alias"]] not in _latest(row)]


def prepare(shown: str, original: str) -> _Prepared:
    free, disputes = _disputes(shown, original)
    cases = []
    for row in disputes:
        alias = row["alias"]
        cases.append({**row, "proposed_bay": free[alias]})
        free[alias] = max(_latest(row), key=row["utilities"].__getitem__)
    marked = tuple(row["alias"] for row in disputes)
    prompt = protocol._prompt(_RULES, {"cases": cases}) if cases else None
    return _Prepared(marked, free, prompt)


def merge(shown: str, original: str, patch: str) -> dict[str, str]:
    choices, disputes = _disputes(shown, original)
    marked = tuple(row["alias"] for row in disputes)
    choices.update(_choices(patch, marked))
    return choices


def _read(path: str, limit: int) -> str:
    with open(
        path,
        "rb",
        opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK | os.O_NOFOLLOW),
    ) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Expected a bounded regular file")
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input file is too large")
    return data.decode("utf-8")


def main() -> int:
    args = sys.argv[1:]
    if not (
        (len(args) == 3 and args[0] in ("request", "free"))
        or (len(args) == 4 and args[0] == "merge")
    ):
        print(
            "usage: python -BS -m examples.seatlab.attention.peer_correction "
            "{request,free,merge} SHOWN ORIGINAL [PATCH]",
            file=sys.stderr,
        )
        return 2
    try:
        shown = _read(args[1], peer_order._MAX_PROMPT_BYTES)
        original = _read(args[2], _MAX_REPLY_BYTES)
        if args[0] == "merge":
            choices = merge(shown, original, _read(args[3], _MAX_REPLY_BYTES))
        else:
            prepared = prepare(shown, original)
            if args[0] == "request":
                if prepared.prompt is not None:
                    sys.stdout.write(prepared.prompt)
                return 0
            choices = prepared.free
        print(json.dumps({"choices": choices}, separators=(",", ":"), allow_nan=False))
    except (OSError, ValueError):
        print("peer_correction: cannot read or validate the input", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
