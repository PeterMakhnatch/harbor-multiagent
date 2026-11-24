from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import simulator, timing_seat, timing_study
from examples.seatlab.report_budget import advice_card, campaign, domain, study
from examples.seatlab.visibility import agy_seat
from examples.seatlab.wait_peer import receipt as owner_receipt

Record = dict[str, Any]
CLAIM = "claim.json"
CLAIM_SCHEMA = "seatlab.advice-turn-claim/v1"
TURN = "turn"
SEAT = "finance"
PURPOSE = "finance-price"
STATUSES = ("not_issued", "unanswered", "unobserved", "invalid", "priced")
TURN_KEYS = (
    "world",
    "rank",
    "arm",
    "status",
    "request",
    "request_sha256",
    "reply",
    "receipt",
    "failure",
    "pick",
    "settlement",
)
SLOTS = len(advice_card.ARMS) * len(advice_card.WORLDS)
SHARED = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

assert PURPOSE in timing_seat.ROLES and SLOTS == 16
assert set(timing_study.OWNER_FACTS) == set(timing_seat.META_KEYS)


class Integrity(RuntimeError):
    pass


def need(test: object, what: str) -> None:
    if not test:
        raise Integrity(what)


def owner_root(raw: Path) -> Path:
    root = Path(raw).expanduser().resolve(strict=True)
    need(
        timing_seat.private_parent(root),
        "the owner root must be a 0700 directory owned by this user",
    )
    need(
        root != study.ROOT and study.ROOT not in root.parents,
        "keep the owner root outside the repository",
    )
    return root


def load(args: argparse.Namespace) -> tuple[Any, list[Record]]:
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    need(stock.numpy_version == study.NUMPY, f"the pilot requires NumPy {study.NUMPY}")
    cards = advice_card.make_cards(stock, args.source.resolve(strict=True))
    need(len(list(advice_card.slots(cards))) == SLOTS, "cards do not cover 16 slots")
    return stock, cards


def senders(cards: list[Record]) -> set[str]:
    return {card["sender_session_sha256"] for card in cards}


def label(card: Record, arm: str) -> str:
    return f"rank {card['rank']} world {card['world']} {arm}"


def claim_dir(root: Path, card: Record, arm: str) -> Path:
    return root / f"r{card['rank']}-w{card['world']:02d}-{arm}"


def claim_record(card: Record, arm: str, prompt: str) -> Record:
    return {
        "schema": CLAIM_SCHEMA,
        "source_sha256": advice_card.SOURCE_SHA256,
        "rank": card["rank"],
        "world": card["world"],
        "arm": arm,
        "native_id": card["native_id"],
        "checkpoint_sha256": card["checkpoint_sha256"],
        "prompt_sha256": agy_seat.sha(prompt),
    }


def blank(card: Record, arm: str, status: str, failure: str | None) -> Record:
    return {
        "world": card["world"],
        "rank": card["rank"],
        "arm": arm,
        "status": status,
        "request": False,
        "request_sha256": None,
        "reply": None,
        "receipt": None,
        "failure": failure,
        "pick": None,
        "settlement": None,
    }


def refusal(entry: Record) -> str:
    note = entry["admission"]
    if entry["diagnostic"] == "not_admitted" and note["outcome"] in (
        "refused",
        "unavailable",
    ):
        return "admission_" + note["outcome"]
    return entry["diagnostic"]


def observe(card: Record, arm: str, claim: Path, sessions: set[str]) -> Record:
    where = label(card, arm)
    if not os.path.lexists(claim):
        return blank(card, arm, "not_issued", None)
    need(
        timing_seat.private_parent(claim), f"{where}: claim is not a private directory"
    )
    prompt = advice_card.prompt(card, arm)
    turn = claim / TURN
    if not os.path.lexists(claim / CLAIM):
        need(not os.path.lexists(turn), f"{where}: turn without a recorded claim")
        return blank(card, arm, "unobserved", "claim_incomplete")
    need(
        timing_study.load(claim / CLAIM) == claim_record(card, arm, prompt),
        f"{where}: claim conflicts with the pinned source card",
    )
    entry = timing_study.owner_entry(turn, SEAT, PURPOSE)
    if not entry["request_recorded"]:
        return blank(card, arm, "unobserved", refusal(entry))
    need(entry["admitted"], f"{where}: request recorded without admission")
    raw = owner_receipt.read(turn / PURPOSE / "request.json")
    need(
        raw == agy_seat.line({"schema": timing_seat.TURN_SCHEMA, "prompt": prompt}),
        f"{where}: recorded request is not the registered prompt",
    )
    row = blank(card, arm, "unanswered", "unanswered")
    row.update(request=True, request_sha256=agy_seat.sha(json.loads(raw)["prompt"]))
    if entry["diagnostic"] == "unanswered":
        return row
    if not entry["verified"]:
        row.update(status="unobserved", failure=entry["diagnostic"])
        return row
    return answered(row, entry, turn, prompt, sessions)


