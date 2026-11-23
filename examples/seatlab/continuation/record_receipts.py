from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
import zlib
from fractions import Fraction
from pathlib import Path
from typing import Any

SCHEMA = "seatlab.continuation-record-receipts/v1"
REGISTRATION = "examples/seatlab/continuation-study.md: Independent check: did the other seat read the message before writing a record?"
QUESTION = "Did the other seat read the message before writing a record?"
PROG = "python3.12 -BS -m examples.seatlab.continuation.record_receipts"
SOURCE = {
    "title": "shared-records artifact v2",
    "author": "Sanjana Gopnal Swamy",
    "record": "https://zenodo.org/records/24226165",
    "license": "CC BY 4.0",
}
ZIP_SHA256 = "c3a37f775376fa41b1d492655d730db906af743eace28fad8ed56ffe4dbe4177"
ZIP_BYTES = 48_350_826
ZIP_ENTRIES = 11_329
PREFIX = "coordination-shared-records-v2/data/results4/full/phase1/transcripts/"
CONFIG = "messages"
NAME = re.compile(
    r"(?P<model>[a-z0-9._-]{1,64})__messages__(?P<task>[a-z_]{1,64})"
    r"@i(?P<index>0|[1-9][0-9]{0,2})__n2__s(?P<seed>[01])\.json"
)
MODELS = {
    "claude-fable-5": "claude-fable-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
    "claude-sonnet-5": "claude-sonnet-5",
    "openrouter_google_gemini-4-argon-20261013": "openrouter:google/gemini-4-argon-20261013",
    "openrouter_openai_gpt-5.5-20260423": "openrouter:openai/gpt-5.5-20260423",
    "openrouter_qwen_qwen3.6-27b-20260422": "openrouter:qwen/qwen3.6-27b-20260422",
}
PAIRS_SHA256 = "b805436fb6340aed16423b7f5b294330f7bf43807a5254eb748f6dd88efacaa4"
PAIRS = 32
SPLITS = {"calibration": 0, "heldout": 1}
EPISODES = len(MODELS) * PAIRS
MEMBER_MAX_BYTES = 83_026
SEATS = 2
TURN_CAP = 12
MAX_CALLS = SEATS * TURN_CAP
WRITES = frozenset({"claim", "update_record"})
OK = 200
THRESHOLD = Fraction(4, 5)
MIN_MODEL_SENDS = 10
MIN_ELIGIBLE_MODELS = 3
COUNTS = (
    "episodes",
    "empty_send_episodes",
    "failed_sends",
    "sends",
    "sends_with_later_write",
    "read_before_write",
    "read_only_after_write",
    "never_read_with_write",
    "no_later_write_read",
    "no_later_write_never_read",
    "read_items_unknown_id",
    "read_items_not_matching",
)
GROUPS = ("all_read_before_write", "some_read_before_write", "none_read_before_write")
GROUP_FIELDS = ("episodes", "stale_overwrite", "non_serializable")
ERRORS = (
    OSError,
    ValueError,
    TypeError,
    LookupError,
    ArithmeticError,
    RecursionError,
    EOFError,
    zipfile.BadZipFile,
    zlib.error,
)
LIMITATIONS = (
    "order is ledger sequence order, not wall time or model claims",
    "a receipt is the intended seat's successful read_messages returning the same message ID, author and exact text; it does not prove comprehension or use",
    "sends with no later committed write by the receiver are kept outside the primary denominator, not counted as unread",
    "stale_overwrite and non_serializable groups are descriptive; selection and task difficulty can explain differences, not a causal effect of reading",
    "message text, prompts, seat names and model or machine IDs are never exported; only counts and digests",
)


class Refusal(Exception):
    pass


ReadItem = tuple[int, str, str]
Send = tuple[int, str, str, int, str]
Read = tuple[int, str, list[ReadItem]]
Write = tuple[int, str]
Grouped = tuple[str, dict[str, int]] | None


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Refusal(reason)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        _require(stat.S_ISREG(info.st_mode), "not_regular_file")
        _require(info.st_size == ZIP_BYTES, "zip_size")
        data = handle.read(ZIP_BYTES + 1)
    _require(len(data) == ZIP_BYTES, "zip_size")
    _require(_sha256(data) == ZIP_SHA256, "zip_sha256")
    return data


