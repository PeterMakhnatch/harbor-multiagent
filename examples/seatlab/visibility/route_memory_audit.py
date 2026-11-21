from __future__ import annotations

import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import clarify_run as files
from . import route_memory as m
from . import route_memory_summary, route_packet, route_protocol, spend
from .route_audit import _READ_ERRORS, _Turn, _block, _meter, _read, _sha, _usage
from .route_run import _MAX_REPLY_BYTES, _MAX_TOKENS, _MODEL_TAG

_CLAIM = "claim.json"
_TURN_NAMES = {stage: f"{stage}.json" for stage in m.STAGES}
_ALLOWED_ARTIFACTS = {_CLAIM, *_TURN_NAMES.values()}


def _claim(fd: int | None, state: str, block: str, digest: str, eligible: bool) -> str:
    if fd is None:
        return "ineligible" if state == "missing" and not eligible else state
    if not eligible:
        return "invalid_evidence"
    try:
        if _read(fd, _CLAIM) == {
            "schema": "seatlab.memory-claim/v1",
            "block": block,
            "packet_sha256": digest,
        }:
            return "claimed"
    except FileNotFoundError:
        return "unbound_claim"
    except _READ_ERRORS:
        pass
    return "invalid_evidence"


def _bounded_usage(raw: object) -> dict[str, Any] | None:
    usage = _usage(raw)
    if usage is not None and (
        _MODEL_TAG.fullmatch(usage["served_model"]) is None
        or usage["input_tokens"] > _MAX_TOKENS
        or usage["output_tokens"] > _MAX_TOKENS
    ):
        raise ValueError("invalid metering")
    return usage


def _turn(
    fd: int, block: route_protocol.Block, digest: str, request: m.Request, prompt: str
) -> _Turn:
    expected = {
        "schema": "seatlab.memory-turn/v1",
        "block": m.key(block),
        "stage": request.stage,
        "run": m.run_id(block, request.stage),
        "role": request.role,
        "packet_sha256": digest,
        "prompt_sha256": prompt,
    }
    try:
        record = _read(fd, _TURN_NAMES[request.stage])
        if record.keys() != expected.keys() | {
            "status",
            "text",
            "response_sha256",
            "usage",
        } or any(record[k] != value for k, value in expected.items()):
            return _Turn("invalid_evidence")
        text = record["text"]
        if record["status"] == "callback_error" and (
            text is None
            and record["response_sha256"] is None
            and record["usage"] is None
        ):
            return _Turn("callback_error")
        if (
            record["status"] == "received"
            and isinstance(text, str)
            and len(text.encode("utf-8")) <= _MAX_REPLY_BYTES
            and _sha(text) == record["response_sha256"]
        ):
            return _Turn("received", text, _bounded_usage(record["usage"]))
    except FileNotFoundError:
        return _Turn("missing")
    except _READ_ERRORS:
        pass
    return _Turn("invalid_evidence")


@dataclass
class _Replay:
    claim: str
    turns: dict[str, _Turn] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    bound: set[str] = field(default_factory=set)


def _reject_unbound(fd: int, replay: _Replay) -> None:
    try:
        with os.scandir(fd) as entries:
            for entry in entries:
                if entry.name not in _ALLOWED_ARTIFACTS:
                    replay.claim = "invalid_evidence"
                    break
                if entry.name == _CLAIM:
                    continue
                stage = entry.name.removesuffix(".json")
                if stage not in replay.bound:
                    replay.turns[stage] = _Turn("unbound_child")
                    replay.claim = "invalid_evidence"
    except OSError:
        replay.claim = "invalid_evidence"


def _replay(
    fd: int | None,
    state: str,
    menu: m.Menu | None,
    block: route_protocol.Block,
    digest: str,
) -> _Replay:
    replay = _Replay(_claim(fd, state, m.key(block), digest, menu is not None))
    if menu is not None:
        script = m.steps(menu, block)
        response: str | None = None
        stopped = False
        for _ in m.STAGES:
            try:
                request = script.send(response)
            except StopIteration:
                break
            stage = request.stage
            prompt = _sha(request.prompt)
            replay.prompts[stage] = prompt
            turn = _Turn("missing" if replay.claim == "claimed" else replay.claim)
            if fd is not None and replay.claim == "claimed" and not stopped:
                replay.bound.add(stage)
                turn = _turn(fd, block, digest, request, prompt)
                stopped = turn.status == "callback_error"
            replay.turns[stage] = turn
            response = turn.text
    if fd is not None:
        _reject_unbound(fd, replay)
    for stage in m.STAGES:
        if stage in replay.turns:
            continue
        if menu is None or replay.claim != "claimed":
            status = replay.claim
        elif f"{stage[0]}-review" not in replay.prompts and any(
            replay.turns[a].status != "received" for a in ("bound", "anonymous")
        ):
            status = "unknown_selection"
        elif f"{stage[0]}-review" not in replay.prompts:
            status = "not_requested"
        else:
            status = "not_reached"
        replay.turns[stage] = _Turn(status)
    return replay


