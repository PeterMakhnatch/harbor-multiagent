"""Validate source-only prefix forecasts against independent native calendar episodes."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, NamedTuple, TypedDict, TypeGuard, cast
from uuid import UUID

if TYPE_CHECKING:
    from seatlab.episodes import Episode

# The interactive cohort has case 1 in development and cases 2–6 in follow-up.
_CASES = tuple(f"{stratum}/{case}" for stratum in range(2, 6) for case in range(1, 7))
_ROLE = "m1"
_EXPORT_STATUSES = {"verified", "unattempted", "incomplete", "failed", "invalid"}


class _Action(TypedDict):
    action: str | int | None


class _Forecast(TypedDict):
    id: str
    case_sha256: str
    role: str
    native_trial_id: str
    episode_sha256: str
    closed: bool
    frames: int
    matched_prefix: int
    boundary_turn: int | None
    status: str
    source_action: _Action | None
    candidate_action: _Action | None
    error: str | None
    calls: int
    withheld_suffix: int


class _AlignmentRow(TypedDict):
    """Fields consumed from the trusted SeatLab alignment result."""

    id: str
    status: str
    matched_prefix: int
    boundary_turn: int | None
    source_action: _Action | None
    target_action: _Action | None
    source_view_sha256: str | None
    target_view_sha256: str | None


_FORECAST_FIELDS = frozenset(_Forecast.__annotations__)
_FORECAST_STATUSES = {
    "matched",
    "diverged",
    "unbound",
    "missing_observation",
    "policy_failed",
}
_POLICY_ERRORS = {
    "invalid_action",
    "invalid_output",
    "spawn_failed",
    "policy_failed",
    "timeout",
    "output_limit",
}
_OUTCOMES = ("confirmed_action_change", "confirmed_match", "unconfirmed", "missing")


@dataclass(frozen=True)
class _Native:
    status: str
    trial: str | None
    episode: Episode | None

    def receipt(self) -> dict[str, str] | None:
        if self.episode is None:
            return None
        return {
            "native_trial_id": self.episode["native_trial_id"],
            "episode_sha256": self.episode["episode_sha256"],
        }

    def summary(self) -> dict[str, object]:
        episode = self.episode
        return {
            "status": self.status,
            "native_trial_id": self.trial,
            "receipt": self.receipt(),
            "episode_id": None if episode is None else episode["id"],
            "closed": None if episode is None else episode["closed"],
            "frames": None if episode is None else len(episode["frames"]),
        }


class _Case(NamedTuple):
    case_id: str
    group: str
    pair_id: str
    case_sha256: str | None
    source: _Native
    target: _Native


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON number")


def _read_json(path: Path) -> object:
    return json.loads(
        path.read_bytes(),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _is_json_array(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _is_int(value: object) -> TypeGuard[int]:
    return type(value) is int


def _json_bytes(value: object) -> bytes:
    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
    return (text + "\n").encode("ascii")


def _canonical_uuid(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _read_export(root: Path, arm: str) -> dict[str, _Native]:
    from seatlab.episodes import read_episodes

    index = _read_json(root / "index.json")
    if not _is_json_array(index):
        raise ValueError(f"{arm} index must be an array")
    episodes = {row["id"]: row for row in read_episodes(root / "episodes.jsonl")}
    rows: dict[str, _Native] = {}
    trials: set[str] = set()
    for entry in index:
        if not _is_json_object(entry) or entry.keys() - {
            "case_id",
            "status",
            "reason",
            "native_trial_id",
            "episode_id",
        }:
            raise ValueError(f"invalid {arm} index row")
        case = entry.get("case_id")
        if not isinstance(case, str) or case not in _CASES or case in rows:
            raise ValueError(
                f"{arm} index has an unexpected or duplicate registered case"
            )
        status = entry.get("status")
        if not isinstance(status, str) or status not in _EXPORT_STATUSES:
            raise ValueError(f"{arm} {case}: invalid index status")
        if "reason" in entry and not isinstance(entry["reason"], str):
            raise ValueError(f"{arm} {case}: invalid index reason")
        trial: str | None = None
        if "native_trial_id" in entry:
            value = entry["native_trial_id"]
            if not _canonical_uuid(value) or value in trials:
                raise ValueError(
                    f"{arm} {case}: invalid or duplicate native trial UUID"
                )
            trial = value
            trials.add(trial)
        episode: Episode | None = None
        if status == "verified":
            expected_id = f"calendar:{case.replace('/', '-')}:cursor:{_ROLE}"
            if trial is None or entry.get("episode_id") != expected_id:
                raise ValueError(
                    f"{arm} {case}: verified index identity does not match case"
                )
            episode = episodes.pop(expected_id, None)
            if (
                episode is None
                or episode["role"] != _ROLE
                or episode["native_trial_id"] != trial
            ):
                raise ValueError(
                    f"{arm} {case}: verified index identity does not match episode"
                )
        elif "episode_id" in entry:
            raise ValueError(
                f"{arm} {case}: an unverified case cannot register an episode"
            )
        rows[case] = _Native(status, trial, episode)
    if rows.keys() != set(_CASES):
        raise ValueError(f"{arm} index must contain exactly the 24 registered cases")
    if episodes:
        raise ValueError(f"{arm} export has episodes absent from its verified index")
    return rows


def _register_cases(
    source: dict[str, _Native], target: dict[str, _Native]
) -> list[_Case]:
    shared_trials = {
        item.trial for item in source.values() if item.trial is not None
    } & {item.trial for item in target.values() if item.trial is not None}
    if shared_trials:
        raise ValueError("source and target native trial UUIDs must be independent")
    cases = []
    for case_id in _CASES:
        left, right = source[case_id], target[case_id]
        hashes = {
            item.episode["case_sha256"]
            for item in (left, right)
            if item.episode is not None
        }
        if len(hashes) > 1:
            raise ValueError(f"{case_id}: source and target case digests differ")
        group = "development" if case_id.endswith("/1") else "follow_up"
        case_hash = next(iter(hashes), None)
        cases.append(
            _Case(case_id, group, case_id.replace("/", "-"), case_hash, left, right)
        )
    return cases


def _is_action(value: object) -> TypeGuard[_Action]:
    return (
        _is_json_object(value)
        and value.keys() == {"action"}
        and type(value["action"]) in (str, int, type(None))
    )


def _same_action(left: object, right: object) -> bool:
    # An absent observation is not an observed null action.
    if left is None or right is None:
        return left is right
    if not _is_action(left) or not _is_action(right):
        return False
    a, b = left["action"], right["action"]
    return type(a) is type(b) and a == b


def _check_forecast(row: dict[str, object], native: Episode) -> _Forecast:
    """Check all wire fields before treating the JSON object as a forecast."""
    label = f"forecast {native['id']}"
    identity = (
        ("id", native["id"]),
        ("case_sha256", native["case_sha256"]),
        ("role", native["role"]),
        ("native_trial_id", native["native_trial_id"]),
        ("episode_sha256", native["episode_sha256"]),
    )
    for field, value in identity:
        if row[field] != value:
            raise ValueError(f"{label}: {field} does not match native source")
    frames = native["frames"]
    size = len(frames)
    if (
        row["closed"] is not native["closed"]
        or not _is_int(row["frames"])
        or row["frames"] != size
    ):
        raise ValueError(f"{label}: closed or frames does not match native source")
    matched, calls, withheld = (
        row[key] for key in ("matched_prefix", "calls", "withheld_suffix")
    )
    if (
        not _is_int(matched)
        or not 0 <= matched <= size
        or not _is_int(calls)
        or not _is_int(withheld)
    ):
        raise ValueError(f"{label}: invalid prefix counts")
    for turn in range(matched):
        frame = frames[turn]
        if frame["basis"] != "bound" or frame["observed"] is None:
            raise ValueError(
                f"{label}: matched prefix crosses an ineligible source frame"
            )

    status = row["status"]
    boundary = row["boundary_turn"]
    source, candidate, error = (
        row[key] for key in ("source_action", "candidate_action", "error")
    )
    if matched == size:
        if (status, boundary, source, candidate, error, calls, withheld) != (
            "matched",
            None,
            None,
            None,
            None,
            size,
            0,
        ):
            raise ValueError(f"{label}: inconsistent full prefix")
        return cast(_Forecast, row)

    frame = frames[matched]
    if (
        not _is_int(boundary)
        or boundary != matched + 1
        or withheld != size - boundary
        or not _same_action(source, frame["observed"])
    ):
        raise ValueError(f"{label}: boundary does not match native source")
    if frame["basis"] != "bound":
        valid = status == "unbound" and calls == matched
    elif frame["observed"] is None:
        valid = status == "missing_observation" and calls == matched
    elif status == "diverged":
        valid = (
            _is_action(candidate)
            and not _same_action(source, candidate)
            and calls == boundary
        )
    else:
        valid = status == "policy_failed" and calls in (matched, boundary)
    if (
        not valid
        or (status != "diverged" and candidate is not None)
        or (status != "policy_failed" and error is not None)
        or (
            status == "policy_failed"
            and (not isinstance(error, str) or error not in _POLICY_ERRORS)
        )
    ):
        raise ValueError(f"{label}: inconsistent boundary status or actions")
    return cast(_Forecast, row)


def _read_forecast(path: Path, cases: list[_Case]) -> tuple[bool, dict[str, _Forecast]]:
    document = _read_json(path)
    if (
        not _is_json_object(document)
        or document.keys()
        != {"schema", "policy", "interpretation", "history", "counts", "episodes"}
        or document["schema"] != "seatlab.prefix-result/v1"
        or document["policy"] != "external"
        or document["interpretation"] != "recorded_prefix_agreement"
    ):
        raise ValueError("expected a SeatLab v1 prefix result with an external policy")
    history, episode_rows = document["history"], document["episodes"]
    if not isinstance(history, bool) or not _is_json_array(episode_rows):
        raise ValueError("expected a SeatLab v1 prefix result with an external policy")
    expected: dict[str, Episode] = {}
    for case in cases:
        episode = case.source.episode
        if episode is not None:
            expected[episode["id"]] = episode
    results: dict[str, _Forecast] = {}
    counts: dict[str, int] = dict.fromkeys(
        (
            "frames",
            "matched_prefix",
            "calls",
            "withheld_suffix",
            *_FORECAST_STATUSES,
            "episodes",
            "observed_prefix",
            "matched_open",
            "matched_closed",
        ),
        0,
    )
    for row in episode_rows:
        if not _is_json_object(row) or row.keys() != _FORECAST_FIELDS:
            raise ValueError("invalid forecast episode row")
        episode_id = row["id"]
        if (
            not isinstance(episode_id, str)
            or episode_id not in expected
            or episode_id in results
        ):
            raise ValueError(
                "forecast must list each verified source episode exactly once"
            )
        verified = _check_forecast(row, expected[episode_id])
        results[episode_id] = verified
        counts["episodes"] += 1
        counts[verified["status"]] += 1
        counts["frames"] += verified["frames"]
        counts["matched_prefix"] += verified["matched_prefix"]
        counts["calls"] += verified["calls"]
        counts["withheld_suffix"] += verified["withheld_suffix"]
        if verified["status"] == "matched":
            counts["matched_closed" if verified["closed"] else "matched_open"] += 1
    if results.keys() != expected.keys():
        raise ValueError("forecast must list each verified source episode exactly once")
    provided = document["counts"]
    if (
        not _is_json_object(provided)
        or any(not _is_int(value) for value in provided.values())
        or provided != counts
    ):
        raise ValueError("forecast counts do not match its verified episode rows")
    return history, results


def _outcome(
    forecast: _Forecast | None, aligned: _AlignmentRow | None
) -> tuple[str, str]:
    if aligned is None:
        return "missing", "both_native_episodes_missing"
    if aligned["status"] == "missing":
        return "missing", "native_episode_missing"
    if forecast is None:
        raise ValueError("native pair lacks its source forecast")
    status = forecast["status"]
    if status == "matched":
        if (
            aligned["status"] == "matched"
            and forecast["closed"]
            and aligned["matched_prefix"] == forecast["frames"]
        ):
            return "confirmed_match", "full_closed_agreement"
        return "unconfirmed", "no_full_closed_agreement"
    if status != "diverged":
        return "unconfirmed", "forecast_did_not_make_a_comparable_claim"
    if aligned["status"] != "action_changed":
        return "unconfirmed", "native_boundary_is_not_an_action_change"
    if (
        aligned["boundary_turn"] != forecast["boundary_turn"]
        or aligned["matched_prefix"] != forecast["matched_prefix"]
        or aligned["source_view_sha256"] is None
        or aligned["source_view_sha256"] != aligned["target_view_sha256"]
        or not _same_action(aligned["source_action"], forecast["source_action"])
    ):
        return "unconfirmed", "native_boundary_does_not_match_forecast_boundary"
    if not _same_action(aligned["target_action"], forecast["candidate_action"]):
        return "unconfirmed", "native_target_action_does_not_match_candidate"
    return "confirmed_action_change", "same_input_and_candidate_action"


def validate_prefix(source: Path, target: Path, forecast: Path, out: Path) -> None:
    """Write the manifest, normative alignment, and confirmation counts."""
    from seatlab.alignment import align

    try:
        out.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("output must be a new directory")
    cases = _register_cases(
        _read_export(source, "source"), _read_export(target, "target")
    )
    history, forecasts = _read_forecast(forecast, cases)
    pairs = {
        "schema": "seatlab.episode-pairs/v1",
        "pairs": [
            {
                "id": case.pair_id,
                "case_sha256": case.case_sha256,
                "role": _ROLE,
                "source": case.source.receipt(),
                "target": case.target.receipt(),
            }
            for case in cases
            if case.case_sha256 is not None
        ],
    }
    pair_bytes = _json_bytes(pairs)
    with TemporaryDirectory(prefix="seatlab-native-alignment-") as tmp:
        manifest = Path(tmp) / "pairs.json"
        manifest.write_bytes(pair_bytes)
        alignment = align(
            source / "episodes.jsonl", target / "episodes.jsonl", manifest
        )
    # SeatLab exposes a generic dict, but its v1 result supplies these row fields.
    aligned = {row["id"]: row for row in cast(list[_AlignmentRow], alignment["pairs"])}

    count_names = (
        "scheduled",
        "source_verified",
        "target_verified",
        "manifest_pairs",
        "skipped_no_case_hash",
        *_OUTCOMES,
    )
    counts: dict[str, dict[str, int]] = {
        group: dict.fromkeys(count_names, 0)
        for group in ("all", "development", "follow_up")
    }
    results = []
    for case in cases:
        episode = case.source.episode
        prediction = None if episode is None else forecasts[episode["id"]]
        native = None if case.case_sha256 is None else aligned[case.pair_id]
        outcome, reason = _outcome(prediction, native)
        results.append(
            {
                "case_id": case.case_id,
                "group": case.group,
                "case_sha256": case.case_sha256,
                "pair_id": None if native is None else case.pair_id,
                "source": case.source.summary(),
                "target": case.target.summary(),
                "forecast": prediction,
                "alignment_status": "missing" if native is None else native["status"],
                "status": outcome,
                "reason": reason,
            }
        )
        for group in (counts["all"], counts[case.group]):
            group["scheduled"] += 1
            group["source_verified"] += case.source.episode is not None
            group["target_verified"] += case.target.episode is not None
            group[
                "manifest_pairs" if native is not None else "skipped_no_case_hash"
            ] += 1
            group[outcome] += 1
    validation = {
        "schema": "seatlab.prefix-validation/v1",
        "policy": "external",
        "history": history,
        "counts": counts,
        "cases": results,
    }
    payloads = {
        "pairs.json": pair_bytes,
        "alignment.json": _json_bytes(alignment),
        "validation.json": _json_bytes(validation),
    }
    try:
        out.mkdir()
    except FileExistsError:
        raise ValueError("output must be a new directory") from None
    for name, payload in payloads.items():
        with (out / name).open("xb") as output:
            output.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Install SeatLab v0.3 or put its src directory on PYTHONPATH. No policy runs here.",
    )
    parser.add_argument(
        "source_export",
        type=Path,
        metavar="source-export",
        help="source index.json and episodes.jsonl directory",
    )
    parser.add_argument(
        "target_export",
        type=Path,
        metavar="target-export",
        help="independent target index.json and episodes.jsonl directory",
    )
    parser.add_argument(
        "forecast_result",
        type=Path,
        metavar="forecast-result",
        help="source-only seatlab.prefix-result/v1 JSON",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        metavar="NEW_DIR",
        help="new directory for pairs.json, alignment.json and validation.json",
    )
    args = parser.parse_args(argv)
    try:
        validate_prefix(
            args.source_export, args.target_export, args.forecast_result, args.out
        )
    except ImportError:
        parser.exit(
            1,
            "validate_prefix: install SeatLab v0.3 or put its src directory on PYTHONPATH\n",
        )
    except (ValueError, OSError) as exc:
        parser.exit(1, f"validate_prefix: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
