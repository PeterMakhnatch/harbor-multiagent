import argparse
import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any

from .peer_choice import Pair, decode_pick, prepare
from .peer_choice_score import summarize
from .receiver_panel import (
    _MAX_LINE_BYTES,
    _MAX_REPLY_BYTES,
    _open_nonblocking,
    _print_json,
    _worker_line,
)

_MAX_FILES = 128
_MAX_CASES = 128


def _read_reviews(
    files: list[Path], expected: dict[str, str]
) -> dict[str, tuple[str, int | None]]:
    observed: dict[str, tuple[str, int | None]] = {}
    for path in files:
        with open(path, "rb", opener=_open_nonblocking) as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError(f"{str(path)!r}: expected a regular review file")
            for number in range(1, len(expected) + 2):
                line = source.readline(_MAX_LINE_BYTES + 1)
                if not line:
                    break
                try:
                    review_id, commitment, reply = _worker_line(line)
                    if review_id not in expected:
                        raise ValueError("unknown review ID")
                    if review_id in observed:
                        raise ValueError("duplicate review ID")
                    if commitment != expected[review_id]:
                        raise ValueError(
                            "prompt SHA-256 does not match the review request"
                        )
                    encoded = reply.encode("utf-8")
                    if len(encoded) > _MAX_REPLY_BYTES:
                        raise ValueError("reply exceeds 64 KiB in UTF-8")
                except (ValueError, RecursionError) as exc:
                    raise ValueError(f"{str(path)!r}:{number}: {exc}") from exc
                try:
                    pick = decode_pick(reply)
                except (ValueError, RecursionError):
                    pick = None
                observed[review_id] = (sha256(encoded).hexdigest(), pick)
    return observed


def _drafts(pair: Pair) -> list[dict[str, str | int]]:
    case = pair.case
    drafts = []
    for number, (action, digest) in enumerate(
        zip(pair.actions, pair.reply_sha256, strict=True), start=1
    ):
        try:
            correct = case.score(action)
        except (ValueError, RecursionError) as exc:
            raise ValueError(
                f"{case.id}: stock scorer rejected normalized draft {number}"
            ) from exc
        if type(correct) is not int or not 0 <= correct <= case.units:
            raise ValueError(f"{case.id}: stock scorer returned an invalid unit count")
        drafts.append(
            {
                "reply_sha256": digest,
                "action_sha256": sha256(action.encode("utf-8")).hexdigest(),
                "correct": correct,
            }
        )
    return drafts


def build(first: Path, second: Path, reviews: list[Path]) -> dict[str, Any]:
    if len(reviews) > _MAX_FILES:
        raise ValueError(f"expected at most {_MAX_FILES} review files")
    pairs, requests = prepare(first, second)
    if len(pairs) > _MAX_CASES:
        raise ValueError(f"expected at most {_MAX_CASES} cases")
    observed = _read_reviews(reviews, {r.id: r.prompt_sha256 for r in requests})
    slots: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in pairs}
    for request in sorted(requests, key=lambda r: (r.order, r.repeat)):
        digest, pick = observed.get(request.id, (None, None))
        status = "missing" if digest is None else "invalid" if pick is None else "valid"
        slots[request.case_id].append(
            {
                "order": request.order,
                "repeat": request.repeat,
                "review_prompt_sha256": request.prompt_sha256,
                "reply_sha256": digest,
                "pick": pick,
                "status": status,
            }
        )
    rows = [
        {
            "id": pair.case.id,
            "task": pair.case.task,
            "world": pair.case.world,
            "units": pair.case.units,
            "source_prompt_sha256": pair.case.prompt_sha256,
            "drafts": _drafts(pair),
            "reviews": slots[case_id],
        }
        for case_id, pair in sorted(pairs.items())
    ]
    return {
        "schema": "seatlab.peer-choice-study/v1",
        "rows": rows,
        "summary": summarize(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score first replies to offline peer-choice requests.",
        allow_abbrev=False,
    )
    parser.add_argument("first", type=Path, metavar="A.jsonl")
    parser.add_argument("second", type=Path, metavar="B.jsonl")
    parser.add_argument("reviews", type=Path, nargs="+", metavar="REVIEWFILE")
    args = parser.parse_args()
    try:
        _print_json(build(args.first, args.second, args.reviews))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
