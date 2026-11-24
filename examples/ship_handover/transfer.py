import argparse
import json
import os
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from ..native_evidence import Invalid, Record, require
from .study import CHANNELS, FATES, INFLIGHT, PRIORS, STOCK, Spec
from .transfer_contract import (
    GRADE_OF,
    GRADED,
    NATIVE_AGREED,
    OPTIONALLY_GRADED,
    PACKET_SCHEMA,
    PACKET_STATUSES,
    bindings,
    check_packet,
    load_pair,
    read_packet,
    registration_digest,
    team_label,
)

SCHEMA = "harbor.ship-handover.transfer/v1"
MAX_PACKETS = 64
MAX_DIAGNOSTIC = 512
DELIVER, WITHHOLD = CHANNELS
STATES = ("original_succeeds", "original_fails")
COMPARISONS = ("smaller", "equal", "larger", "unresolved")
assert CHANNELS == ("deliver", "withhold")
assert PRIORS == ("committed", "dropped") and FATES == ("settles", "fails")
assert GRADED == ("zero", "positive") and set(GRADE_OF) == set(GRADED)

Interval = tuple[Fraction, Fraction]
Graded = tuple[str, int | None]


class World(NamedTuple):
    name: str
    schema: str
    axis: str
    states: tuple[tuple[str, str], ...]
    statuses: tuple[str, ...]

    def original(self, state: object) -> str:
        found = [original for own, original in self.states if own == state]
        require(len(found) == 1, f"{self.name} cell {self.axis} is not registered")
        return found[0]

    def own(self, original: str) -> str:
        found = [own for own, mapped in self.states if mapped == original]
        assert len(found) == 1
        return found[0]

    @property
    def other(self) -> tuple[str, ...]:
        return tuple(status for status in self.statuses if status not in GRADED)


def _world(spec: Spec, states: tuple[str, ...]) -> World:
    assert spec is STOCK or spec is INFLIGHT
    statuses = tuple(PACKET_STATUSES[spec.world])
    assert set(GRADED) | set(OPTIONALLY_GRADED) <= set(statuses)
    return World(
        spec.world, spec.schema, spec.axis, tuple(zip(states, STATES)), statuses
    )


STOCK_WORLD = _world(STOCK, PRIORS)
INFLIGHT_WORLD = _world(INFLIGHT, FATES)
WORLDS = (STOCK_WORLD, INFLIGHT_WORLD)
assert (
    STOCK_WORLD.own(STATES[0]) == "committed"
    and INFLIGHT_WORLD.own(STATES[1]) == "fails"
)
assert (
    len({world.name for world in WORLDS}) == len({world.axis for world in WORLDS}) == 2
)

QUESTION = (
    "For the same named native team with an identical roster, how does the"
    " deliver-minus-withhold good-outcome rate differ between the stock task"
    " (prior committed or dropped) and the inflight task (fate settles or"
    " fails), within each original-shipment state?"
)
NOTES = {
    "states": (
        "original_succeeds pairs stock committed with inflight settles;"
        " original_fails pairs stock dropped with inflight fails"
    ),
    "reported": (
        "reported counts packet rows received for registered cells; receipt of a"
        " row is not a witnessed seat return"
    ),
    "eligible": (
        "only reported rows with status positive or zero (world grade 1 or 0,"
        f" grade source {NATIVE_AGREED}) count as known outcomes: both native"
        " seats returned with no error and the grade was verified"
    ),
    "unknown": (
        "every other registered cell is unknown, never a failure: missing cells"
        " (no reported row, no_result) and every declared other status; the"
        " status duplicate is a declared collection status, distinct from a"
        " repeated ticket claim across packets, which is rejected"
    ),
    "physical": (
        "physical_known and physical_positive count unresolved or native_error"
        " rows that still carry a world grade; they are reported per world and"
        " never enter a success interval"
    ),
    "success": (
        "[k/n,(k+u)/n] over the registered n of one world, state and channel,"
        " with k positive and u unknown; an exact identification bound, not a"
        " sampling confidence interval"
    ),
    "delta": "deliver success interval minus withhold success interval, sharp bounds",
    "transfer": "inflight delta minus stock delta within one original state, sharp bounds",
    "comparison": (
        "larger when the whole transfer interval is > 0, smaller when it is < 0,"
        " equal when it is exactly [0/1,0/1], unresolved otherwise"
    ),
    "fractions": "every bound is an exact reduced fraction written numerator/denominator",
    "pooling": (
        "no success rate is pooled across states, channels or worlds; world"
        " totals are accounting counts only"
    ),
    "scope": (
        "registrations and packets are stationary operator inputs read once;"
        " digests name content only and are not credentials, signatures or proof"
        " of a model response; the report describes the registered team under"
        " the registered worlds only, with no causal or model attribution,"
        " p-value or cost claim"
    ),
}


