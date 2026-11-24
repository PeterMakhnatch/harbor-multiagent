import argparse
import json
import math
import sys
import textwrap
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from ..native_evidence import Invalid, Record, require
from . import collect as collector
from .study import CHANNELS, INFLIGHT_WORLD, PRIORS, WORLD, load_registration

DELIVER, WITHHOLD = CHANNELS
KNOWN = {"zero": 0, "positive": 1}
OUTCOMES = ("omission", "exactly_one", "duplicate")
FATES = ("settles", "fails")
MONOTONIC = ("identified", "ruled_out", "unresolved", "not_comparable")
OPPOSITION = (
    "opposite_signs_identified",
    "possible_tradeoff",
    "tradeoff_ruled_out",
    "not_comparable",
)
MAX_DIAGNOSTIC = collector.MAX_DIAGNOSTIC
assert CHANNELS == ("deliver", "withhold")
assert PRIORS == ("committed", "dropped")
assert set(KNOWN) < set(collector.STATUSES) < set(collector.INFLIGHT_STATUSES)


def _unknown(statuses: tuple[str, ...]) -> str:
    names = [
        "duplicate ticket claim" if status == "duplicate" else status
        for status in statuses
        if status not in KNOWN
    ]
    assert 0 < len(names) == len(statuses) - len(KNOWN)
    return (
        f"every other registered cell ({', '.join(names)}) is unknown, never a"
        " failure; the collection status duplicate is a repeated ticket claim,"
        " not a duplicate dispatch"
    )


SUCCESS = (
    "[low,high]/denominator sets every unknown cell of the stratum to"
    " failure, then to success; an exact identification bound, not a"
    " sampling confidence interval"
)
FRACTIONS = (
    "every bound is exact: low and high share the least denominator that holds both"
)


class World(NamedTuple):
    registration: str
    schema: str
    axis: str
    states: tuple[str, str]
    statuses: tuple[str, ...]
    failure: dict[str, str]
    inherits: bool
    max_cells: int
    question: str
    notes: dict[str, str]

    @property
    def monotonic(self) -> str:
        return f"no_{self.axis}_worse_and_some_better"

    @property
    def strata(self) -> tuple[tuple[str, str], ...]:
        return tuple((state, channel) for state in self.states for channel in CHANNELS)

    @property
    def boundaries(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("Observed:", ("observed", "unknown", "failure")),
            (
                "Inferred (exact bounds only):",
                (
                    "success",
                    "delta",
                    "mean_delta",
                    self.monotonic,
                    "opposition",
                    "fractions",
                ),
            ),
            ("Not inferred:", ("scope",)),
        )


def _notes(
    axis: str,
    states: tuple[str, str],
    statuses: tuple[str, ...],
    texts: tuple[str, str, str],
) -> dict[str, str]:
    observed, failure, scope = texts
    return {
        "observed": observed,
        "unknown": _unknown(statuses),
        "failure": failure,
        "success": SUCCESS,
        "delta": (
            f"deliver minus withhold success rate within one {axis}; sharp bounds"
            " over every completion of the unknown cells"
        ),
        "mean_delta": (
            f"equally weighted mean of the {states[0]} and {states[1]} deltas, not a"
            " pooled rate over completed cells"
        ),
        f"no_{axis}_worse_and_some_better": (
            "identified when both lower deltas are >= 0 and one is > 0; ruled_out"
            " when an upper delta is < 0 or no upper delta is > 0; unresolved"
            " otherwise; not_comparable when one of the four registered"
            f" {axis} and channel denominators is zero"
        ),
        "opposition": (
            f"opposite_signs_identified when one {axis} delta is surely > 0 and the"
            " other surely < 0, even if their average is zero;"
            " possible_tradeoff when the bounds still allow opposite signs;"
            " tradeoff_ruled_out otherwise; not_comparable as above"
        ),
        "fractions": FRACTIONS,
        "scope": scope,
    }