def _registered(
    archive: zipfile.ZipFile, seed: int
) -> list[tuple[zipfile.ZipInfo, str, str, int]]:
    infos = archive.infolist()
    _require(len(infos) == ZIP_ENTRIES, "zip_entries")
    _require(len({info.filename for info in infos}) == ZIP_ENTRIES, "duplicate_member")
    selected, pairs = [], {model: set() for model in MODELS}
    for info in infos:
        rest = info.filename.removeprefix(PREFIX)
        if rest == info.filename or f"__{CONFIG}__" not in rest:
            continue
        match = NAME.fullmatch(rest)
        if match is None:
            raise Refusal("unregistered_name")
        if int(match["seed"]) != seed:
            continue
        model, task, index = match["model"], match["task"], int(match["index"])
        _require(model in MODELS, "unregistered_model")
        pairs[model].add(f"{task}@i{index}")
        selected.append((info, model, task, index))
    _require(len(selected) == EPISODES, "episode_count")
    reference = pairs[next(iter(MODELS))]
    _require(all(found == reference for found in pairs.values()), "pairs_differ")
    _require(len(reference) == PAIRS, "pair_count")
    _require(
        _sha256("\n".join(sorted(reference)).encode()) == PAIRS_SHA256, "pairs_sha256"
    )
    return sorted(selected, key=lambda row: row[0].filename)


def _member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    _require(info.compress_type == zipfile.ZIP_DEFLATED, "member_compression")
    _require(info.flag_bits & 1 == 0, "member_encrypted")
    _require(0 < info.file_size <= MEMBER_MAX_BYTES, "member_size")
    _require(info.compress_size <= MEMBER_MAX_BYTES, "member_compressed_size")
    with archive.open(info) as handle:
        data = handle.read(MEMBER_MAX_BYTES + 1)
    _require(len(data) == info.file_size, "member_length")
    return data


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    _require(len(result) == len(pairs), "duplicate_key")
    return result


def _constant(_name: str) -> Any:
    raise Refusal("json_constant")


def _object(value: Any, reason: str) -> dict[str, Any]:
    _require(type(value) is dict, reason)
    return value


def _count(value: Any, reason: str) -> int:
    _require(type(value) is int and value >= 0, reason)
    return value


def _identity(
    episode: dict[str, Any], stem: str, model: str, task: str, index: int, seed: int
) -> dict[str, Any]:
    agents = _object(episode["agents"], "agents")
    schedule, score = (
        _object(episode["schedule"], "schedule"),
        _object(episode["score"], "score"),
    )
    _require(episode["model"] == MODELS[model], "model_mismatch")
    _require(
        episode["config"] == CONFIG and score["config"] == CONFIG, "config_mismatch"
    )
    _require(
        type(episode["n_agents"]) is int
        and episode["n_agents"] == SEATS == len(agents),
        "seats",
    )
    _require(
        episode["dry_run"] is False and episode["concurrency_aware_prompt"] is False,
        "variant",
    )
    _require(episode["episode_id"] == stem, "episode_mismatch")
    _require(
        type(schedule["seed"]) is int and schedule["seed"] == seed, "seed_mismatch"
    )
    _require(
        type(schedule["turn_cap"]) is int and schedule["turn_cap"] == TURN_CAP,
        "turn_cap",
    )
    _require(score["task"] == f"{task}@i{index}/n{SEATS}", "task_mismatch")
    flags = (score["stale_overwrite"], score["non_serializable"])
    _require(all(type(flag) is bool for flag in flags), "score_flags")
    return score


def _response(call: dict[str, Any], success: bool) -> dict[str, Any]:
    response = _object(call["response"], "response")
    _require(type(response["ok"]) is bool and response["ok"] is success, "response_ok")
    return response


def _read_items(response: dict[str, Any]) -> list[ReadItem]:
    messages = response["messages"]
    _require(type(messages) is list and len(messages) <= MAX_CALLS, "read_messages")
    items: list[ReadItem] = []
    for item in messages:
        item = _object(item, "read_item")
        author, text = item["from"], item["text"]
        _require(type(author) is str and type(text) is str, "read_item")
        items.append((_count(item["message_id"], "read_item"), author, text))
    _require(len({item[0] for item in items}) == len(items), "duplicate_read_item")
    return items


