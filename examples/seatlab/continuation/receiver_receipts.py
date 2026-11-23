from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from fractions import Fraction
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

from . import mark_transfer as mt
from . import receipt_projection, receipt_source, source

SCHEMA = "seatlab.continuation-receiver-receipts/v1"
REGISTRATION = "examples/seatlab/continuation-study.md: Does a return message prove the receiver saw the first one?"
QUESTION = "Does a return message prove the receiver saw the first one?"
PROG = "python3.12 -BS -m examples.seatlab.continuation.receiver_receipts"
SPLITS = {"calibration": (1,), "heldout": (2, 3)}
LAST_MINUTE = 230
WINDOW_S = 600
THRESHOLD = Fraction(4, 5)
MIN_MODEL_MESSAGES = 10
MIN_ELIGIBLE_MODELS = 2
MAX_MESSAGES = 100_000
MAX_RECEIPTS = 100_000
MAX_MEMBERS = 10_000
SEATS = 2
LOAD_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    LookupError,
    ArithmeticError,
    AttributeError,
    RecursionError,
)
KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
OUTCOMES = (
    "confirmed_before_reply",
    "same_second_unknown_order",
    "first_bound_after_reverse",
    "no_visible_receipt",
)
UNANSWERED = (
    "receipt_within_window",
    "first_bound_after_window",
    "no_visible_receipt",
)
LIMITATIONS = (
    "a visible receipt is the receiver's own successful board tool return containing the original; it does not prove comprehension, action or a reward effect",
    "shortened tool output and unseen tools leave a receipt unknown; no_visible_receipt is not evidence the receiver missed the message",
    "board time has 1-second resolution; a receipt in the same second as the reverse post has unknown order and is not counted as before it",
    "the proportion is a lower bound on observation before the reverse post, not an estimate of it",
    "a later upper bound does not prove the receipt happened late",
    "receipt upper bounds earlier than the original post are inconsistent and are ignored",
    "messages, receiver observations and seat names are never published; only counts and public file digests",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _int(value: Any, label: str) -> int:
    _require(type(value) is int and value >= 0, f"{label}: not a non-negative integer")
    return value


def _minute(value: Any, label: str) -> Fraction:
    _require(type(value) in (int, float), f"{label}: not a number")
    _require(math.isfinite(value) and value >= 0, f"{label}: not a finite minute")
    return Fraction(value)


def _message(item: Any, at: str) -> dict[str, Any]:
    _require(type(item) is dict, f"{at}: not an object")
    _require(
        {"row", "direction", "sent_s", "minute", "read_s"} <= item.keys(),
        f"{at}: missing fields",
    )
    direction = _int(item["direction"], f"{at}.direction")
    _require(direction < SEATS, f"{at}.direction: not 0 or 1")
    reads = item["read_s"]
    _require(
        type(reads) is list and len(reads) <= MAX_RECEIPTS, f"{at}.read_s: bad list"
    )
    return {
        "row": _int(item["row"], f"{at}.row"),
        "direction": direction,
        "sent_s": _int(item["sent_s"], f"{at}.sent_s"),
        "minute": _minute(item["minute"], f"{at}.minute"),
        "read_s": [_int(read, f"{at}.read_s") for read in reads],
    }


def _diagnostics(value: Any) -> dict[str, int]:
    _require(type(value) is dict and len(value) <= 256, "diagnostics: bad object")
    result = {}
    frames = value.get("framing")
    _require(
        type(frames) is list and len(frames) == SEATS, "diagnostics: bad seat framing"
    )
    labels = {
        "NONE": "seats_no_board_reads",
        "UNKNOWN": "seats_unknown_framing",
        "claude-code": "seats_claude_code",
        "antigravity-cli": "seats_antigravity_cli",
    }
    for frame in frames:
        _require(
            type(frame) is str and frame in labels, "diagnostics: unknown framing label"
        )
        label = labels[frame]
        result[label] = result.get(label, 0) + 1
    for key, count in value.items():
        if key == "framing":
            continue
        _require(
            type(key) is str and KEY_PATTERN.fullmatch(key) is not None,
            "diagnostics: unstable key",
        )
        result[key] = _int(count, "diagnostics value")
    return dict(sorted(result.items()))


def _projected(value: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    _require(
        type(value) is dict and {"messages", "diagnostics"} <= value.keys(),
        "projection: bad shape",
    )
    items = value["messages"]
    _require(
        type(items) is list and len(items) <= MAX_MESSAGES, "projection: bad messages"
    )
    messages = [_message(item, f"message[{n}]") for n, item in enumerate(items)]
    rows = [message["row"] for message in messages]
    _require(len(set(rows)) == len(rows), "projection: board row counted twice")
    messages.sort(key=lambda message: message["row"])
    _require(
        all(
            earlier["sent_s"] <= later["sent_s"]
            for earlier, later in pairwise(messages)
        ),
        "projection: board time decreases along board rows",
    )
    return messages, _diagnostics(value["diagnostics"])


def _sources(members: Any) -> list[dict[str, str]]:
    _require(
        type(members) is dict and 0 < len(members) <= MAX_MEMBERS,
        "sources: bad member map",
    )
    rows = []
    for member, digest in members.items():
        _require(type(member) is str and type(digest) is str, "sources: bad entry")
        path = PurePosixPath(member)
        _require(
            member == str(path)
            and not path.is_absolute()
            and ".." not in path.parts
            and "\\" not in member
            and member.isprintable(),
            "sources: member is not a relative public path",
        )
        _require(DIGEST_PATTERN.fullmatch(digest) is not None, "sources: bad sha256")
        rows.append({"member": member, "sha256": digest})
    return sorted(rows, key=lambda row: row["member"])


def _run_id(run: Any, model: str, rollout: int) -> str:
    _require(
        type(run) is str and RUN_ID_PATTERN.fullmatch(run) is not None,
        "run: bad public id",
    )
    _require(
        run == f"{model}-k{SEATS}-r{rollout}", "run: id does not match the selection"
    )
    return run


def _replies(messages: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    following: list[dict[str, Any] | None] = [None] * SEATS
    replies: list[dict[str, Any] | None] = [None] * len(messages)
    for index in range(len(messages) - 1, -1, -1):
        original = messages[index]
        later = following[1 - original["direction"]]
        if later is not None and later["sent_s"] - original["sent_s"] <= WINDOW_S:
            replies[index] = later
        following[original["direction"]] = original
    return replies


def _empty() -> dict[str, Any]:
    return {
        "board_messages": 0,
        "after_last_minute": 0,
        "primary_messages": 0,
        "qualifying_messages": 0,
        "inconsistent_receipt_bounds": 0,
        "qualifying": dict.fromkeys(OUTCOMES, 0),
        "without_timely_reply": dict.fromkeys(UNANSWERED, 0),
        "without_timely_reply_messages": 0,
    }


def _classify(
    original: dict[str, Any], reply: dict[str, Any] | None
) -> tuple[str, str, int]:
    sent = original["sent_s"]
    valid = [read for read in original["read_s"] if read >= sent]
    ignored = len(original["read_s"]) - len(valid)
    first = min(valid) if valid else None
    if reply is None:
        if first is None:
            return "without_timely_reply", "no_visible_receipt", ignored
        within = first - sent <= WINDOW_S
        return "without_timely_reply", UNANSWERED[0 if within else 1], ignored
    if first is None:
        return "qualifying", "no_visible_receipt", ignored
    if first < reply["sent_s"]:
        return "qualifying", "confirmed_before_reply", ignored
    if first == reply["sent_s"]:
        return "qualifying", "same_second_unknown_order", ignored
    return "qualifying", "first_bound_after_reverse", ignored


def _count(messages: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _empty()
    counts["board_messages"] = len(messages)
    replies = _replies(messages)
    for original, reply in zip(messages, replies, strict=True):
        if original["minute"] > LAST_MINUTE:
            counts["after_last_minute"] += 1
            continue
        counts["primary_messages"] += 1
        group, outcome, ignored = _classify(original, reply)
        counts[group][outcome] += 1
        counts["inconsistent_receipt_bounds"] += ignored
    counts["qualifying_messages"] = sum(counts["qualifying"].values())
    counts["without_timely_reply_messages"] = sum(
        counts["without_timely_reply"].values()
    )
    _require(
        counts["qualifying_messages"] + counts["without_timely_reply_messages"]
        == counts["primary_messages"],
        "counts: primary messages not partitioned",
    )
    _require(
        counts["primary_messages"] + counts["after_last_minute"]
        == counts["board_messages"],
        "counts: board messages not partitioned",
    )
    return counts


def _add(total: dict[str, Any], part: dict[str, Any]) -> None:
    for key, value in part.items():
        if type(value) is dict:
            _add(total[key], value)
        else:
            total[key] += value


def _share(name: str, part: int, whole: int) -> dict[str, Any]:
    if whole == 0:
        return {name: None, f"{name}_exact": None}
    value = Fraction(part, whole)
    return {name: float(value), f"{name}_exact": str(value)}


def _bounds(counts: dict[str, Any]) -> dict[str, Any]:
    qualifying, n = counts["qualifying"], counts["qualifying_messages"]
    confirmed = qualifying["confirmed_before_reply"]
    tied = qualifying["same_second_unknown_order"]
    return (
        _share("lower_bound", confirmed, n)
        | _share("with_same_second", confirmed + tied, n)
        | _share("timely_reply_coverage", n, counts["primary_messages"])
    )


def _summary(counts: dict[str, Any], diagnostics: dict[str, int]) -> dict[str, Any]:
    return {"counts": counts, "bounds": _bounds(counts), "diagnostics": diagnostics}


def _load(cache: Path, model: str, rollout: int) -> dict[str, Any]:
    row: dict[str, Any] = {"model": model, "rollout": rollout}
    stage = "source"
    try:
        run, board, seats, members = receipt_source.load_run(cache, model, rollout)
        stage = "source_metadata"
        run_id, sources = _run_id(run, model, rollout), _sources(members)
        _require(type(seats) is list and len(seats) == SEATS, "source: not two seats")
        stage = "projection"
        projected = receipt_projection.project(board, seats)
        stage = "analysis"
        messages, diagnostics = _projected(projected)
        counts = _count(messages)
    except LOAD_ERRORS as exc:
        return row | {
            "status": "unavailable",
            "stage": stage,
            "error": type(exc).__name__,
        }
    return (
        row
        | {"status": "complete", "run_id": run_id, "sources": sources}
        | _summary(counts, diagnostics)
    )


def _merge(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts, diagnostics = _empty(), {}
    for row in rows:
        _add(counts, row["counts"])
        for key, value in row["diagnostics"].items():
            diagnostics[key] = diagnostics.get(key, 0) + value
    return _summary(counts, dict(sorted(diagnostics.items())))


def _passes(counts: dict[str, Any]) -> bool:
    n = counts["qualifying_messages"]
    confirmed = counts["qualifying"]["confirmed_before_reply"]
    return n > 0 and Fraction(confirmed, n) >= THRESHOLD


def _eligible(row: dict[str, Any]) -> bool:
    return row["counts"]["qualifying_messages"] >= MIN_MODEL_MESSAGES


def _decision(
    split: str, models: list[dict[str, Any]], pooled: dict[str, Any]
) -> dict[str, Any]:
    eligible = [row for row in models if row["eligible"]]
    counted = {
        "eligible_model_count": len(eligible),
        "eligible_models": [row["model"] for row in eligible],
    }
    if split != "heldout":
        return {
            "applies": False,
            "reason": "rollout 1 is format calibration only",
        } | counted
    passing = [row["model"] for row in eligible if _passes(row["counts"])]
    checks = {
        "pooled_passes": _passes(pooled["counts"]),
        "enough_eligible_models": len(eligible) >= MIN_ELIGIBLE_MODELS,
        "every_eligible_model_passes": len(passing) == len(eligible),
    }
    return {
        "applies": True,
        **counted,
        "eligible_models_passing": passing,
        **checks,
        "recommend_proxy": all(checks.values()),
    }


def _sources_digest(runs: list[dict[str, Any]]) -> str:
    bound = [[row["run_id"], row["sources"]] for row in runs]
    return hashlib.sha256(mt._encode({"runs": bound})).hexdigest()


def _design(split: str) -> dict[str, Any]:
    return {
        "cohort": "9 released two-seat communicating seed-to-profit runs: 3 models x 3 rollouts",
        "split": split,
        "rollouts": list(SPLITS[split]),
        "primary": f"each board message once, posted at or before minute {LAST_MINUTE}",
        "reply": f"first later board row from the other seat within {WINDOW_S} s of board time",
        "board_time": "nondecreasing along board rows; any decrease makes the run unavailable",
        "receipt": "earliest receipt upper bound at or after the original; earlier bounds are ignored",
        "confirmed": "receipt strictly before the reverse post; the same second is unknown order",
        "without_reply": f"first receipt bound within {WINDOW_S} s or later, reported separately",
        "gate": {
            "threshold": float(THRESHOLD),
            "threshold_exact": str(THRESHOLD),
            "min_model_messages": MIN_MODEL_MESSAGES,
            "min_eligible_models": MIN_ELIGIBLE_MODELS,
            "applies_to": "heldout",
        },
        "inference": "descriptive",
    }


def analyze(cache: Path, split: str) -> dict[str, Any]:
    _require(split in SPLITS, "split: unknown")
    cache = Path(cache).resolve()
    runs = [
        _load(cache, model, rollout)
        for model in source.MODELS
        for rollout in SPLITS[split]
    ]
    complete = all(row["status"] == "complete" for row in runs)
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "registration": REGISTRATION,
        "question": QUESTION,
        "status": "complete" if complete else "incomplete",
        "design": _design(split),
        "runs": runs,
        "models": None,
        "pooled": None,
        "decision": None,
        "sources_sha256": None,
        "limitations": list(LIMITATIONS),
    }
    if not complete:
        return result
    models = []
    for model in source.MODELS:
        merged = _merge([row for row in runs if row["model"] == model])
        models.append({"model": model, "eligible": _eligible(merged)} | merged)
    pooled = _merge(runs)
    result |= {
        "models": models,
        "pooled": pooled,
        "decision": _decision(split, models, pooled),
        "sources_sha256": _sources_digest(runs),
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Count board-return receipts before the reverse message on two-seat runs.",
        allow_abbrev=False,
    )
    parser.add_argument("cache", type=Path, metavar="CACHE")
    parser.add_argument("--split", required=True, choices=tuple(SPLITS))
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    result = analyze(args.cache, args.split)
    sys.stdout.buffer.write(mt._encode(result))
    sys.stdout.buffer.flush()
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
