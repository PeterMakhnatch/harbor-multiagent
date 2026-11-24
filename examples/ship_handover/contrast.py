import argparse
import json
import math
import sys
import textwrap
from collections import Counter
from fractions import Fraction
from pathlib import Path

from ..native_evidence import Invalid, Record, require
from . import collect as collector
from .study import CHANNELS, MAX_CELLS, PRIORS, load_registration

SCHEMA = "harbor.ship-handover.contrast/v1"
DELIVER, WITHHOLD = CHANNELS
KNOWN = {"zero": 0, "positive": 1}
OUTCOMES = ("omission", "exactly_one", "duplicate")
FAILURE = {"committed": "duplicate", "dropped": "omission"}
MONOTONIC = ("identified", "ruled_out", "unresolved", "not_comparable")
OPPOSITION = (
    "opposite_signs_identified",
    "possible_tradeoff",
    "tradeoff_ruled_out",
    "not_comparable",
)
STRATA = tuple((prior, channel) for prior in PRIORS for channel in CHANNELS)
MAX_DIAGNOSTIC = collector.MAX_DIAGNOSTIC
assert CHANNELS == ("deliver", "withhold")
assert PRIORS == ("committed", "dropped") and set(FAILURE) == set(PRIORS)
assert set(KNOWN) < set(collector.STATUSES)
assert len(STRATA) == len(set(STRATA)) == 4

QUESTION = (
    "Does delivering the outgoing handoff improve both business risks"
    " (duplicate dispatch on committed fixtures, omission on dropped fixtures),"
    " or does a flat average hide opposite changes?"
)
NOTES = {
    "observed": (
        "per registered team, prior and channel: registered cells (the"
        " denominator), completed cells whose native reward matches the"
        " trusted verifier replay, and the business outcome of each completed"
        " cell as omission, exactly_one or duplicate dispatch"
    ),
    "unknown": (
        "every other registered cell (no_result, partial, ambiguous, duplicate"
        " ticket claim, native_error, invalid) is unknown, never a failure;"
        " the collection status duplicate is a repeated ticket claim, not a"
        " duplicate dispatch"
    ),
    "failure": (
        "a committed fixture can fail only by duplicate dispatch and a dropped"
        " fixture only by omission; a committed success can be inherited with"
        " no recovery action"
    ),
    "success": (
        "[low,high]/denominator sets every unknown cell of the stratum to"
        " failure, then to success; an exact identification bound, not a"
        " sampling confidence interval"
    ),
    "delta": (
        "deliver minus withhold success rate within one prior; sharp bounds"
        " over every completion of the unknown cells"
    ),
    "mean_delta": (
        "equally weighted mean of the committed and dropped deltas, not a"
        " pooled rate over completed cells"
    ),
    "no_prior_worse_and_some_better": (
        "identified when both lower deltas are >= 0 and one is > 0; ruled_out"
        " when an upper delta is < 0 or no upper delta is > 0; unresolved"
        " otherwise; not_comparable when one of the four registered prior and"
        " channel denominators is zero"
    ),
    "opposition": (
        "opposite_signs_identified when one prior delta is surely > 0 and the"
        " other surely < 0, even if their average is zero;"
        " possible_tradeoff when the bounds still allow opposite signs;"
        " tradeoff_ruled_out otherwise; not_comparable as above"
    ),
    "fractions": (
        "every bound is exact: low and high share the least denominator that holds both"
    ),
    "scope": (
        "describes each registered team under the registered worlds only; no"
        " causal or model attribution to a seat, handoff content, inbox read or"
        " recovery action, no p-value, stopping rule or cost claim"
    ),
}
BOUNDARIES = (
    ("Observed:", ("observed", "unknown", "failure")),
    (
        "Inferred (exact bounds only):",
        (
            "success",
            "delta",
            "mean_delta",
            "no_prior_worse_and_some_better",
            "opposition",
            "fractions",
        ),
    ),
    ("Not inferred:", ("scope",)),
)
assert sorted(key for _, keys in BOUNDARIES for key in keys) == sorted(NOTES)