STOCK = World(
    registration=WORLD[0],
    schema="harbor.ship-handover.contrast/v1",
    axis="prior",
    states=PRIORS,
    statuses=collector.STATUSES,
    failure={"committed": "duplicate", "dropped": "omission"},
    inherits=False,
    max_cells=WORLD[5],
    question=(
        "Does delivering the outgoing handoff improve both business risks"
        " (duplicate dispatch on committed fixtures, omission on dropped fixtures),"
        " or does a flat average hide opposite changes?"
    ),
    notes=_notes(
        "prior",
        PRIORS,
        collector.STATUSES,
        (
            (
                "per registered team, prior and channel: registered cells (the"
                " denominator), completed cells whose native reward matches the"
                " trusted verifier replay, and the business outcome of each"
                " completed cell as omission, exactly_one or duplicate dispatch"
            ),
            (
                "a committed fixture can fail only by duplicate dispatch and a"
                " dropped fixture only by omission; a committed success can be"
                " inherited with no recovery action"
            ),
            (
                "describes each registered team under the registered worlds only;"
                " no causal or model attribution to a seat, handoff content, inbox"
                " read or recovery action, no p-value, stopping rule or cost claim"
            ),
        ),
    ),
)
INFLIGHT = World(
    registration=INFLIGHT_WORLD[0],
    schema="harbor.ship-inflight.contrast/v1",
    axis="fate",
    states=FATES,
    statuses=collector.INFLIGHT_STATUSES,
    failure={"settles": "duplicate", "fails": "omission"},
    inherits=True,
    max_cells=INFLIGHT_WORLD[5],
    question=(
        "Does delivering the outgoing handoff improve both business risks of a"
        " shipment still in flight at the change of shift (duplicate dispatch"
        " when the original settles, omission when it fails), or does a flat"
        " average hide opposite changes?"
    ),
    notes=_notes(
        "fate",
        FATES,
        collector.INFLIGHT_STATUSES,
        (
            (
                "per registered team, fate and channel: registered cells (the"
                " denominator), completed cells whose native reward matches the"
                " trusted inflight verifier replay, the business outcome of each"
                " completed cell after the supplier drain as omission, exactly_one"
                " or duplicate dispatch, and inherited: exactly_one cells whose"
                " relief settled nothing before the freeze"
            ),
            (
                "success is exactly one dispatch after the drain; the original"
                " settles after the freeze unless relief replaced it, so a settles"
                " fixture can fail only by duplicate dispatch and a fails fixture"
                " only by omission; a settles success can be inherited with no"
                " relief action and still counts as success"
            ),
            (
                "describes each registered team under the registered worlds only;"
                " no causal or model attribution to a seat, handoff content, inbox"
                " read or relief action; a reference served before resolve is"
                " receipt, not attention, and a runtime model may act without"
                " reading it; unresolved marks a trial with a seat lacking a"
                " recorded execution disposition; execution and world evidence of"
                " unresolved or native_error trials stay outside these counts; no"
                " p-value, stopping rule or cost claim"
            ),
        ),
    ),
)
WORLDS = (STOCK, INFLIGHT)
for _checked_world in WORLDS:
    assert len(_checked_world.strata) == len(set(_checked_world.strata)) == 4
    assert set(_checked_world.failure) == set(_checked_world.states)
    assert set(_checked_world.failure.values()) == {"omission", "duplicate"}
    assert sorted(
        key for _, keys in _checked_world.boundaries for key in keys
    ) == sorted(_checked_world.notes)
assert len({world.registration for world in WORLDS}) == len(WORLDS)
assert len({world.schema for world in WORLDS}) == len(WORLDS)
assert INFLIGHT.failure["settles"] == "duplicate"

Interval = tuple[Fraction, Fraction]


def _world(field: str, value: object) -> World:
    found = [world for world in WORLDS if getattr(world, field) == value]
    require(len(found) == 1, f"{field} {value!r} names no known world")
    return found[0]


def _bound(interval: Interval | None) -> Record | None:
    if interval is None:
        return None
    low, high = interval
    assert -1 <= low <= high <= 1
    denominator = math.lcm(low.denominator, high.denominator)
    assert (low * denominator).denominator == (high * denominator).denominator == 1
    return {
        "low": int(low * denominator),
        "high": int(high * denominator),
        "denominator": denominator,
    }


def _text(bound: Record | None) -> str | None:
    if bound is None:
        return None
    return "[{low},{high}]/{denominator}".format(**bound)


def _inherited(world: World, row: Record, outcome: str) -> bool:
    if not world.inherits:
        return False
    observation = row["observation"]
    inherited = observation.get("inherited")
    settled = observation.get("settled")
    require(
        type(inherited) is bool and type(settled) is list,
        "observation lacks the inherited settlement",
    )
    require(
        inherited == (outcome == "exactly_one" and settled == []),
        "observation inherited differs from its settlement",
    )
    require(
        not inherited or world.failure[row[world.axis]] == "duplicate",
        "a success is inherited in a state that never drains",
    )
    return bool(inherited)


