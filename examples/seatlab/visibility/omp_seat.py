"""Run one tool-free, explicitly named OMP subscription seat over local stdio."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_TURN_SCHEMA = "seatlab.seat-turn/v1"
_MAX_TURN_BYTES = 32 * 1024
_MAX_READY_BYTES = 4096
_MAX_GATEWAY_BYTES = 64 * 1024
_MAX_TEXT_BYTES = 8192
_MODEL_ID = re.compile(
    r"openai-codex/(?=[a-z0-9.-]*[0-9])[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*"
)
_ALIAS_PARTS = {"alias", "auto", "best", "default", "fallback", "latest"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _object_frame(frame: bytes, limit: int) -> dict[str, object]:
    if len(frame) > limit or not frame.endswith(b"\n") or b"\n" in frame[:-1]:
        raise ValueError
    value = json.loads(
        frame.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )
    if not isinstance(value, dict):
        raise TypeError
    return value


def _encode_frame(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _parse_turn(frame: bytes) -> str:
    value = _object_frame(frame, _MAX_TURN_BYTES)
    if set(value) != {"schema", "prompt"} or value["schema"] != _TURN_SCHEMA:
        raise ValueError
    prompt = value["prompt"]
    if not isinstance(prompt, str) or not prompt:
        raise ValueError
    prompt.encode("utf-8")
    return prompt


def _validate_model(model: str) -> None:
    if len(model) > 128 or _MODEL_ID.fullmatch(model) is None:
        raise ValueError
    if _ALIAS_PARTS.intersection(re.split(r"[.-]", model.split("/", 1)[1])):
        raise ValueError


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError
    return value


def _parse_usage(usage: object) -> dict[str, int]:
    if not isinstance(usage, dict):
        raise TypeError
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        raise TypeError
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    total = _count(usage.get("total_tokens"))
    cached = _count(details.get("cached_tokens"))
    if total != prompt + completion or cached > prompt:
        raise ValueError
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
    }


def _parse_completion(frame: bytes, model: str) -> tuple[str, dict[str, int]]:
    response = _object_frame(frame, _MAX_GATEWAY_BYTES)
    if _count(response.get("id")) != 1 or _count(response.get("status")) != 200:
        raise ValueError
    body = response.get("body")
    if (
        not isinstance(body, dict)
        or body.get("model") != model
        or body.get("error") is not None
    ):
        raise ValueError
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError
    choice = choices[0]
    if not isinstance(choice, dict) or _count(choice.get("index")) != 0:
        raise ValueError
    if choice.get("finish_reason") != "stop":
        raise ValueError
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError
    if (
        message.get("refusal") is not None
        or {"tool_calls", "function_call"} & message.keys()
    ):
        raise ValueError
    text = message.get("content")
    if (
        not isinstance(text, str)
        or not 0 < len(text.encode("utf-8")) <= _MAX_TEXT_BYTES
    ):
        raise ValueError
    return text, _parse_usage(body.get("usage"))


def _request(model: str, prompt: str) -> bytes:
    return _encode_frame(
        {
            "id": 1,
            "path": "/v1/chat/completions",
            "body": {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": "Answer only with JSON."},
                    {"role": "user", "content": prompt},
                ],
            },
        }
    )


def _complete(
    omp: Path, home: Path, model: str, prompt: str
) -> tuple[str, dict[str, int]]:
    with subprocess.Popen(
        [omp, "auth-gateway", "stdio"],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            assert process.stdin is not None and process.stdout is not None
            ready = _object_frame(
                process.stdout.readline(_MAX_READY_BYTES + 1), _MAX_READY_BYTES
            )
            if ready.get("ready") is not True or not isinstance(
                ready.get("version"), str
            ):
                raise ValueError
            process.stdin.write(_request(model, prompt))
            process.stdin.close()
            result = _parse_completion(
                process.stdout.readline(_MAX_GATEWAY_BYTES + 1), model
            )
            if process.stdout.read(1) or process.wait() != 0:
                raise ValueError
            return result
        finally:
            if process.poll() is None:
                process.kill()


@contextmanager
def _receipt_directory(path: Path | None) -> Iterator[int | None]:
    if path is None:
        yield None
        return
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError
        yield fd
    finally:
        os.close(fd)


def _write_receipt(
    directory: int, model: str, prompt: str, text: str, usage: dict[str, int]
) -> None:
    receipt = _encode_frame(
        {
            "schema": "seatlab.omp-seat-receipt/v1",
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "reply_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "served_model": model,
            "usage": usage,
        }
    )
    name = f"{secrets.token_hex(16)}.json"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(receipt)
    except OSError:
        os.unlink(name, dir_fd=directory)
        raise


class _Arguments(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError


def _run() -> None:
    parser = _Arguments(description=__doc__, allow_abbrev=False)
    parser.add_argument("--omp", required=True, type=Path)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--receipts", type=Path)
    args = parser.parse_args()
    if (
        not args.omp.is_absolute()
        or not args.home.is_absolute()
        or not args.home.is_dir()
    ):
        raise ValueError
    _validate_model(args.model)
    prompt = _parse_turn(sys.stdin.buffer.read(_MAX_TURN_BYTES + 1))
    with _receipt_directory(args.receipts) as directory:
        text, usage = _complete(args.omp, args.home, args.model, prompt)
        if directory is not None:
            _write_receipt(directory, args.model, prompt, text, usage)
    sys.stdout.buffer.write(_encode_frame({"text": text}))
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        _run()
    except (OSError, ValueError, TypeError, RecursionError, KeyboardInterrupt):
        print("seat command failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
