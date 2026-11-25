import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

MAX_JSON = 1_048_576
MAX_PAIRS = 4096
MAX_PATH_DEPTH = 32
SCHEMA = "seatlab.exhibit-pairs/v1"
OBSERVATION_SCHEMA = "harbor.exhibit-relay.observation/v1"
CONTRASTS = {
    "coupling": {("masked", "bundled"), ("linked", "bundled")},
    "fork": {("independent", "linked")},
    "mesh": {("independent", "linked")},
}
STAGES = ("request", "registrar", "depot", "dispatch", "done")
COUNTS = (
    "correct_items",
    "registrar_supported",
    "depot_supported",
    "chains_supported",
    "dispatch_supported",
)
HANDOFF = {
    "total_weight",
    "explicit_ceiling_weight",
    "nominated_weight",
    "nominated_count",
    "registrar_filed",
    "depot_filed",
    "potential_intent_weight",
    "missing_depot_intent_weight",
    "visible_supported_weight",
    "visible_item_weight",
    "stage",
}
METRICS = ("physical", "C", "P")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def object_keys(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError("unexpected object fields")
    return cast(dict[str, Any], value)


def unique_object(entries: list[tuple[str, object]]) -> dict[str, Any]:
    result = {}
    for key, value in entries:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def reject_float(value: str) -> None:
    raise ValueError("noninteger JSON number")


def read_json(directory: int, name: str) -> object:
    fd = os.open(name, FILE_FLAGS, dir_fd=directory)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode), "not a regular file")
        require(info.st_size <= MAX_JSON, "JSON size limit")
        data = source.read(MAX_JSON + 1)
    require(len(data) <= MAX_JSON, "JSON size limit")
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=reject_float,
    )


def path_parts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not 0 < len(value) <= 4096:
        raise ValueError("invalid folder path")
    require("\\" not in value and "\x00" not in value, "invalid folder path")
    parts = tuple(value.split("/"))
    require(len(parts) <= MAX_PATH_DEPTH, "folder depth limit")
    require(
        all(part not in ("", ".", "..") for part in parts), "invalid relative folder"
    )
    return parts


def open_folder(root: int, parts: tuple[str, ...]) -> int:
    fd = os.dup(root)
    try:
        for part in parts:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def validate_manifest(raw: object) -> dict[str, Any]:
    manifest = object_keys(raw, {"schema", "profile", "arms", "pairs"})
    require(manifest["schema"] == SCHEMA, "invalid manifest schema")
    profile = manifest["profile"]
    arms = manifest["arms"]
    require(type(profile) is str and profile in CONTRASTS, "invalid profile")
    require(
        type(arms) is list and len(arms) == 2 and all(type(a) is str for a in arms),
        "invalid arms",
    )
    require(tuple(arms) in CONTRASTS[profile], "unsupported arm contrast or direction")
    pairs = manifest["pairs"]
    require(type(pairs) is list and 0 < len(pairs) <= MAX_PAIRS, "invalid roster size")
    tickets, cases, paths = set(), set(), set()
    for pair in pairs:
        object_keys(pair, {"id", "case_commitment", "left", "right"})
        ticket = pair["id"]
        case = pair["case_commitment"]
        require(
            type(ticket) is str and 0 < len(ticket) <= 256 and ticket.strip() == ticket,
            "invalid ticket",
        )
        require(
            type(case) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", case) is not None,
            "invalid case commitment",
        )
        require(ticket not in tickets and case not in cases, "duplicate ticket or case")
        tickets.add(ticket)
        cases.add(case)
        for side in ("left", "right"):
            parts = path_parts(pair[side])
            require(parts not in paths, "duplicate folder path")
            paths.add(parts)
    return manifest