def _outcome(world: World, row: Record) -> tuple[str | None, bool]:
    status = row["status"]
    require(status in world.statuses, "collection status is not known")
    require(row[world.axis] in world.states, f"cell {world.axis} is not known")
    if status not in KNOWN:
        require(
            row["grade"] is None and row["observation"] is None,
            "an unknown cell carries an outcome",
        )
        return None, False
    observation = row["observation"]
    require(type(observation) is dict, "a completed cell has no observation")
    outcome = observation.get("outcome")
    require(outcome in OUTCOMES, "observation outcome is not known")
    require(
        row["grade"] == KNOWN[status] == int(outcome == "exactly_one"),
        "observation outcome differs from the grade",
    )
    require(
        outcome in ("exactly_one", world.failure[row[world.axis]]),
        f"observation outcome is impossible for its {world.axis}",
    )
    return str(outcome), _inherited(world, row, str(outcome))


def _stratum(
    world: World, key: tuple[str, str], found: list[tuple[str | None, bool]]
) -> tuple[Record, Interval | None]:
    state, channel = key
    counts = Counter(outcome for outcome, _ in found if outcome is not None)
    inherited = sum(flag for _, flag in found)
    registered = len(found)
    completed = sum(counts.values())
    unknown = registered - completed
    positive = counts["exactly_one"]
    assert 0 <= inherited <= positive <= completed <= registered <= world.max_cells
    assert set(counts) <= {"exactly_one", world.failure[state]}
    interval = (
        None
        if registered == 0
        else (
            Fraction(positive, registered),
            Fraction(positive + unknown, registered),
        )
    )
    record: Record = {
        world.axis: state,
        "channel": channel,
        "registered": registered,
        "completed": completed,
        "unknown": unknown,
        "outcomes": {outcome: counts[outcome] for outcome in OUTCOMES},
    }
    if world.inherits:
        record["inherited"] = inherited
    record["failure"] = world.failure[state]
    record["success"] = _bound(interval)
    return record, interval


def _delta(deliver: Interval | None, withhold: Interval | None) -> Interval | None:
    if deliver is None or withhold is None:
        return None
    delta = (deliver[0] - withhold[1], deliver[1] - withhold[0])
    assert -1 <= delta[0] <= delta[1] <= 1
    return delta


def _monotonic(deltas: list[Interval]) -> str:
    assert len(deltas) == 2
    lows = [low for low, _ in deltas]
    highs = [high for _, high in deltas]
    if min(lows) >= 0 and max(lows) > 0:
        return "identified"
    if min(highs) < 0 or max(highs) <= 0:
        return "ruled_out"
    return "unresolved"


def _opposition(first: Interval, second: Interval) -> str:
    pairs = ((first, second), (second, first))
    if any(better[0] > 0 and worse[1] < 0 for better, worse in pairs):
        return "opposite_signs_identified"
    if any(better[1] > 0 and worse[0] < 0 for better, worse in pairs):
        return "possible_tradeoff"
    return "tradeoff_ruled_out"


def _statuses(
    world: World, deltas: dict[str, Interval | None]
) -> tuple[Interval | None, str, str]:
    found = [deltas[state] for state in world.states]
    if any(delta is None for delta in found):
        return None, "not_comparable", "not_comparable"
    known = [delta for delta in found if delta is not None]
    assert len(known) == len(world.states)
    mean = (
        sum((low for low, _ in known), Fraction(0)) / len(known),
        sum((high for _, high in known), Fraction(0)) / len(known),
    )
    monotonic = _monotonic(known)
    opposition = _opposition(known[0], known[1])
    assert monotonic != "identified" or opposition == "tradeoff_ruled_out"
    assert opposition != "opposite_signs_identified" or monotonic == "ruled_out"
    return mean, monotonic, opposition


def _team(world: World, label: str, rows: list[Record]) -> Record:
    found: dict[tuple[str, str], list[tuple[str | None, bool]]] = {
        key: [] for key in world.strata
    }
    for row in rows:
        outcome = _outcome(world, row)
        found[(row[world.axis], row["channel"])].append(outcome)
    strata: list[Record] = []
    success: dict[tuple[str, str], Interval | None] = {}
    for key, outcomes in found.items():
        record, success[key] = _stratum(world, key, outcomes)
        strata.append(record)
    deltas = {
        state: _delta(success[(state, DELIVER)], success[(state, WITHHOLD)])
        for state in world.states
    }
    mean, monotonic, opposition = _statuses(world, deltas)
    comparable = all(interval is not None for interval in success.values())
    assert comparable == (mean is not None) == (monotonic != "not_comparable")
    assert monotonic in MONOTONIC and opposition in OPPOSITION
    return {
        "team": label,
        "registered": len(rows),
        "comparable": comparable,
        "strata": strata,
        "delta": {state: _bound(deltas[state]) for state in world.states},
        "mean_delta": _bound(mean),
        world.monotonic: monotonic,
        "opposition": opposition,
    }