def _final(selected: str, replay: _Replay) -> tuple[str, str | None]:
    branch = selected.lower()
    review = replay.turns[f"{branch}-review"]
    final = replay.turns[f"{branch}-decide"]
    if final.text is not None:
        try:
            return "valid", route_protocol.parse_decision(final.text)
        except ValueError:
            return "invalid_decision", None
    if final.status != "not_reached":
        return final.status, None
    if review.text is not None:
        return "invalid_review", None
    return review.status, None


def _decisions(
    case: route_packet.Case,
    block: route_protocol.Block,
    menu: m.Menu | None,
    replay: _Replay,
) -> list[dict[str, Any]]:
    best = None if case.best is None else ("A", "B")[case.best ^ block.swap]
    rows: list[dict[str, Any]] = []
    for arm in m.arm_order(block):
        selector = replay.turns[arm]
        status = replay.claim
        selected = source_index = better_history = current_exact = label = None
        if menu is not None and replay.claim == "claimed":
            status = selector.status
            if selector.text is not None:
                try:
                    selected = m.parse_selection(selector.text)
                except ValueError:
                    status = "invalid_selection"
            if selected is not None:
                status, label = _final(selected, replay)
                index = int(selected == "Y")
                source_index = menu.source_indices[index]
                chosen, other = menu.history[index], menu.history[1 - index]
                if chosen != other:
                    better_history = chosen > other
                current_exact = m.exact(case, source_index)
        rows.append(
            {
                "policy": block.policy,
                "round": block.round,
                "rotation": block.rotation,
                "swap": block.swap,
                "rep": block.rep,
                "arm": arm,
                "status": status,
                "selected": selected,
                "source_index": source_index,
                "better_history": better_history,
                "current_exact": current_exact,
                "label": label,
                "best": best,
            }
        )
    return rows


def _turns(
    block: route_protocol.Block,
    replay: _Replay,
    ledger: dict[tuple[str, str], dict[str, Any]],
    available: bool,
) -> list[dict[str, Any]]:
    rows = []
    for stage in m.STAGES:
        run = m.run_id(block, stage)
        role = "route_review" if stage.endswith("-review") else "route_decide"
        turn = replay.turns[stage]
        key = run, role
        entry = ledger.pop(key, None) if stage in replay.bound else ledger.get(key)
        rows.append(
            {
                "run": run,
                "role": role,
                "status": turn.status,
                "meter": _meter(turn, entry, available),
                "prompt_sha256": replay.prompts.get(stage),
                "response_sha256": _sha(turn.text) if turn.text is not None else None,
            }
        )
    return rows


def _costs(spend_dir: Path | None) -> dict[str, Any] | None:
    if spend_dir is None:
        return None
    try:
        return spend.report(spend_dir, run_prefix="ma-")
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        raise files.StudyError("invalid_spend_evidence") from None


def _check_register(directory: int, blocks: tuple[route_protocol.Block, ...]) -> None:
    allowed = {m.key(block) for block in blocks}
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.startswith("ma-") and entry.name not in allowed:
                raise files.StudyError("unregistered_memory_artifact")


def collect(
    packet: Path, digest: str, study: Path, *, spend_dir: Path | None = None
) -> dict[str, Any]:
    cases = route_packet.load(packet, digest)
    costs = _costs(spend_dir)
    ledger = (
        {(r["run"], r["role"]): r for r in costs["calls"]} if costs is not None else {}
    )
    blocks = m.blocks()
    decisions: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    claims: Counter[str] = Counter()
    eligible = 0
    try:
        with files._directory(files._study_path(study)) as directory:
            files._private(directory, mode=0o700)
            _check_register(directory, blocks)
            for block in blocks:
                case = cases[block.policy * 6 + block.round - 1]
                menu = m.menu(cases, block)
                eligible += menu is not None
                with _block(directory, m.key(block)) as (fd, state):
                    replay = _replay(fd, state, menu, block, digest)
                claims[replay.claim] += 1
                decisions.extend(_decisions(case, block, menu, replay))
                turns.extend(_turns(block, replay, ledger, costs is not None))
    except OSError:
        raise files.StudyError("invalid_study_evidence") from None
    return {
        "schema": "seatlab.memory-report/v1",
        "packet_sha256": digest,
        "claims": dict(claims),
        "scope": (
            "New reviewer choices using archived numeric observations, not native team "
            "rewards. Missing results are not incorrect choices. A ledger match is "
            "not provider attestation. External callback costs are unknown."
        ),
        "source_rounds": len(cases),
        "eligible_blocks": eligible,
        "decisions": decisions,
        "turns": turns,
        "outcomes": route_memory_summary.summarize(decisions),
        "meter": {
            "direct_ledger_supplied": costs is not None,
            "registered_turns": len(turns),
            "by_status": dict(Counter(t["status"] for t in turns)),
            "by_match": dict(Counter(t["meter"] for t in turns)),
            "unexpected_ledger_rows": len(ledger),
            "known_usd_low": costs["known_usd_low"] if costs is not None else None,
            "upper_usd_including_holds": costs["upper_usd_including_holds"]
            if costs is not None
            else None,
        },
    }