class Stratum(NamedTuple):
    record: Record
    success: Interval


def _fraction(value: Fraction) -> str:
    assert -2 <= value <= 2
    return f"{value.numerator}/{value.denominator}"


def _interval(interval: Interval) -> Record:
    low, high = interval
    assert low <= high
    return {"low": _fraction(low), "high": _fraction(high)}


def _text(interval: Record) -> str:
    return "[{low},{high}]".format(**interval)


def _minus(left: Interval, right: Interval) -> Interval:
    found = (left[0] - right[1], left[1] - right[0])
    assert found[0] <= found[1]
    return found


def _compare(interval: Interval) -> str:
    low, high = interval
    if low > 0:
        return "larger"
    if high < 0:
        return "smaller"
    if low == high == 0:
        return "equal"
    return "unresolved"


def _reason(error: BaseException) -> str:
    if isinstance(error, OSError):
        return error.strerror or "storage failed"
    return str(error)


def _graded(world: World, row: Record) -> Graded:
    status, grade = row["status"], row["world_grade"]
    assert status in world.statuses
    if status in GRADED:
        assert grade == GRADE_OF[status] and row["grade_source"] == NATIVE_AGREED
        return str(status), None
    if status in OPTIONALLY_GRADED and grade is not None:
        assert type(grade) is int and grade in (0, 1)
        return str(status), grade
    assert grade is None
    return str(status), None


def _claims(
    world: World, reg: Record, paths: list[Path]
) -> tuple[list[str], dict[str, Graded]]:
    require(len(paths) <= MAX_PACKETS, f"more than {MAX_PACKETS} {world.name} packets")
    digests: list[str] = []
    claims: dict[str, Graded] = {}
    for index, path in enumerate(paths, 1):
        try:
            packet = read_packet(path)
            rows = check_packet(reg, packet)
            for row in rows:
                ticket = row["ticket"]
                require(
                    ticket not in claims, "a ticket is claimed by an earlier packet"
                )
                claims[ticket] = _graded(world, row)
        except (OSError, ValueError, Invalid) as error:
            raise Invalid(f"{world.name} packet {index}: {_reason(error)}") from None
        digests.append(registration_digest(packet))
    return sorted(digests), claims


def _stratum(
    world: World, key: tuple[str, str], n: int, found: list[Graded]
) -> Stratum:
    original, channel = key
    require(n > 0, f"{world.name} registration leaves a stratum empty")
    statuses = Counter(status for status, _ in found)
    physical = [grade for _, grade in found if grade is not None]
    positive = statuses["positive"]
    eligible = positive + statuses["zero"]
    unknown = n - eligible
    assert 0 <= len(found) <= n and 0 <= unknown <= n
    success = (Fraction(positive, n), Fraction(positive + unknown, n))
    record: Record = {
        "state": original,
        world.axis: world.own(original),
        "channel": channel,
        "registered": n,
        "reported": len(found),
        "missing": n - len(found),
        "eligible": eligible,
        "positive": positive,
        "zero": statuses["zero"],
        "unknown": unknown,
        "statuses": {status: statuses[status] for status in world.other},
        "physical_known": len(physical),
        "physical_positive": sum(physical),
        "success": _interval(success),
    }
    assert sum(record["statuses"].values()) + eligible == len(found)
    return Stratum(record, success)


def _side(
    world: World, bound: dict[str, Record], claims: dict[str, Graded]
) -> tuple[Record, dict[str, Interval]]:
    keys = [(state, channel) for state in STATES for channel in CHANNELS]
    registered: dict[tuple[str, str], int] = {key: 0 for key in keys}
    found: dict[tuple[str, str], list[Graded]] = {key: [] for key in keys}
    for ticket, cell in bound.items():
        assert cell["world"] == world.name and cell["channel"] in CHANNELS
        key = (world.original(cell["state"]), cell["channel"])
        registered[key] += 1
        if ticket in claims:
            found[key].append(claims[ticket])
    assert set(claims) <= set(bound)
    strata = {key: _stratum(world, key, registered[key], found[key]) for key in keys}
    deltas = {
        state: _minus(
            strata[(state, DELIVER)].success, strata[(state, WITHHOLD)].success
        )
        for state in STATES
    }
    records = [strata[key].record for key in keys]
    totals: Record = {
        field: sum(record[field] for record in records)
        for field in ("registered", "reported", "missing", "eligible", "unknown")
    }
    totals["statuses"] = {
        status: sum(record["statuses"][status] for record in records)
        for status in world.other
    }
    totals["physical_known"] = sum(record["physical_known"] for record in records)
    assert totals["registered"] == len(bound) and totals["reported"] == len(claims)
    side = {
        "totals": totals,
        "strata": records,
        "delta": {state: _interval(deltas[state]) for state in STATES},
    }
    return side, deltas