def contrast(
    registration_path: Path, workspaces: list[Path], task_dir: Path | None
) -> Record:
    registration = load_registration(registration_path)
    world = _world("registration", registration["schema"])
    trusted = collector.load_trusted(registration, task_dir)
    collection = collector.collect_registered(registration, workspaces, trusted)
    cells = collection["cells"]
    require(
        tuple(collection["statuses"]) == world.statuses,
        "collection statuses differ from the registered world",
    )
    grouped: dict[str, list[Record]] = {label: [] for label in registration["teams"]}
    for row in cells:
        require(row["team"] in grouped, "a cell names an unregistered team")
        grouped[row["team"]].append(row)
    teams = [_team(world, label, grouped[label]) for label in sorted(grouped)]
    completed = sum(
        stratum["completed"] for team in teams for stratum in team["strata"]
    )
    require(completed == collection["valid"], "collection valid count differs")
    assert sum(team["registered"] for team in teams) == len(cells)
    return {
        "schema": world.schema,
        "question": world.question,
        "task": collection["task"],
        f"{world.axis}s": list(world.states),
        "channels": list(CHANNELS),
        "registered": len(cells),
        "completed": completed,
        "unknown": len(cells) - completed,
        "statuses": collection["statuses"],
        "teams": teams,
        world.monotonic: _tally(teams, world.monotonic, MONOTONIC),
        "opposition": _tally(teams, "opposition", OPPOSITION),
        "notes": world.notes,
    }


def _tally(teams: list[Record], field: str, values: tuple[str, ...]) -> Record:
    counts = Counter(team[field] for team in teams)
    assert set(counts) <= set(values)
    return {value: counts[value] for value in values}


def _tsv(rows: list[Record]) -> str:
    assert rows
    lines = ["\t".join(rows[0])]
    lines.extend(
        "\t".join("-" if value is None else str(value) for value in row.values())
        for row in rows
    )
    return "\n".join(lines)


def _stratum_row(world: World, team: Record, stratum: Record) -> Record:
    row: Record = {
        "team": team["team"],
        world.axis: stratum[world.axis],
        "channel": stratum["channel"],
        "registered": stratum["registered"],
        "completed": stratum["completed"],
        "unknown": stratum["unknown"],
        **{f"outcome_{key}": count for key, count in stratum["outcomes"].items()},
    }
    if world.inherits:
        row["inherited"] = stratum["inherited"]
    row["failure"] = stratum["failure"]
    row["success"] = _text(stratum["success"])
    return row


def _team_row(world: World, team: Record) -> Record:
    return {
        "team": team["team"],
        "registered": team["registered"],
        **{f"delta_{state}": _text(team["delta"][state]) for state in world.states},
        "mean_delta": _text(team["mean_delta"]),
        world.monotonic: team[world.monotonic],
        "opposition": team["opposition"],
    }


def render(report: Record) -> str:
    world = _world("schema", report["schema"])
    summary = {
        **{key: report[key] for key in ("registered", "completed", "unknown")},
        **{f"status_{key}": count for key, count in report["statuses"].items()},
        **{f"monotonic_{key}": count for key, count in report[world.monotonic].items()},
        **{f"opposition_{key}": count for key, count in report["opposition"].items()},
    }
    sections = [_tsv([summary])]
    teams = report["teams"]
    if teams:
        strata = [
            _stratum_row(world, team, stratum)
            for team in teams
            for stratum in team["strata"]
        ]
        sections.extend(
            (_tsv(strata), _tsv([_team_row(world, team) for team in teams]))
        )
    notes = report["notes"]
    sections.append("\n".join(f"{key}: {text}" for key, text in notes.items()))
    return "\n\n".join(sections)


def _safe(text: str) -> str:
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_DIAGNOSTIC]


def _epilog() -> str:
    lines: list[str] = []
    for title, keys in STOCK.boundaries:
        lines.append(title)
        lines.extend(
            textwrap.fill(
                f"{key}: {STOCK.notes[key]}",
                width=78,
                initial_indent="  ",
                subsequent_indent="    ",
            )
            for key in keys
        )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="python3 -BS -m examples.ship_handover.contrast",
        description=STOCK.question,
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    found.add_argument("registration", type=Path)
    found.add_argument("workspaces", type=Path, nargs="+", metavar="WORKSPACE")
    found.add_argument("--task", type=Path, metavar="MATERIALIZED")
    found.add_argument("--json", action="store_true")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = contrast(args.registration, args.workspaces, args.task)
    except OSError as error:
        reason = _safe(error.strerror or "storage failed")
        print(f"contrast: {reason}", file=sys.stderr)
        return 2
    except (ValueError, Invalid) as error:
        print(f"contrast: {_safe(str(error))}", file=sys.stderr)
        return 2
    text = (
        json.dumps(report, indent=2, ensure_ascii=True) if args.json else render(report)
    )
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