def integer(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("invalid integer field")
    return value


@dataclass(frozen=True)
class Outcome:
    status: str
    physical: Fraction | None = None
    C: Fraction | None = None
    P: Fraction | None = None
    total_weight: int | None = None


def validate_handoff(
    handoff: dict[str, Any], complete: bool, profile: str
) -> tuple[int, Fraction | None, Fraction | None]:
    object_keys(handoff, HANDOFF)
    stage = handoff["stage"]
    require(stage in STAGES, "invalid stage")
    index = STAGES.index(stage)
    require(complete == (stage == "done"), "grade and stage disagree")
    total = integer(handoff["total_weight"], 1, 1_000_000)
    ceiling = integer(handoff["explicit_ceiling_weight"], 0, total)
    brief_count, nominate, registrar_cap, depot_cap = {
        "fork": (3, 2, 3, 3),
        "mesh": (6, 3, 4, 4),
        "coupling": (4, 4, 4, 2),
    }[profile]
    rules = (
        ("nominated_weight", 1, total),
        ("nominated_count", 1, min(brief_count, nominate)),
        ("registrar_filed", 2, registrar_cap),
        ("potential_intent_weight", 2, 2 * total),
        ("depot_filed", 3, depot_cap),
        ("missing_depot_intent_weight", 3, 2 * total),
        ("visible_supported_weight", 3, ceiling),
        ("visible_item_weight", 3, 2 * total),
    )
    for key, start, upper in rules:
        if index < start:
            require(handoff[key] is None, "premature handoff field")
        else:
            integer(handoff[key], 0, upper)
    if index >= 1:
        require(
            (handoff["nominated_weight"] == 0) == (handoff["nominated_count"] == 0),
            "nomination mismatch",
        )
    if index >= 2:
        require(
            handoff["potential_intent_weight"] <= 2 * handoff["nominated_weight"],
            "intent exceeds nominations",
        )
    if index < 3:
        return total, None, None
    require(
        handoff["missing_depot_intent_weight"] <= handoff["potential_intent_weight"],
        "missing weight exceeds intent",
    )
    chain = handoff["visible_supported_weight"]
    item = handoff["visible_item_weight"]
    require(2 * chain <= item <= total + chain, "inconsistent coverage")
    return total, Fraction(chain, total), Fraction(item, 2 * total)


def validate_observation(
    raw: object, folder: int, profile: str, mode: str, case: str
) -> Outcome:
    if isinstance(raw, dict) and raw.get("status") == "infrastructure_error":
        return Outcome("invalid")
    observation = object_keys(
        raw,
        {
            "schema",
            "profile",
            "mode",
            "case_commitment",
            "status",
            "reward",
            "total_items",
            "handoff",
            *COUNTS,
        },
    )
    require(observation["schema"] == OBSERVATION_SCHEMA, "invalid observation schema")
    if (
        observation["profile"],
        observation["mode"],
        observation["case_commitment"],
    ) != (profile, mode, case):
        return Outcome("identity_mismatch")
    status = observation["status"]
    require(status in ("complete", "incomplete"), "invalid grade status")
    integer(observation["total_items"], 2, 2)
    complete = status == "complete"
    total, chain, item = validate_handoff(observation["handoff"], complete, profile)
    if not complete:
        require(
            all(observation[key] is None for key in (*COUNTS, "reward")),
            "incomplete grade contains scores",
        )
        try:
            os.stat("reward.json", dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            return Outcome("incomplete", Fraction(0), chain, item, total)
        raise ValueError("unexpected reward file")
    for key in COUNTS:
        integer(observation[key], 0, 2)
    correct, registrar, depot, chains, dispatch = (observation[key] for key in COUNTS)
    require(
        max(0, registrar + depot - 2) <= chains <= min(registrar, depot),
        "inconsistent chain count",
    )
    require(
        max(0, chains + correct - 2) <= dispatch <= min(chains, correct),
        "inconsistent dispatch count",
    )
    reward = integer(observation["reward"], 0, 1)
    require(reward == int(correct == 2), "reward and grade disagree")
    receipt = object_keys(read_json(folder, "reward.json"), {"reward"})
    require(integer(receipt["reward"], 0, 1) == reward, "reward receipt disagrees")
    return Outcome("complete", Fraction(reward), chain, item, total)


def read_outcome(root: int, path: str, profile: str, mode: str, case: str) -> Outcome:
    try:
        folder = open_folder(root, path_parts(path))
        try:
            observation = read_json(folder, "observation.json")
            return validate_observation(observation, folder, profile, mode, case)
        finally:
            os.close(folder)
    except FileNotFoundError:
        return Outcome("missing")
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return Outcome("invalid")


def interval(value: Fraction | None) -> tuple[Fraction, Fraction]:
    return (Fraction(0), Fraction(1)) if value is None else (value, value)


def summarize_metric(
    rows: list[tuple[Outcome, Outcome]], metric: str
) -> dict[str, Any]:
    n = len(rows)
    left_sum = [Fraction(0), Fraction(0)]
    right_sum = [Fraction(0), Fraction(0)]
    coverage = [0, 0]
    discordance = {"left_greater": 0, "right_greater": 0, "equal": 0, "unknown": 0}
    for left, right in rows:
        a, b = getattr(left, metric), getattr(right, metric)
        for index, (value, sums) in enumerate(((a, left_sum), (b, right_sum))):
            lower, upper = interval(value)
            sums[0] += lower
            sums[1] += upper
            coverage[index] += value is not None
        if a is None or b is None:
            discordance["unknown"] += 1
        elif a > b:
            discordance["left_greater"] += 1
        elif b > a:
            discordance["right_greater"] += 1
        else:
            discordance["equal"] += 1
    bounds = [(right_sum[0] - left_sum[1]) / n, (right_sum[1] - left_sum[0]) / n]
    return {
        "difference_bounds": [str(value) for value in bounds],
        "left_mean_bounds": [str(value / n) for value in left_sum],
        "right_mean_bounds": [str(value / n) for value in right_sum],
        "coverage": {
            "left": str(Fraction(coverage[0], n)),
            "right": str(Fraction(coverage[1], n)),
            "paired": str(Fraction(n - discordance["unknown"], n)),
        },
        "discordance": discordance,
    }


def analyze(manifest_path: Path) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    root = os.open(manifest_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        manifest = validate_manifest(read_json(root, manifest_path.name))
        profile, arms = manifest["profile"], manifest["arms"]
        rows = []
        for pair in manifest["pairs"]:
            left = read_outcome(
                root, pair["left"], profile, arms[0], pair["case_commitment"]
            )
            right = read_outcome(
                root, pair["right"], profile, arms[1], pair["case_commitment"]
            )
            if (
                left.total_weight is not None
                and right.total_weight is not None
                and left.total_weight != right.total_weight
            ):
                left = right = Outcome("identity_mismatch")
            rows.append((left, right))
    finally:
        os.close(root)
    statuses = ("complete", "incomplete", "missing", "invalid", "identity_mismatch")
    counts = {
        side: {
            status: sum(row[index].status == status for row in rows)
            for status in statuses
        }
        for index, side in enumerate(("left", "right"))
    }
    invalid = sum(counts[side][status] for side in counts for status in statuses[2:])
    return {
        "schema": "seatlab.exhibit-pairs.summary/v1",
        "evidence": "declared local artifacts only; no native team, model, or CI authentication; no causal inference",
        "profile": profile,
        "arms": arms,
        "pair_count": len(rows),
        "outcome_counts": counts,
        "invalid_or_missing_count": invalid,
        "metrics": {metric: summarize_metric(rows, metric) for metric in METRICS},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Describe declared local Exhibit Relay verifier artifacts"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        summary = analyze(args.manifest)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError) as error:
        print(f"invalid enrollment: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, sort_keys=True, allow_nan=False))
    else:
        print(summary["evidence"])
        left, right = summary["arms"]
        print(
            f"{summary['profile']}: {right} minus {left}, {summary['pair_count']} enrolled pairs"
        )
        print(f"Outcome counts: {summary['outcome_counts']}")
        for name, data in summary["metrics"].items():
            print(
                f"{name}: bounds {data['difference_bounds']}; coverage {data['coverage']}; discordance {data['discordance']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
