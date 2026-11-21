"""Replay one archived population without exposing paths, identities, or messages."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from . import crucible, flotilla, muster
from .core import Trial, project, summarize

Loader = Callable[[Path], Iterable[Trial]]
_POPULATIONS: dict[str, tuple[int, Loader]] = {
    "flotilla": (276, flotilla.load),
    "muster": (210, muster.load),
    "crucible": (130, crucible.load),
    "crucible-timeouts": (8, crucible.load_excluded),
}


def _fingerprints(files: set[Path]) -> dict[Path, bytes]:
    result = {}
    for path in files:
        with path.open("rb") as stream:
            result[path] = hashlib.file_digest(stream, "sha256").digest()
    return result


def _compare(trial: Trial) -> dict[str, bool]:
    """Compare actual projections. Timing concerns original recorded actions only."""
    baseline = project(trial)
    changed = {kind: False for kind in ("availability", "fetch", "bound")}
    before_action = dict.fromkeys(changed, False)
    for seat in trial.seats:
        muted = project(trial, muted_seat=seat)
        later_action = False
        for old, new in zip(
            reversed(baseline.events), reversed(muted.events), strict=True
        ):
            if old.seat != seat or old.kind not in ("available", "fetch", "bound"):
                if old != new:
                    raise ValueError("projection changed a protected event")
                later_action |= old.kind == "action" and old.seat == seat
                continue
            expected = tuple(
                pair
                for pair in old.messages
                if pair[0] == seat or pair[0] not in trial.seats
            )
            if (new.kind, new.seat, new.messages) != (old.kind, seat, expected):
                raise ValueError("projection changed data outside the intervention")
            if new.messages != old.messages:
                kind = "availability" if old.kind == "available" else old.kind
                changed[kind] = True
                before_action[kind] |= later_action
    return {
        **{f"trials_with_changed_{kind}": count for kind, count in changed.items()},
        **{
            f"trials_with_changed_{kind}_before_action": count
            for kind, count in before_action.items()
        },
    }


def replay(root: Path, population: str) -> dict[str, object]:
    """Return a path-free public report. Never write to the source tree."""
    expected, load = _POPULATIONS[population]
    # Some worlds declare the input files on the individual trial, not the job.
    sources = {path for trial in load(root) for path in trial.sources}
    if not sources:
        raise ValueError("population has no recorded source files")
    before = _fingerprints(sources)
    observed: set[Path] = set()
    projection: Counter[str] = Counter()
    per_arm: dict[str, Counter[str]] = {}
    count = 0
    solos = 0

    def trials() -> Iterator[Trial]:
        nonlocal count, solos
        for trial in load(root):
            if not trial.sources:
                raise ValueError("a trial lacks source evidence")
            observed.update(trial.sources)
            comparison = _compare(trial)
            projection.update(comparison)
            per_arm.setdefault(trial.arm, Counter()).update(comparison)
            count += 1
            solos += len(trial.seats) == 1
            yield trial

    arms = summarize(trials())  # Also rejects a duplicate native trial key.
    if count != expected:
        raise ValueError("source count does not match the selected panel")
    if observed != sources or _fingerprints(observed) != before:
        raise ValueError("source evidence changed during replay")
    for arm, result in arms.items():
        result.update(per_arm[arm])
    return {
        "schema": "seatlab.stored-visibility/v1",
        "population": population,
        "trials": count,
        "solo_controls": solos,
        "multi_seat_trials": count - solos,
        "intervention": "mute incoming roster-peer messages for one receiver at a time",
        "source_verification": {
            "files_read": len(observed),
            "matched_before_and_after": True,
        },
        "projection": dict(projection),
        "arms": arms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("population", choices=_POPULATIONS)
    parser.add_argument(
        "root", type=Path, help="root of this source population; see replication.md"
    )
    args = parser.parse_args()
    try:
        report = replay(args.root, args.population)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        # Native errors can contain a private path or the text of a message.
        print(
            f"Replay refused the source evidence ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
