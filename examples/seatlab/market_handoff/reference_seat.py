import hashlib
import json
import re
import sys
from typing import Any, NoReturn

MAX_INPUT_BYTES = 1 << 22
MAX_PACKET_LINES = 24
TURN_SCHEMA = "seatlab.seat-turn/v1"
CASE = re.compile(r"cont-[0-9a-f]{24}")
OPENERS = {
    "sales": "You are the sales analyst of ",
    "finance": "You are the finance director of ",
}
RULE = (
    r'Use no tools and add no notes\. Reply with only the JSON object \{"pick": N\}, '
    r"where N is 1, 2, or 3\."
)
CLOSERS = {
    "sales": re.compile(
        r"Your recommendation goes to the finance director, who makes the final "
        r"choice\. " + RULE
    ),
    "finance": re.compile(
        r"The sales analyst recommends option ([123])\. "
        r"You make the final choice\. " + RULE
    ),
}
POLICIES = {"keep": 1, "discount": 3, "follow-sales": None}


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    return (
        dict(pairs)
        if len({key for key, _ in pairs}) == len(pairs)
        else fail("duplicate key")
    )


def choose(policy: str, prompt: object) -> int:
    assert policy in POLICIES
    if not isinstance(prompt, str) or "\n" not in prompt:
        fail("absent market prompt")
    first, last = prompt.split("\n", 1)[0], prompt.rsplit("\n", 1)[1]
    matches = [
        (opener, CLOSERS[role].fullmatch(last)) for role, opener in OPENERS.items()
    ]
    found = [match for opener, match in matches if match and first.startswith(opener)]
    if len(found) != 1:
        fail("prompt has no recognized market role")
    advice = found[0].group(1) if found[0].re is CLOSERS["finance"] else None
    pick = POLICIES[policy] or (
        int(advice) if advice else fail("follow-sales needs finance")
    )
    assert pick in (1, 2, 3)
    return pick


def read_lines(limit: int) -> list[Any]:
    assert 1 <= limit <= MAX_PACKET_LINES
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if (
        len(data) > MAX_INPUT_BYTES
        or not data.endswith(b"\n")
        or data.count(b"\n") > limit
    ):
        fail(
            f"input must be at most {limit} newline-terminated lines and {MAX_INPUT_BYTES} bytes"
        )
    lines = data.decode("utf-8")[:-1].split("\n")
    assert 1 <= len(lines) <= limit
    return [
        json.loads(line, object_pairs_hook=_unique, parse_constant=fail)
        for line in lines
    ]


def run_packet(policy: str) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for number, record in enumerate(read_lines(MAX_PACKET_LINES), 1):
        keys = record.keys() if isinstance(record, dict) else set()
        if keys != {"id", "prompt_sha256", "prompt"} or any(
            not isinstance(value, str) for value in record.values()
        ):
            fail(
                f"line {number}: expected exactly string id, prompt_sha256, and prompt"
            )
        case, prompt = record["id"], record["prompt"]
        if not CASE.fullmatch(case) or case in seen:
            fail(f"line {number}: malformed or duplicate request id")
        if (
            record["prompt_sha256"]
            != hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        ):
            fail(f"line {number}: prompt digest mismatch")
        seen.add(case)
        reply = canonical({"pick": choose(policy, prompt)})
        out.append(
            canonical(
                {"id": case, "prompt_sha256": record["prompt_sha256"], "reply": reply}
            )
            + "\n"
        )
    return "".join(out)


def run_turn(policy: str) -> str:
    frame = read_lines(1)[0]
    keys = frame.keys() if isinstance(frame, dict) else set()
    if keys != {"schema", "prompt"} or frame["schema"] != TURN_SCHEMA:
        fail("turn is not a seatlab.seat-turn/v1 frame")
    return (
        canonical({"text": canonical({"pick": choose(policy, frame["prompt"])})}) + "\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("packet", "turn") or argv[1] not in POLICIES:
        sys.stderr.write(
            "usage: reference_seat.py packet|turn keep|discount|follow-sales\n"
        )
        return 2
    try:
        result = run_packet(argv[1]) if argv[0] == "packet" else run_turn(argv[1])
    except (ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {type(exc).__name__}: {exc}\n")
        return 2
    sys.stdout.write(result)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