def _ledger(
    calls: Any, agents: dict[str, Any]
) -> tuple[list[Send], list[Read], list[Write], int]:
    _require(type(calls) is list and len(calls) <= MAX_CALLS, "calls")
    sends: list[Send] = []
    reads: list[Read] = []
    writes: list[Write] = []
    failed, previous = 0, -1
    turns = dict.fromkeys(agents, 0)
    for call in calls:
        call = _object(call, "call")
        seq, agent, tool, status = (
            call["seq"],
            call["agent_id"],
            call["tool"],
            call["status"],
        )
        _require(_count(seq, "seq") > previous, "seq_order")
        _require(
            type(agent) is str and agent in turns and type(tool) is str, "call_identity"
        )
        _require(type(status) is int and type(call["committed"]) is bool, "call_status")
        _require(not call["committed"] or status == OK, "committed_status")
        previous, success = seq, status == OK
        turns[agent] += 1
        _require(turns[agent] <= TURN_CAP, "turn_cap")
        if tool == "send_message":
            response = _response(call, success)
            if not success:
                failed += 1
                continue
            args = _object(call["args"], "args")
            receiver, text = args["to_agent"], args["text"]
            _require(
                type(text) is str and receiver in turns and receiver != agent, "send"
            )
            sends.append(
                (
                    seq,
                    agent,
                    receiver,
                    _count(response["message_id"], "message_id"),
                    text,
                )
            )
        elif tool == "read_messages":
            response = _response(call, success)
            if success:
                reads.append((seq, agent, _read_items(response)))
        elif tool in WRITES and call["committed"]:
            writes.append((seq, agent))
    _require(len({send[3] for send in sends}) == len(sends), "duplicate_message_id")
    return sends, reads, writes, failed


def _first_reads(
    sends: list[Send], reads: list[Read], counts: dict[str, int]
) -> dict[int, int]:
    by_id = {send[3]: send for send in sends}
    first: dict[int, int] = {}
    for seq, reader, items in reads:
        for message_id, author, text in items:
            send = by_id.get(message_id)
            if send is None:
                counts["read_items_unknown_id"] += 1
                continue
            if (
                send[0] < seq
                and send[2] == reader
                and send[1] == author
                and send[4] == text
            ):
                first.setdefault(message_id, seq)
            else:
                counts["read_items_not_matching"] += 1
    return first


def _episode(
    episode: dict[str, Any], score: dict[str, Any]
) -> tuple[dict[str, int], Grouped]:
    counts = dict.fromkeys(COUNTS, 0)
    ledger = _object(episode["ledger"], "ledger")
    sends, reads, writes, counts["failed_sends"] = _ledger(
        ledger["calls"], episode["agents"]
    )
    first = _first_reads(sends, reads, counts)
    counts["episodes"], counts["sends"] = 1, len(sends)
    counts["empty_send_episodes"] = int(not sends)
    for seq, _sender, receiver, message_id, _text in sends:
        write = next((w for w, agent in writes if w > seq and agent == receiver), None)
        read = first.get(message_id)
        if write is None:
            counts[
                "no_later_write_read"
                if read is not None
                else "no_later_write_never_read"
            ] += 1
            continue
        counts["sends_with_later_write"] += 1
        if read is None:
            counts["never_read_with_write"] += 1
        else:
            counts[
                "read_before_write" if read < write else "read_only_after_write"
            ] += 1
    eligible, before = counts["sends_with_later_write"], counts["read_before_write"]
    with_write = (
        before + counts["read_only_after_write"] + counts["never_read_with_write"]
    )
    without = counts["no_later_write_read"] + counts["no_later_write_never_read"]
    _require(
        with_write == eligible and eligible + without == len(sends), "conservation"
    )
    if eligible == 0:
        return counts, None
    group = GROUPS[0] if before == eligible else GROUPS[2] if before == 0 else GROUPS[1]
    flags = {
        "episodes": 1,
        "stale_overwrite": int(score["stale_overwrite"]),
        "non_serializable": int(score["non_serializable"]),
    }
    return counts, (group, flags)


def _empty() -> dict[str, Any]:
    return {
        "counts": dict.fromkeys(COUNTS, 0),
        "groups": {group: dict.fromkeys(GROUP_FIELDS, 0) for group in GROUPS},
    }


def _add(total: dict[str, Any], counts: dict[str, int], grouped: Grouped) -> None:
    for key in COUNTS:
        total["counts"][key] += counts[key]
    if grouped is not None:
        for key, value in grouped[1].items():
            total["groups"][grouped[0]][key] += value