def answered(
    row: Record, entry: Record, turn: Path, prompt: str, sessions: set[str]
) -> Record:
    where = label(row, row["arm"])
    owned = owner_receipt.owner_turn(turn, PURPOSE, prompt, None)
    need(owned["status"] == "answered_unused", f"{where}: answer is not owner-verified")
    text, seat = owned["text"], owned["receipt"]
    need(
        owner_receipt.read(turn / PURPOSE / "reply.json")
        == agy_seat.line({"text": text}),
        f"{where}: raw answer differs from the owner-verified answer",
    )
    meta = {key: entry[key] for key in timing_seat.META_KEYS}
    attested = {
        "session_sha256": seat["session_sha256"],
        "prompt_sha256": seat["prompt_sha256"],
        "answer_sha256": seat["answer_sha256"],
        "requested_model": seat["requested_model"],
        "served_model": seat["server_model"],
        "client_tokens": seat["usage"]["total_tokens"],
        "tool_steps": seat["observed_tool_steps"],
        "credit_fallback": seat["ai_credit_fallback"],
    }
    need(
        meta == attested
        and meta["prompt_sha256"] == row["request_sha256"] == agy_seat.sha(prompt)
        and meta["answer_sha256"] == agy_seat.sha(text)
        and meta["served_model"] is None
        and meta["tool_steps"] == 0
        and meta["credit_fallback"] is False
        and seat["api_key_passed"] is False
        and type(meta["client_tokens"]) is int
        and meta["client_tokens"] > 0,
        f"{where}: receipt is not attested",
    )
    need(meta["session_sha256"] not in sessions, f"{where}: session reused")
    sessions.add(meta["session_sha256"])
    row.update(reply=text, receipt=meta, failure=None)
    try:
        pick = domain.read_price(text)
    except ValueError:
        row.update(status="invalid", failure="price_invalid")
        return row
    row.update(status="priced", pick=pick)
    return row


def issue(client: Path, claim: Path, card: Record, arm: str) -> str:
    prompt = advice_card.prompt(card, arm)
    os.mkdir(claim, 0o700)
    timing_study.store(claim / CLAIM, claim_record(card, arm, prompt))
    try:
        timing_seat.call(client, claim / TURN, prompt, PURPOSE)
    except timing_seat.SeatFailure as exc:
        need(exc.category != "contract", f"{label(card, arm)}: seat contract refused")
        return exc.category
    return "answered"


def issue_all(client: Path, root: Path, cards: list[Record]) -> int:
    sessions = senders(cards)
    streak = 0
    for card, arm in advice_card.slots(cards):
        claim = claim_dir(root, card, arm)
        fresh = not os.path.lexists(claim)
        outcome = issue(client, claim, card, arm) if fresh else "preexisting"
        turn = observe(card, arm, claim, sessions)
        note = timing_seat.admission(claim / TURN)
        streak = 0 if turn["request"] else streak + 1
        print(f"{label(card, arm)}: {outcome} -> {turn['status']}", flush=True)
        cause = (
            campaign.REFUSED
            if note is not None and note["outcome"] == "refused"
            else campaign.UNSENT
            if streak >= campaign.UNSENT_LIMIT
            else None
        )
        if cause is not None:
            campaign.halt(root, cause, claim.name)
            print(f"stopped: {cause}", flush=True)
            return 1
    return 0


def run(args: argparse.Namespace, cards: list[Record]) -> int:
    client = study.executable(args.client, "trusted agy")
    timing_seat.bind_host(os.environ)
    held = campaign.acquire(args.owner)
    try:
        if campaign.halted(args.owner):
            raise campaign.Refused("a durable stop is recorded for this owner root")
        return issue_all(client, args.owner, cards)
    finally:
        os.close(held)


def shared(root: Path) -> int | None:
    try:
        fd = os.open(root / campaign.LOCK, SHARED)
    except FileNotFoundError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise campaign.Refused("a run holds this owner root") from None
    return fd


def settle(
    stock: Any,
    card: Record,
    pick: int,
    companies: dict[int, Any],
    settled: dict[tuple[int, int], Record],
) -> Record:
    world, seal = card["world"], card["checkpoint_sha256"]
    if world not in companies:
        checkpoint, binding = domain.make_company(stock, world)
        need(binding["checkpoint_sha256"] == seal, f"world {world}: checkpoint drifted")
        companies[world] = checkpoint
    if (world, pick) not in settled:
        record = domain.settle(stock, companies[world], pick)
        need(
            record.keys() == domain.SETTLE_KEYS
            and record["pick"] == pick
            and record["checkpoint_sha256"] == seal,
            f"world {world} pick {pick}: settlement is not bound to the card",
        )
        settled[(world, pick)] = record
    return dict(settled[(world, pick)])


def export(stock: Any, cards: list[Record], root: Path) -> list[Record]:
    held = shared(root)
    try:
        sessions = senders(cards)
        pairs = list(advice_card.slots(cards))
        turns = [observe(c, a, claim_dir(root, c, a), sessions) for c, a in pairs]
    finally:
        if held is not None:
            os.close(held)
    companies: dict[int, Any] = {}
    settled: dict[tuple[int, int], Record] = {}
    for (card, _), turn in zip(pairs, turns, strict=True):
        if turn["status"] == "priced":
            turn["settlement"] = settle(stock, card, turn["pick"], companies, settled)
    need(
        len(turns) == SLOTS
        and all(tuple(t) == TURN_KEYS and t["status"] in STATUSES for t in turns),
        "export must hold every registered slot",
    )
    return turns


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue once or export the registered seller-advice finance turns."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "export"):
        sub = commands.add_parser(name)
        for flag in ("source", "stock", "owner"):
            sub.add_argument("--" + flag, type=Path, required=True)
        if name == "run":
            sub.add_argument("--client", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run" and not args.client.is_absolute():
        parser.error("--client must be the absolute path of the trusted agy")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    os.umask(0o077)
    args.owner = owner_root(args.owner)
    stock, cards = load(args)
    try:
        if args.command == "run":
            return run(args, cards)
        turns = export(stock, cards, args.owner)
    except campaign.Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(turns, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
