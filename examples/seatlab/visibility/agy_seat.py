"""One public role prompt, one fresh Antigravity session, no paid fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

MODEL = "gemini-4-carbon-low"
VERSION = "1.5.15"
MAX_STREAM = 2 * 1024 * 1024
ENV_NAMES = ("HOME", "PATH", "LANG", "TMPDIR")
USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_tokens",
    "total_tokens",
}
DENY = [
    f"{op}(*)"
    for op in (
        "command",
        "read_file",
        "write_file",
        "read_url",
        "execute_url",
        "mcp",
        "unsandboxed",
    )
]
SETTINGS = {
    "useG1Credits": False,
    "modelProvider": "",
    "allowNonWorkspaceAccess": False,
    "notifications": False,
    "toolPermission": "strict",
    "permissions": {"deny": DENY},
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        + "\n"
    ).encode()


def require(test: bool, why: str) -> None:
    if not test:
        raise ValueError(why)


def check_personal(cwd: Path, options: list[str], env: dict[str, str]) -> None:
    settings = Path(env["HOME"]) / ".gemini" / "antigravity-cli" / "settings.json"
    existing = (
        json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
    )
    require(
        not existing.get("modelProvider") and not existing.get("gcp"),
        "not a personal subscription route",
    )
    version = subprocess.run(
        [options[0], "--version"],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=4,
        check=True,
    )
    require(
        (version.stdout or version.stderr).strip() == VERSION.encode(),
        "unverified CLI version",
    )
    for query in ("/credits", "/quota"):
        result = subprocess.run(
            [*options, "--output-format", "json", "-p", query],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=6,
            check=True,
        )
        report = json.loads(result.stdout)
        require(
            report["status"] == "SUCCESS" and report["num_turns"] == 0,
            "no zero-turn admission report",
        )
        data = report["command"]["data"]
        if query == "/credits":
            require(
                report["command"]["name"] == "credits"
                and data["remaining_credits"] == 0,
                "this adapter requires zero spendable AI credits",
            )
        else:
            groups = [g for g in data["groups"] if g["name"] == "Gemini Models"]
            require(
                report["command"]["name"] == "usage" and len(groups) == 1,
                "missing Gemini subscription group",
            )
            buckets = {b["name"]: b["remaining_fraction"] for b in groups[0]["buckets"]}
            require(
                all(
                    type(buckets.get(b)) in (int, float) and 0.10 < buckets[b] <= 1.0
                    for b in ("Weekly Limit Remaining", "Five Hour Limit Remaining")
                ),
                "quota floor or unknown quota",
            )


def parse_stream(raw: bytes) -> tuple[str, dict[str, Any]]:
    require(0 < len(raw) <= MAX_STREAM, "stream size")
    events = [json.loads(row) for row in raw.splitlines()]
    require(
        3 <= len(events) <= 2048
        and events[0]["event"] == "init"
        and events[-1]["event"] == "result",
        "event order",
    )
    init, result = events[0]["init"], events[-1]["result"]
    sid = events[0]["conversation_id"]
    require(
        str(UUID(sid)) == sid and sid == result["conversation_id"], "session mismatch"
    )
    require(
        init.get("model") == MODEL and init.get("permission_mode") == "strict",
        "unexpected CLI model or permissions",
    )
    replies, users = set(), set()
    for event in events[1:-1]:
        require(event.get("event") == "step_update", "extra event")
        step = event["step_update"]
        require(
            step.get("conversation_id") == sid
            and step.get("step_type") in ("user_input", "agent_response", "checkpoint")
            and not any(
                step.get(k) for k in ("tool_info", "tool_name", "subagent_info")
            ),
            "non-text step",
        )
        if step.get("state") == "DONE" and step["step_type"] in (
            "user_input",
            "agent_response",
        ):
            (users if step["step_type"] == "user_input" else replies).add(
                step["step_index"]
            )
    require(
        len(users) == len(replies) == 1
        and result.get("num_turns") == 1
        and result.get("status") == "SUCCESS"
        and not result.get("finished_reason")
        and not result.get("error"),
        "no complete single reply",
    )
    text, usage = result["response"], result["usage"]
    require(isinstance(text, str) and 0 < len(text.encode()) <= 8192, "invalid reply")
    require(
        set(usage) == USAGE_FIELDS
        and all(type(n) is int and n >= 0 for n in usage.values())
        and usage["total_tokens"] > 0,
        "missing token report",
    )
    return text, {
        "requested_model": MODEL,
        "client_model": init["model"],
        "server_model": None,
        "session_sha256": sha(sid),
        "answer_sha256": sha(text),
        "usage": usage,
        "observed_tool_steps": 0,
    }


def answer(exe: Path, record: Path, prompt: str) -> str:
    require(
        exe.is_absolute() and exe.is_file() and os.access(exe, os.X_OK),
        "missing trusted CLI",
    )
    record.mkdir(mode=0o700, parents=False, exist_ok=False)
    env = {name: os.environ[name] for name in ENV_NAMES if os.environ.get(name)}
    require("HOME" in env, "missing owner session")
    options = [
        str(exe),
        "--model",
        MODEL,
        "--settings",
        json.dumps(SETTINGS, separators=(",", ":")),
    ]
    with tempfile.TemporaryDirectory(prefix="seatlab-agy-") as workspace:
        cwd = Path(workspace)
        check_personal(cwd, options, env)
        (record / "request.json").write_bytes(
            line({"schema": "seatlab.seat-turn/v1", "prompt": prompt})
        )
        event = line({"event": "user", "message": {"content": prompt}})
        with tempfile.TemporaryFile() as stream:
            subprocess.run(
                [
                    *options,
                    "--disable-slash-commands",
                    "--effort",
                    "low",
                    "--input-format",
                    "stream-json",
                    "--output-format",
                    "stream-json",
                    "--print-timeout",
                    "50s",
                ],
                input=event,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.DEVNULL,
                timeout=55,
                check=True,
            )
            stream.seek(0)
            text, receipt = parse_stream(stream.read(MAX_STREAM + 1))
    (record / "reply.json").write_bytes(line({"text": text}))
    receipt.update(
        cli_version=VERSION,
        prompt_sha256=sha(prompt),
        api_key_passed=False,
        ai_credit_fallback=False,
    )
    (record / "receipt.json").write_bytes(line(receipt))
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--personal-quota",
        action="store_true",
        required=True,
        help="Use only your signed-in personal Google subscription",
    )
    parser.add_argument("--agy", type=Path, required=True)
    parser.add_argument(
        "--record",
        type=Path,
        required=True,
        help="New private directory for this first attempt",
    )
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(65537)
        require(
            raw.endswith(b"\n") and len(raw) <= 65536 and raw.count(b"\n") == 1,
            "invalid turn framing",
        )
        packet = json.loads(raw)
        require(
            isinstance(packet, dict)
            and set(packet) == {"schema", "prompt"}
            and packet["schema"] == "seatlab.seat-turn/v1"
            and isinstance(packet["prompt"], str)
            and bool(packet["prompt"]),
            "invalid turn",
        )
        text = answer(args.agy, args.record, packet["prompt"])
        sys.stdout.buffer.write(line({"text": text}))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        RecursionError,
    ):
        print(
            "subscription seat did not return a verified first reply", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