def _summary(total: dict[str, Any]) -> dict[str, Any]:
    counts = total["counts"]
    part, whole = counts["read_before_write"], counts["sends_with_later_write"]
    share = Fraction(part, whole) if whole else None
    return total | {
        "read_before_write_share": None if share is None else float(share),
        "read_before_write_share_exact": None if share is None else str(share),
        "eligible": whole >= MIN_MODEL_SENDS,
        "passes": share is not None and share >= THRESHOLD,
    }


def _decision(models: list[dict[str, Any]], pooled: dict[str, Any]) -> dict[str, Any]:
    eligible = [row for row in models if row["eligible"]]
    checks = {
        "pooled_passes": pooled["passes"],
        "enough_eligible_models": len(eligible) >= MIN_ELIGIBLE_MODELS,
        "every_eligible_model_passes": all(row["passes"] for row in eligible),
    }
    return {
        "eligible_models": [row["model"] for row in eligible],
        **checks,
        "treat_sent_as_available": all(checks.values()),
        "otherwise": "require an actual read receipt",
    }


def _scan(data: bytes, seed: int) -> tuple[dict[str, dict[str, Any]], str]:
    totals = {model: _empty() for model in MODELS}
    digests = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info, model, task, index in _registered(archive, seed):
            raw = _member(archive, info)
            digests.append(f"{info.filename}\t{_sha256(raw)}")
            episode = _object(
                json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique,
                    parse_constant=_constant,
                ),
                "episode",
            )
            stem = info.filename.removeprefix(PREFIX).removesuffix(".json")
            score = _identity(episode, stem, model, task, index, seed)
            _add(totals[model], *_episode(episode, score))
    _require(
        all(total["counts"]["episodes"] == PAIRS for total in totals.values()),
        "model_episodes",
    )
    return totals, _sha256("\n".join(digests).encode())


def _design(split: str) -> dict[str, Any]:
    return {
        "cohort": f"Study 1 phase 1, config {CONFIG}, {SEATS} seats, {len(MODELS)} models x {PAIRS} task instances",
        "split": split,
        "seed": SPLITS[split],
        "receipt": "intended seat's successful read_messages returns the same message ID, author and exact text",
        "write": "intended seat's first later committed claim or update_record in ledger sequence order",
        "primary": "read_before_write / sends_with_later_write",
        "gate": {
            "threshold_exact": str(THRESHOLD),
            "min_model_sends": MIN_MODEL_SENDS,
            "min_eligible_models": MIN_ELIGIBLE_MODELS,
            "applies_to": "heldout",
        },
        "groups": "episodes with an eligible send by whether all, some or none were read before their write; descriptive only",
    }


def analyze(path: Path, split: str) -> dict[str, Any]:
    _require(split in SPLITS, "split")
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "registration": REGISTRATION,
        "question": QUESTION,
        "status": "incomplete",
        "source": SOURCE
        | {"zip_sha256": ZIP_SHA256, "pairs_sha256": PAIRS_SHA256, "prefix": PREFIX},
        "design": _design(split),
        "error": None,
        "members_sha256": None,
        "models": None,
        "pooled": None,
        "decision": None,
        "limitations": list(LIMITATIONS),
    }
    stage = "archive"
    try:
        data = _archive(Path(path))
        stage = "episodes"
        totals, members = _scan(data, SPLITS[split])
    except Refusal as error:
        return result | {"error": {"stage": stage, "reason": str(error)}}
    except ERRORS as error:
        return result | {"error": {"stage": stage, "reason": type(error).__name__}}
    result |= {"status": "complete", "members_sha256": members}
    if split != "heldout":
        return result | {
            "models": {
                "withheld": "seed 0 is format calibration only",
                "episodes": EPISODES,
            }
        }
    pooled = _empty()
    for total in totals.values():
        for key in COUNTS:
            pooled["counts"][key] += total["counts"][key]
        for group in GROUPS:
            for key in GROUP_FIELDS:
                pooled["groups"][group][key] += total["groups"][group][key]
    models = [{"model": model} | _summary(total) for model, total in totals.items()]
    pooled = _summary(pooled)
    return result | {
        "models": models,
        "pooled": pooled,
        "decision": _decision(models, pooled),
    }


def _encode(result: dict[str, Any]) -> bytes:
    return (
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Count read receipts before the receiver's next committed record write.",
        allow_abbrev=False,
    )
    parser.add_argument("release", type=Path, metavar="RELEASE_ZIP")
    parser.add_argument("--split", required=True, choices=tuple(SPLITS))
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    result = analyze(args.release, args.split)
    sys.stdout.buffer.write(_encode(result))
    sys.stdout.buffer.flush()
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