Interval = tuple[Fraction, Fraction]


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


def _outcome(row: Record) -> str | None:
    status = row["status"]
    require(status in collector.STATUSES, "collection status is not known")
    if status not in KNOWN:
        require(
            row["grade"] is None and row["observation"] is None,
            "an unknown cell carries an outcome",
        )
        return None
    observation = row["observation"]
    require(type(observation) is dict, "a completed cell has no observation")
    outcome = observation.get("outcome")
    require(outcome in OUTCOMES, "observation outcome is not known")
    require(
        row["grade"] == KNOWN[status] == int(outcome == "exactly_one"),
        "observation outcome differs from the grade",
    )
    require(
        outcome in ("exactly_one", FAILURE[row["prior"]]),
        "observation outcome is impossible for its prior",
    )
    return str(outcome)


def _stratum(
    prior: str, channel: str, outcomes: list[str | None]
) -> tuple[Record, Interval | None]:
    counts = Counter(outcome for outcome in outcomes if outcome is not None)
    registered = len(outcomes)
    completed = sum(counts.values())
    unknown = registered - completed
    positive = counts["exactly_one"]
    assert 0 <= positive <= completed <= registered <= MAX_CELLS
    assert set(counts) <= {"exactly_one", FAILURE[prior]}
    interval = (
        None
        if registered == 0
        else (
            Fraction(positive, registered),
            Fraction(positive + unknown, registered),
        )
    )
    record = {
        "prior": prior,
        "channel": channel,
        "registered": registered,
        "completed": completed,
        "unknown": unknown,
        "outcomes": {outcome: counts[outcome] for outcome in OUTCOMES},
        "failure": FAILURE[prior],
        "success": _bound(interval),
    }
    return record, interval


def _delta(deliver: Interval | None, withhold: Interval | None) -> Interval | None:
    if deliver is None or withhold is None:
        return None
    delta = (deliver[0] - withhold[1], deliver[1] - withhold[0])
    assert -1 <= delta[0] <= delta[1] <= 1
    return delta


def _monotonic(deltas: list[Interval]) -> str:
    assert len(deltas) == len(PRIORS)
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


def _statuses(deltas: dict[str, Interval | None]) -> tuple[Interval | None, str, str]:
    found = [deltas[prior] for prior in PRIORS]
    if any(delta is None for delta in found):
        return None, "not_comparable", "not_comparable"
    known = [delta for delta in found if delta is not None]
    assert len(known) == len(PRIORS)
    mean = (
        sum((low for low, _ in known), Fraction(0)) / len(known),
        sum((high for _, high in known), Fraction(0)) / len(known),
    )
    monotonic = _monotonic(known)
    opposition = _opposition(known[0], known[1])
    assert monotonic != "identified" or opposition == "tradeoff_ruled_out"
    assert opposition != "opposite_signs_identified" or monotonic == "ruled_out"
    return mean, monotonic, opposition


def _team(label: str, rows: list[Record]) -> Record:
    outcomes: dict[tuple[str, str], list[str | None]] = {key: [] for key in STRATA}
    for row in rows:
        outcomes[(row["prior"], row["channel"])].append(_outcome(row))
    strata: list[Record] = []
    success: dict[tuple[str, str], Interval | None] = {}
    for (prior, channel), found in outcomes.items():
        record, success[(prior, channel)] = _stratum(prior, channel, found)
        strata.append(record)
    deltas = {
        prior: _delta(success[(prior, DELIVER)], success[(prior, WITHHOLD)])
        for prior in PRIORS
    }
    mean, monotonic, opposition = _statuses(deltas)
    comparable = all(interval is not None for interval in success.values())
    assert comparable == (mean is not None) == (monotonic != "not_comparable")
    assert monotonic in MONOTONIC and opposition in OPPOSITION
    return {
        "team": label,
        "registered": len(rows),
        "comparable": comparable,
        "strata": strata,
        "delta": {prior: _bound(deltas[prior]) for prior in PRIORS},
        "mean_delta": _bound(mean),
        "no_prior_worse_and_some_better": monotonic,
        "opposition": opposition,
    }


