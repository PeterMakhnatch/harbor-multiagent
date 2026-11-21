"""Rebuild two role views from recorded first attempts. Never call either agent."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import clarify_run as files
from . import route_packet, route_protocol as protocol, route_summary, spend

_READ_ERRORS = (OSError, ValueError, TypeError, UnicodeError, RecursionError)
_MAX_FILE = 64 * 1024


@contextmanager
def _block(root: int, name: str) -> Generator[tuple[int | None, str]]:
    try:
        fd = os.open(name, files._DIRECTORY, dir_fd=root)
    except FileNotFoundError:
        yield None, "missing"
        return
    except OSError:
        yield None, "invalid_evidence"
        return
    try:
        try:
            files._private(fd, mode=0o700)
        except _READ_ERRORS:
            yield None, "invalid_evidence"
            return
        yield fd, "claimed"
    finally:
        os.close(fd)


def _read(fd: int, name: str) -> dict[str, Any]:
    with files._regular(fd, name, 0o600) as stream:
        raw = stream.read(_MAX_FILE + 1)
    if len(raw) > _MAX_FILE:
        raise ValueError("oversized receipt")
    return files._object(raw)


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Turn:
    status: str
    text: str | None = None
    usage: dict[str, Any] | None = None


def _claim(fd: int | None, state: str, block: protocol.Block, digest: str) -> str:
    if fd is None:
        return state
    try:
        if _read(fd, "claim.json") == {
            "schema": "seatlab.routed-claim/v1",
            "block": block.id,
            "packet_sha256": digest,
        }:
            return "claimed"
    except FileNotFoundError:
        return "unbound_claim"
    except _READ_ERRORS:
        pass
    return "invalid_evidence"


def _usage(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.keys() != {
        "served_model",
        "input_tokens",
        "cache_tokens",
        "output_tokens",
    }:
        raise ValueError("invalid metering")
    model = raw["served_model"]
    if not isinstance(model, str) or not model or len(model) > 128:
        raise ValueError("invalid metering")
    if (
        any(
            type(raw[k]) is not int or raw[k] < 0
            for k in ("input_tokens", "cache_tokens", "output_tokens")
        )
        or raw["cache_tokens"] > raw["input_tokens"]
    ):
        raise ValueError("invalid metering")
    return raw


def _turn(
    fd: int | None,
    state: str,
    block: protocol.Block,
    digest: str,
    lens: str,
    stage: str,
    prompt: str | None,
) -> _Turn:
    if fd is None or state != "claimed":
        return _Turn(state)
    try:
        record = _read(fd, f"{lens}-{stage}.json")
    except FileNotFoundError:
        return _Turn("not_reached" if prompt is None else "missing")
    except _READ_ERRORS:
        return _Turn("invalid_evidence")
    if prompt is None:
        return _Turn("unbound_child")
    expected = {
        "schema": "seatlab.routed-turn/v1",
        "block": block.id,
        "lens": lens,
        "stage": stage,
        "run": protocol.run_id(block, lens, stage),
        "role": "route_review" if stage == "review" else "route_decide",
        "packet_sha256": digest,
        "prompt_sha256": _sha(prompt),
    }
    if record.keys() != expected.keys() | {
        "status",
        "text",
        "response_sha256",
        "usage",
    } or any(record[k] != val for k, val in expected.items()):
        return _Turn("invalid_evidence")
    text = record["text"]
    if (
        record["status"] == "callback_error"
        and text is None
        and record["response_sha256"] is None
        and record["usage"] is None
    ):
        return _Turn("callback_error")
    try:
        if (
            record["status"] == "received"
            and isinstance(text, str)
            and len(text.encode("utf-8")) <= 8192
            and _sha(text) == record["response_sha256"]
        ):
            return _Turn("received", text, _usage(record["usage"]))
    except _READ_ERRORS:
        pass
    return _Turn("invalid_evidence")


def _meter(turn: _Turn, ledger: dict[str, Any] | None, available: bool) -> str:
    if not available:
        return "no_ledger_supplied"
    if ledger is None:
        return "receipt_without_ledger" if turn.usage is not None else "no_ledger_row"
    if ledger["estimated_usd_high"] is None:
        return "unsettled"
    raw = turn.usage
    if raw is None:
        return "ledger_without_receipt"
    for field in ("cache_hit", "cache_miss", "output"):
        if type(ledger[field]) is not int or ledger[field] < 0:
            return "mismatch"
    return (
        "matched"
        if files._served_identity(ledger["served_model"])
        and (
            raw["served_model"],
            raw["input_tokens"],
            raw["cache_tokens"],
            raw["output_tokens"],
        )
        == (
            ledger["served_model"],
            ledger["cache_hit"] + ledger["cache_miss"],
            ledger["cache_hit"],
            ledger["output"],
        )
        else "mismatch"
    )


def _measure(
    fd: int | None,
    claim: str,
    case: route_packet.Case,
    block: protocol.Block,
    digest: str,
    ledger: dict[tuple[str, str], dict[str, Any]],
    available: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible = case.reports is not None
    own = protocol.view(case, block, "duplicate") if eligible else None
    best = None if case.best is None else ("A", "B")[case.best ^ block.swap]
    decisions: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []

    def record(lens: str, stage: str, prompt: str | None) -> _Turn:
        turn = _turn(fd, claim, block, digest, lens, stage, prompt)
        run = protocol.run_id(block, lens, stage)
        role = "route_review" if stage == "review" else "route_decide"
        turns.append(
            {
                "run": run,
                "role": role,
                "status": turn.status,
                "meter": _meter(turn, ledger.get((run, role)), available),
                "prompt_sha256": _sha(prompt) if prompt is not None else None,
                "response_sha256": _sha(turn.text) if turn.text is not None else None,
            }
        )
        return turn

    for lens in protocol.lenses(block):
        peer = protocol.view(case, block, lens) if eligible else None
        reviewer = record(
            lens, "review", protocol.review_prompt(peer) if peer else None
        )
        review = None
        if reviewer.text is not None:
            try:
                review = protocol.parse_review(reviewer.text)
            except ValueError:
                pass
        exact = (
            None
            if peer is None or review is None
            else (review.a, review.b) == (peer.a, peer.b)
        )
        for channel in protocol.channels(block, lens):
            prompt = (
                protocol.decision_prompt(own, peer.fields, review, channel)
                if own is not None and peer is not None and review is not None
                else None
            )
            response = record(lens, channel, prompt)
            label = None
            status = response.status
            if response.text is not None:
                try:
                    label = protocol.parse_decision(response.text)
                    status = "valid"
                except ValueError:
                    status = "invalid_decision"
            elif status == "not_reached" or (status == "missing" and not eligible):
                status = (
                    "ineligible"
                    if not eligible
                    else "invalid_review"
                    if reviewer.text is not None and review is None
                    else "no_review"
                )
            decisions.append(
                {
                    "policy": block.policy,
                    "round": block.round,
                    "rotation": block.rotation,
                    "swap": block.swap,
                    "rep": block.rep,
                    "lens": lens,
                    "channel": channel,
                    "status": status,
                    "label": label,
                    "best": best,
                    "review_vote": review.vote if review else None,
                    "exact": exact,
                }
            )
    return decisions, turns


def collect(
    packet: Path, digest: str, study: Path, *, spend_dir: Path | None = None
) -> dict[str, Any]:
    """Check first-attempt evidence without reading model keys or changing the study."""
    cases = route_packet.load(packet, digest)
    costs = spend.report(spend_dir, run_prefix="rv-") if spend_dir is not None else None
    ledger = (
        {(r["run"], r["role"]): r for r in costs["calls"]} if costs is not None else {}
    )
    all_rows: list[dict[str, Any]] = []
    all_turns: list[dict[str, Any]] = []
    claims: Counter[str] = Counter()
    with files._directory(files._study_path(study)) as directory:
        files._private(directory, mode=0o700)
        for block in protocol.blocks():
            case = cases[block.policy * 6 + block.round - 1]
            with _block(directory, block.id) as (fd, state):
                claim = _claim(fd, state, block, digest)
                claims[claim] += 1
                rows, turns = _measure(
                    fd, claim, case, block, digest, ledger, costs is not None
                )
            all_rows.extend(rows)
            all_turns.extend(turns)
    expected = {(t["run"], t["role"]) for t in all_turns}
    return {
        "schema": "seatlab.routed-report/v1",
        "packet_sha256": digest,
        "scope": "New two-role decisions using archived numeric observations, not native team rewards. Missing responses are not incorrect choices.",
        "source_rounds": len(cases),
        "eligible_source_rounds": sum(c.reports is not None for c in cases),
        "distinct_source_trials": len({c.trial_sha256 for c in cases}),
        "claims": dict(claims),
        "outcomes": route_summary.summarize(all_rows),
        "meter": {
            "direct_ledger_supplied": costs is not None,
            "registered_turns": len(expected),
            "by_status": dict(Counter(t["status"] for t in all_turns)),
            "by_match": dict(Counter(t["meter"] for t in all_turns)),
            "unexpected_ledger_rows": len(ledger.keys() - expected),
            "known_usd_low": costs["known_usd_low"] if costs is not None else None,
            "upper_usd_including_holds": costs["upper_usd_including_holds"]
            if costs is not None
            else None,
        },
        "decisions": all_rows,
        "turns": all_turns,
    }
