import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...native_study import require
from ..decision_boundary import revised_temporal as rt
from .model import Episode, Selector

SELECTOR = Selector("GET", "/inbox", ("reports",), "slot")
SENDERS = ("truthful", "repeat")
FRAME_FIELDS = {"method", "path", "body", "status", "reply"}


@dataclass(frozen=True)
class _Launch:
    history: tuple[dict[str, Any], ...]
    refresh: dict[str, Any] | None
    reward: int


def _normalize(
    frames: tuple[dict[str, Any], ...], hidden: frozenset[str]
) -> tuple[dict[str, Any], ...]:
    aliases: dict[str, str] = {}
    receipts: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    scrubbed: list[dict[str, Any]] = []
    for frame in frames:
        require(set(frame) == FRAME_FIELDS, "unexpected frame fields")
        body, reply = frame["body"], frame["reply"]
        clean_body, clean_reply = body, reply
        route = (frame["method"], frame["path"])
        if route == ("GET", "/inbox"):
            require(body is None, "inbox request body")
            require(type(reply) is dict and set(reply) == {"reports"}, "inbox reply")
            reports = reply["reports"]
            require(type(reports) is list, "inbox reports")
            aliased, clean = [], []
            for report in reports:
                require(
                    type(report) is dict and set(report) == rt.REPORT_FIELDS,
                    "report fields",
                )
                phase, receipt = report["phase"], report["receipt"]
                require(
                    phase in rt.PHASES and report["slot"] in rt.SLOTS,
                    "report values",
                )
                require(type(receipt) is str and bool(receipt), "invalid receipt")
                require(receipt not in hidden, "hidden identifier used as receipt")
                if phase in receipts:
                    require(receipts[phase] == receipt, "receipt changed between reads")
                else:
                    require(receipt not in aliases, "receipt reused across phases")
                    receipts[phase] = receipt
                    aliases[receipt] = f"receipt-{len(aliases) + 1}"
                aliased.append({**report, "receipt": aliases[receipt]})
                clean.append({**report, "receipt": None})
            require(
                [r["phase"] for r in reports] == list(rt.PHASES[: len(reports)]),
                "inbox report order",
            )
            reply, clean_reply = {"reports": aliased}, {"reports": clean}
        elif route == ("POST", "/ack"):
            require(
                type(body) is dict and set(body) == {"phase", "receipt"}, "ack body"
            )
            phase, receipt = body["phase"], body["receipt"]
            require(
                phase in rt.PHASES and phase in receipts and receipts[phase] == receipt,
                "unmatched ack receipt",
            )
            body = {**body, "receipt": aliases[receipt]}
            clean_body = {**body, "receipt": None}
        else:
            require(route == ("POST", "/draft"), "unexpected reader route")
        normalized.append({**frame, "body": body, "reply": reply})
        scrubbed.append({**frame, "body": clean_body, "reply": clean_reply})
    rest = json.dumps(scrubbed, sort_keys=True)
    require(
        not any(raw in rest for raw in aliases), "raw receipt outside receipt positions"
    )
    require(
        not any(raw in rest for raw in hidden), "hidden identifier in reader frames"
    )
    require(
        not any(
            alias in rest or alias in aliases or alias in hidden
            for alias in aliases.values()
        ),
        "receipt alias collision",
    )
    return tuple(normalized)


