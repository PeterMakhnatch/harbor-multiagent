import argparse
import json
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from . import dispatch_payload, panel_sources, receiver_panel
from .visibility import calendar_protocol

_CASE_COUNT = 32
_SYSTEM = "Answer only with JSON."
_INSTRUCTIONS = (
    "The source evidence below contains an earlier action request. "
    "There are already 2 candidate whole answers to that request, listed as 1 and 2. "
    "Use the evidence to choose the better whole answer. "
    "Do not answer the earlier request directly, change a proposal, "
    "or combine the proposals. "
    'Output exactly {"pick":1} or {"pick":2}.'
)


@dataclass(frozen=True, slots=True)
class Pair:
    case: panel_sources.Case
    actions: tuple[str, str]
    reply_sha256: tuple[str, str]


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    case_id: str
    order: int
    repeat: int
    prompt: str
    prompt_sha256: str


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _read_complete(
    path: Path, cases: dict[str, panel_sources.Case]
) -> dict[str, tuple[str, str]]:
    replies = receiver_panel._read_replies([path], cases)
    if replies.keys() != cases.keys():
        missing = ", ".join(sorted(cases.keys() - replies.keys()))
        raise ValueError(f"{str(path)!r}: missing receiver replies for {missing}")
    return replies


def _native_action(case: panel_sources.Case, text: str, path: Path) -> str:
    try:
        if case.task == "calendar":
            return _json({"slot": calendar_protocol.decode_slot(text)})
        if case.task == "dispatch":
            return _json({"choices": dispatch_payload.decode_choices(text)})
        raise ValueError("unknown receiver task")
    except ValueError as exc:
        raise ValueError(f"{str(path)!r}: invalid native reply for {case.id}") from exc


def _requests(pairs: dict[str, Pair]) -> tuple[Request, ...]:
    rounds: tuple[list[Request], list[Request]] = ([], [])
    for index, (case_id, pair) in enumerate(pairs.items()):
        if pair.actions[0] == pair.actions[1]:
            continue
        proposals = tuple(json.loads(action) for action in pair.actions)
        prompts = []
        for order in (0, 1):
            payload = _json(
                {
                    "evidence": pair.case.prompt,
                    "proposals": [proposals[order], proposals[1 - order]],
                }
            )
            prompt = f"{_INSTRUCTIONS}\n\n{payload}"
            prompts.append((prompt, sha256(prompt.encode("utf-8")).hexdigest()))
        for repeat, requests in enumerate(rounds):
            lead = (index + repeat) % 2
            for order in (lead, 1 - lead):
                prompt, digest = prompts[order]
                requests.append(
                    Request(
                        id=f"pc-{index:02d}-r{repeat}-o{order}",
                        case_id=case_id,
                        order=order,
                        repeat=repeat,
                        prompt=prompt,
                        prompt_sha256=digest,
                    )
                )
    return (*rounds[0], *rounds[1])


def prepare(first: Path, second: Path) -> tuple[dict[str, Pair], tuple[Request, ...]]:
    cases = panel_sources.registry()
    if len(cases) != _CASE_COUNT:
        raise ValueError("Expected exactly 32 factual receiver cases.")
    draft0 = _read_complete(first, cases)
    draft1 = _read_complete(second, cases)
    pairs = {}
    for case_id in sorted(cases):
        case = cases[case_id]
        reply0, hash0 = draft0[case_id]
        reply1, hash1 = draft1[case_id]
        pairs[case_id] = Pair(
            case=case,
            actions=(
                _native_action(case, reply0, first),
                _native_action(case, reply1, second),
            ),
            reply_sha256=(hash0, hash1),
        )
    return pairs, _requests(pairs)


def decode_pick(text: str) -> int:
    limit = receiver_panel._MAX_REPLY_BYTES
    if type(text) is not str:
        raise ValueError("Expected a text selector reply.")
    if len(text) > limit or len(text.encode("utf-8")) > limit:
        raise ValueError("Selector reply exceeds 64 KiB in UTF-8.")
    try:
        value = json.loads(
            text,
            object_pairs_hook=receiver_panel._unique_keys,
            parse_constant=receiver_panel._reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError("Invalid JSON selector reply.") from None
    if isinstance(value, dict) and value.keys() == {"pick"}:
        pick = value["pick"]
        if type(pick) is int and pick in (1, 2):
            return pick
    raise ValueError('Expected exactly one "pick" field with integer 1 or 2.')


def _main() -> int:
    parser = argparse.ArgumentParser(description="Emit peer choice selector requests.")
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    try:
        _, requests = prepare(args.first, args.second)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for request in requests:
        row = {
            "id": request.id,
            "prompt_sha256": request.prompt_sha256,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": request.prompt},
            ],
        }
        sys.stdout.buffer.write((_json(row) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
