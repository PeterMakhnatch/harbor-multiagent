import subprocess
import sys
from pathlib import Path

from .. import calendar_case
from . import booking_worker


def _bound_first(
    path: Path, capsule: calendar_case._Capsule, request: calendar_case._Request
) -> tuple[bytes, int]:
    with calendar_case._directory(path.parent) as directory:
        frame = calendar_case._read_at(
            directory, path.name, calendar_case._MAX_LINE_BYTES
        )
    if not frame or frame.find(b"\n") != len(frame) - 1:
        raise ValueError("expected one original worker envelope")
    case_id, commitment, reply = calendar_case._worker_line(frame)
    if (case_id, commitment) != (request["id"], request["prompt_sha256"]):
        raise ValueError("original reply binding mismatch")
    observed = calendar_case._reply_evidence(reply)
    if observed[1] != capsule._native_reply_sha256:
        raise ValueError("original reply digest mismatch")
    correct = calendar_case._score(capsule.target, observed)["correct"]
    if correct is None or correct != capsule._native_reward:
        raise ValueError("original reply grade mismatch")
    return frame, correct


def _replay(case: Path, first: Path, fallback: str | None) -> dict[str, object]:
    if fallback is not None and not Path(fallback).is_absolute():
        raise ValueError("expected an absolute fallback executable")
    capsule = calendar_case._load_capsule(case)
    request = capsule.requests["checked"]
    factual = capsule.requests["factual"]
    if (
        request["prompt_sha256"] != factual["prompt_sha256"]
        or request["messages"] != factual["messages"]
    ):
        raise ValueError("checked prompt is not the factual native prompt")
    original, native_correct = _bound_first(first, capsule, request)
    fallback_calls = 0

    def receive(request_bytes: bytes) -> bytes:
        nonlocal fallback_calls
        if fallback_calls:
            raise ValueError("expected at most one fallback call")
        fallback_calls = 1
        if fallback is None:
            return original
        return booking_worker._external(fallback, request_bytes)

    routed = booking_worker.resolve(calendar_case._wire(request), receive)
    score = calendar_case._score(
        capsule.target, calendar_case._reply_evidence(routed["reply"])
    )
    return {
        "source": {
            field: capsule.source[field] for field in ("trial_uuid", "world", "arm")
        },
        "native_correct": native_correct,
        "routed": {"status": score["status"], "correct": score["correct"]},
        "route": "defer" if fallback_calls else "book",
        "fallback_calls": fallback_calls,
        "fallback_mode": "external" if fallback is not None else "recorded",
        "new_native_reward": False,
    }


def main() -> int:
    parser = calendar_case._Parser(
        allow_abbrev=False,
        description="Replay an existing team's booking decision, not a new native reward.",
    )
    parser.add_argument("case", type=Path, metavar="CASE", help="Owner case directory.")
    parser.add_argument(
        "first", type=Path, metavar="FIRST", help="Original worker JSONL row."
    )
    parser.add_argument(
        "--fallback",
        metavar="ABSOLUTE_EXECUTABLE",
        help="Use this trusted receiver instead of the original reply on defer.",
    )
    args = parser.parse_args()
    try:
        summary = _replay(args.case, args.first, args.fallback)
        sys.stdout.buffer.write(calendar_case._wire(summary))
    except (
        KeyError,
        OSError,
        RecursionError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ):
        print("Cannot replay existing calendar booking decision.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
