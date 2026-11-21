from typing import Any

from . import protocol

PLAN_SCHEMA = "seatlab.native-evidence-join-plan/v1"
REPORT_SCHEMA = "seatlab.native-evidence-join-report/v1"
RUN_PREFIX = "dvj-"
AGENT_IMPORT = "examples.seatlab.attention.join_agent:JoinSeat"

_ARMS = ("nested", "joined")
_BAYS = protocol._BAYS
_BAY_KEYS = frozenset(_BAYS)
_NESTED_RULES = protocol._DECIDE_RULES
_JOINED_RULES = (
    _NESTED_RULES + "\n"
    "Each bay array contains [utility, revision_1 position], with a third "
    "revision_2 position only when that report is shown for the case. Positions "
    "start at 1 and record report order, not preference. null means the bay is "
    "absent from that report."
)
_MAX_PROMPT_CHARS = 4096


def grid() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "cell_id": f"{RUN_PREFIX}s{seed}-r0-{arm}",
            "seed": seed,
            "repetition": 0,
            "condition": "reordered",
            "pairing": "MM",
            "arm": arm,
        }
        for index, seed in enumerate(range(14521, 14529))
        for arm in (_ARMS if index % 2 == 0 else _ARMS[::-1])
    )


def _bounded(prompt: str) -> str:
    if not isinstance(prompt, str) or len(prompt) > _MAX_PROMPT_CHARS:
        raise ValueError("Expected a bounded dispatch prompt")
    return prompt


def _cases(prompt: str, rules: str) -> list[dict[str, Any]]:
    prefix = rules + "\n"
    if not _bounded(prompt).startswith(prefix):
        raise ValueError("Unexpected dispatch prompt rules")
    rows = protocol._reply(prompt[len(prefix) :], "cases")
    if not isinstance(rows, list) or len(rows) != len(protocol._ALIASES):
        raise ValueError("Expected all dispatch cases")
    for row, alias in zip(rows, protocol._ALIASES, strict=True):
        if not isinstance(row, dict) or row.get("alias") != alias:
            raise ValueError("Expected dispatch aliases in protocol order")
    return rows


def _render(rules: str, rows: list[dict[str, Any]]) -> str:
    return _bounded(protocol._prompt(rules, {"cases": rows}))


def _require_two_reads(rows: list[dict[str, Any]]) -> None:
    if sum("revision_2" in row for row in rows) != 2:
        raise ValueError("Expected exactly two shown revision 2 reports")


def _positions(report: object) -> dict[str, int]:
    if not isinstance(report, list) or len(report) > len(_BAYS):
        raise ValueError("Expected a bounded bay report")
    positions: dict[str, int] = {}
    for position, bay in enumerate(report, start=1):
        if not isinstance(bay, str) or bay not in _BAY_KEYS or bay in positions:
            raise ValueError("Expected distinct registered bays")
        positions[bay] = position
    return positions


def _nested_positions(row: dict[str, Any]) -> tuple[dict[str, int], ...]:
    required = {"alias", "utilities", "revision_1"}
    if row.keys() not in (required, required | {"revision_2"}):
        raise ValueError("Unexpected nested dispatch case fields")
    utilities = row["utilities"]
    if (
        not isinstance(utilities, dict)
        or utilities.keys() != _BAY_KEYS
        or any(type(utility) is not int for utility in utilities.values())
    ):
        raise ValueError("Expected one integer utility per bay")
    positions = (_positions(row["revision_1"]),)
    if "revision_2" in row:
        positions += (_positions(row["revision_2"]),)
    return positions


def project(prompt: str, arm: str) -> str:
    if arm not in _ARMS:
        raise ValueError("Unknown evidence-join arm")
    rows = _cases(prompt, _NESTED_RULES)
    _require_two_reads(rows)
    joined = []
    for row in rows:
        positions = _nested_positions(row)
        if arm == "joined":
            joined.append(
                {
                    "alias": row["alias"],
                    "bays": {
                        bay: [
                            row["utilities"][bay],
                            *(report.get(bay) for report in positions),
                        ]
                        for bay in _BAYS
                    },
                }
            )
    if prompt != _render(_NESTED_RULES, rows):
        raise ValueError("Expected canonical nested dispatch prompt")
    return prompt if arm == "nested" else _render(_JOINED_RULES, joined)


def _report(slots: list[list[Any]], column: int) -> list[str]:
    by_position = {}
    for bay, slot in zip(_BAYS, slots, strict=True):
        position = slot[column]
        if position is None:
            continue
        if (
            type(position) is not int
            or not 1 <= position <= len(_BAYS)
            or position in by_position
        ):
            raise ValueError("Invalid joined report position")
        by_position[position] = bay
    positions = range(1, len(by_position) + 1)
    if by_position.keys() != set(positions):
        raise ValueError("Joined report positions must be consecutive")
    return [by_position[position] for position in positions]


def _unjoin_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.keys() != {"alias", "bays"}:
        raise ValueError("Unexpected joined dispatch case fields")
    bays = row["bays"]
    if not isinstance(bays, dict) or bays.keys() != _BAY_KEYS:
        raise ValueError("Expected one joined entry per bay")
    slots = [bays[bay] for bay in _BAYS]
    if any(not isinstance(slot, list) or len(slot) not in (2, 3) for slot in slots):
        raise ValueError("Invalid joined bay array")
    columns = len(slots[0])
    if any(len(slot) != columns or type(slot[0]) is not int for slot in slots):
        raise ValueError("Expected uniform joined bay arrays with integer utilities")
    nested = {
        "alias": row["alias"],
        "utilities": dict(zip(_BAYS, (slot[0] for slot in slots), strict=True)),
        "revision_1": _report(slots, 1),
    }
    if columns == 3:
        nested["revision_2"] = _report(slots, 2)
    return nested


def unjoin(prompt: str) -> str:
    joined = _cases(prompt, _JOINED_RULES)
    nested = [_unjoin_row(row) for row in joined]
    _require_two_reads(nested)
    if prompt != _render(_JOINED_RULES, joined):
        raise ValueError("Expected canonical joined dispatch prompt")
    return _render(_NESTED_RULES, nested)