def contrast(registration_path: Path, workspaces: list[Path]) -> Record:
    registration = load_registration(registration_path)
    trusted = collector.load_trusted(registration["task"])
    collection = collector.collect_registered(registration, workspaces, trusted)
    cells = collection["cells"]
    grouped: dict[str, list[Record]] = {label: [] for label in registration["teams"]}
    for row in cells:
        require(row["team"] in grouped, "a cell names an unregistered team")
        grouped[row["team"]].append(row)
    teams = [_team(label, grouped[label]) for label in sorted(grouped)]
    completed = sum(
        stratum["completed"] for team in teams for stratum in team["strata"]
    )
    require(completed == collection["valid"], "collection valid count differs")
    assert sum(team["registered"] for team in teams) == len(cells)
    return {
        "schema": SCHEMA,
        "question": QUESTION,
        "task": collection["task"],
        "priors": list(PRIORS),
        "channels": list(CHANNELS),
        "registered": len(cells),
        "completed": completed,
        "unknown": len(cells) - completed,
        "statuses": collection["statuses"],
        "teams": teams,
        "no_prior_worse_and_some_better": _tally(
            teams, "no_prior_worse_and_some_better", MONOTONIC
        ),
        "opposition": _tally(teams, "opposition", OPPOSITION),
        "notes": NOTES,
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


def _stratum_row(team: Record, stratum: Record) -> Record:
    return {
        "team": team["team"],
        "prior": stratum["prior"],
        "channel": stratum["channel"],
        "registered": stratum["registered"],
        "completed": stratum["completed"],
        "unknown": stratum["unknown"],
        **{f"outcome_{key}": count for key, count in stratum["outcomes"].items()},
        "failure": stratum["failure"],
        "success": _text(stratum["success"]),
    }


def _team_row(team: Record) -> Record:
    return {
        "team": team["team"],
        "registered": team["registered"],
        **{f"delta_{prior}": _text(team["delta"][prior]) for prior in PRIORS},
        "mean_delta": _text(team["mean_delta"]),
        "no_prior_worse_and_some_better": team["no_prior_worse_and_some_better"],
        "opposition": team["opposition"],
    }


def render(report: Record) -> str:
    summary = {
        **{key: report[key] for key in ("registered", "completed", "unknown")},
        **{f"status_{key}": count for key, count in report["statuses"].items()},
        **{
            f"monotonic_{key}": count
            for key, count in report["no_prior_worse_and_some_better"].items()
        },
        **{f"opposition_{key}": count for key, count in report["opposition"].items()},
    }
    sections = [_tsv([summary])]
    teams = report["teams"]
    if teams:
        strata = [
            _stratum_row(team, stratum) for team in teams for stratum in team["strata"]
        ]
        sections.extend((_tsv(strata), _tsv([_team_row(team) for team in teams])))
    notes = report["notes"]
    sections.append("\n".join(f"{key}: {text}" for key, text in notes.items()))
    return "\n\n".join(sections)


def _safe(text: str) -> str:
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_DIAGNOSTIC]


def _epilog() -> str:
    lines: list[str] = []
    for title, keys in BOUNDARIES:
        lines.append(title)
        lines.extend(
            textwrap.fill(
                f"{key}: {NOTES[key]}",
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
        description=QUESTION,
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    found.add_argument("registration", type=Path)
    found.add_argument("workspaces", type=Path, nargs="+", metavar="WORKSPACE")
    found.add_argument("--json", action="store_true")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = contrast(args.registration, args.workspaces)
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