def transfer(
    stock_path: Path, inflight_path: Path, packets: dict[str, list[Path]]
) -> Record:
    stock, inflight = load_pair(stock_path, inflight_path)
    regs = {STOCK_WORLD.name: stock, INFLIGHT_WORLD.name: inflight}
    label = team_label(stock)
    assert label == team_label(inflight)
    worlds: Record = {}
    deltas: dict[str, dict[str, Interval]] = {}
    for world in WORLDS:
        reg = regs[world.name]
        digests, claims = _claims(world, reg, packets[world.name])
        side, deltas[world.name] = _side(world, bindings(reg), claims)
        worlds[world.name] = {
            "schema": world.schema,
            "registration_sha256": registration_digest(reg),
            "packet_schema": PACKET_SCHEMA,
            "packets": digests,
            **side,
        }
    comparison: Record = {}
    for state in STATES:
        interval = _minus(
            deltas[INFLIGHT_WORLD.name][state], deltas[STOCK_WORLD.name][state]
        )
        comparison[state] = {
            "interval": _interval(interval),
            "comparison": _compare(interval),
        }
        assert comparison[state]["comparison"] in COMPARISONS
    return {
        "schema": SCHEMA,
        "question": QUESTION,
        "team": label,
        "states": {
            state: {world.name: world.own(state) for world in WORLDS}
            for state in STATES
        },
        "channels": list(CHANNELS),
        "comparisons": list(COMPARISONS),
        "worlds": worlds,
        "transfer": comparison,
        "notes": NOTES,
    }


def _tsv(rows: list[Record]) -> str:
    assert rows
    lines = ["\t".join(rows[0])]
    lines.extend("\t".join(str(value) for value in row.values()) for row in rows)
    return "\n".join(lines)


def _world_row(name: str, world: Record) -> Record:
    totals = world["totals"]
    return {
        "world": name,
        "schema": world["schema"],
        "registration_sha256": world["registration_sha256"],
        "packets": len(world["packets"]),
        **{key: value for key, value in totals.items() if key != "statuses"},
        **{f"status_{key}": count for key, count in totals["statuses"].items()},
    }


def _stratum_row(world: World, stratum: Record) -> Record:
    hidden = ("state", world.axis, "statuses", "success")
    return {
        "world": world.name,
        "state": stratum["state"],
        "registered_state": stratum[world.axis],
        **{key: value for key, value in stratum.items() if key not in hidden},
        **{f"status_{key}": count for key, count in stratum["statuses"].items()},
        "success": _text(stratum["success"]),
    }


def _transfer_row(report: Record, state: str) -> Record:
    found = report["transfer"][state]
    return {
        "team": report["team"],
        "state": state,
        **{
            f"{world.name}_delta": _text(report["worlds"][world.name]["delta"][state])
            for world in WORLDS
        },
        "transfer": _text(found["interval"]),
        "comparison": found["comparison"],
    }


def render(report: Record) -> str:
    worlds = report["worlds"]
    sections = [
        f"team: {report['team']}",
        _tsv([_world_row(world.name, worlds[world.name]) for world in WORLDS]),
        _tsv(
            [
                _stratum_row(world, stratum)
                for world in WORLDS
                for stratum in worlds[world.name]["strata"]
            ]
        ),
        _tsv([_transfer_row(report, state) for state in STATES]),
        "\n".join(f"{key}: {text}" for key, text in report["notes"].items()),
    ]
    return "\n\n".join(sections)


def _scrub(text: str, paths: list[Path]) -> str:
    forms = {
        form
        for path in paths
        for form in (str(path), os.path.abspath(path), os.path.realpath(path))
        if form
    }
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, "<input>")
    for world in (STOCK, INFLIGHT):
        text = world.ticket.sub("<ticket>", text)
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_DIAGNOSTIC]


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.ship_handover.transfer",
        description=QUESTION,
    )
    found.add_argument("stock", type=Path, metavar="STOCK_REG")
    found.add_argument("inflight", type=Path, metavar="INFLIGHT_REG")
    for world in WORLDS:
        found.add_argument(
            f"--{world.name}-packet",
            dest=f"{world.name}_packets",
            type=Path,
            action="append",
            default=[],
            metavar="P",
        )
    found.add_argument("--json", action="store_true")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    packets = {world.name: getattr(args, f"{world.name}_packets") for world in WORLDS}
    inputs = [
        args.stock,
        args.inflight,
        *(path for found in packets.values() for path in found),
    ]
    try:
        report = transfer(args.stock, args.inflight, packets)
    except (OSError, ValueError, Invalid) as error:
        print(f"transfer: {_scrub(_reason(error), inputs)}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