def _play(
    world: Any, sender: str, slot: str, reread: bool
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any] | None]:
    frames: list[dict[str, Any]] = []

    def call(
        role: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw = None if body is None else json.dumps(body).encode("ascii")
        status, reply = world.handle(method, path, f"Bearer {world.tokens[role]}", raw)
        require(status == 200, f"{role} {method} {path} refused: {status}")
        if role == rt.DECIDER:
            frames.append(
                {
                    "method": method,
                    "path": path,
                    "body": body,
                    "status": status,
                    "reply": reply,
                }
            )
        return reply

    def ack(report: dict[str, Any]) -> None:
        call(
            rt.DECIDER,
            "POST",
            "/ack",
            {"phase": report["phase"], "receipt": report["receipt"]},
        )

    initial = call("reporter", "GET", "/brief")
    require(initial["phase"] == "initial", "initial report is not open")
    call("reporter", "POST", "/report", {"phase": "initial", "slot": initial["slot"]})
    first = call(*rt.INBOX)["reports"]
    require([r["phase"] for r in first] == ["initial"], "initial inbox")
    ack(first[0])
    call(rt.DECIDER, "POST", "/draft", {"slot": first[0]["slot"]})
    revision = call("reporter", "GET", "/brief")
    require(revision["phase"] == "revision", "revision report is not open")
    reported_slot = revision["slot"] if sender == "truthful" else initial["slot"]
    call("reporter", "POST", "/report", {"phase": "revision", "slot": reported_slot})
    both = call(*rt.INBOX)["reports"]
    require([r["phase"] for r in both] == list(rt.PHASES), "complete inbox")
    require(
        both[0] == first[0] and both[1]["slot"] == reported_slot,
        "inbox differs from sent reports",
    )
    ack(both[1])
    end = len(frames)
    if reread:
        call(*rt.INBOX)
    decision = tuple(frames)
    call(rt.DECIDER, "POST", "/commit", {"slot": slot})
    normalized = _normalize(decision, frozenset({world.run, *world.tokens.values()}))
    return normalized[:end], normalized[end] if reread else None


def _launch(
    harbor: SimpleNamespace,
    variant: str,
    seed: int,
    sender: str,
    slot: str,
    reread: bool,
) -> _Launch:
    with tempfile.TemporaryDirectory(prefix="receiver-memory-") as scratch:
        world = harbor.World(Path(scratch), variant, seed)
        try:
            history, refresh = _play(world, sender, slot, reread)
            cut = world.freeze()
            log, state = world.log_path.read_bytes(), world.state_path.read_bytes()
        finally:
            world.close()
    try:
        grade = harbor.grade(
            log,
            state,
            harbor.Cut(cut["run"], cut["events"]),
            harbor.Fixture(variant, seed),
        )
    except harbor.Refused as error:
        raise ValueError(f"native grade refused the launch: {error}") from None
    require(
        grade.reward in (0, 1) and grade.decision["run"] == cut["run"], "native grade"
    )
    require(
        grade.decision["event_counts"]
        == {"sent": 2, "returned": 2, "acknowledged": 2, "commit": 1},
        "incomplete native handoff",
    )
    require(
        grade.decision["commit_matches_acknowledged_revision"] is not None,
        "revision not acknowledged before commit",
    )
    require(
        grade.decision["revision_correct"]
        == (sender == "truthful" or variant == "confirm"),
        "native sender control",
    )
    return _Launch(history, refresh, grade.reward)


def cases(seed: int, sender: str) -> list[Episode]:
    require(type(seed) is int and 0 <= seed <= rt.MAX_SEED, "invalid seed")
    require(sender in SENDERS, "invalid sender")
    harbor = rt.native()
    instruction = (rt.TASK / "instructions" / f"{rt.DECIDER}.md").read_text(
        encoding="utf-8"
    )
    episodes: list[Episode] = []
    for variant in rt.VARIANTS:
        immediate = {
            slot: _launch(harbor, variant, seed, sender, slot, False)
            for slot in rt.SLOTS
        }
        refreshed = {
            slot: _launch(harbor, variant, seed, sender, slot, True)
            for slot in rt.SLOTS
        }
        history = immediate[rt.SLOTS[0]].history
        refresh = refreshed[rt.SLOTS[0]].refresh
        if refresh is None or refresh != history[-2]:
            raise ValueError("reread differs from complete inbox")
        require(
            all(
                run.history == history
                for run in (*immediate.values(), *refreshed.values())
            ),
            "native decision prefixes differ",
        )
        require(
            all(run.refresh == refresh for run in refreshed.values()),
            "native refresh frames differ",
        )
        scores = {slot: run.reward for slot, run in immediate.items()}
        require(
            scores == {slot: run.reward for slot, run in refreshed.items()}
            and sum(scores.values()) == 1,
            "native score vectors differ or lack a unique winner",
        )
        episodes.append(Episode(variant, 1, instruction, history, refresh, scores))
    require(
        episodes[0].history[-1] == episodes[1].history[-1],
        "current acknowledgment differs across worlds",
    )
    return episodes
