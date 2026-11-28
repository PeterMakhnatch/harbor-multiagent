import hashlib
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ...native_study import require
from ..decision_boundary.core import World, analyze
from ..decision_boundary.pair import compare, partition
from .native import Session, encode_reply

SCHEMA = "seatlab.dispatch-recall.certificate/v1"
MAX_SEED = 2**63 - 1
SILENT = "--"
TRANSITIONS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
SENDERS = ("truthful", "wrong", "silent")
LOADERS = ("follow", "stay", "switch")
WINDOWS = ("commit", "close")
CUT_KEYS = frozenset(
    {
        "schema",
        "seed",
        "transition",
        "timing",
        "bays",
        "initial_bay",
        "current_bay",
        "actions",
        "windows",
        "reward",
    }
)
_EXPECTED_DECISION = {
    "truthful": {"immediate": "1", "deferred": "1/2", "withheld": "1/2"},
    "wrong": {"immediate": "1", "deferred": "1/2", "withheld": "1/2"},
    "silent": {"immediate": "1/2", "deferred": "1/2", "withheld": "1/2"},
}
_EXPECTED_FOLLOW = {
    "truthful": {"immediate": "1", "deferred": "1/2", "withheld": "1/2"},
    "wrong": {"immediate": "0", "deferred": "1/2", "withheld": "1/2"},
    "silent": {"immediate": "1/2", "deferred": "1/2", "withheld": "1/2"},
}
_HINDSIGHT_SPLIT = {
    "truthful": {"immediate": True, "deferred": True, "withheld": False},
    "wrong": {"immediate": True, "deferred": True, "withheld": False},
    "silent": {"immediate": False, "deferred": False, "withheld": False},
}
_SCOPE = (
    "One held seed, two equally likely transitions, staged initial bay, fixed "
    "depot sender rules, injected phase tokens; every legal commit graded by the "
    "native world snapshot. Byte proof covers engine replies only (encode_reply of "
    "Session.view/apply), not role instructions or host/client request bytes. "
    "Attests only the fixed native request schedule; not arbitrary repeat polling "
    "or wall-clock timing, provider text or tokenization, or a real free team. "
    "Decision values use replies up to the commit window; later replies are "
    "hindsight and close after commit never changes the score."
)
_POOLED = ("truthful", "wrong")


def phase_tickets(seed: int) -> tuple[str, str]:
    require(type(seed) is int and 0 <= seed <= MAX_SEED, "invalid held seed")
    tickets = tuple(
        hashlib.sha256(f"{SCHEMA}:{seed}:{window}".encode("ascii")).hexdigest()[:32]
        for window in WINDOWS
    )
    require(tickets[0] != tickets[1], "phase tokens collide")
    return tickets[0], tickets[1]


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class _Loader:
    session: Any
    lines: list[bytes] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        self.requests.append({"op": "view"})
        data = self.session.view("loader")
        line = encode_reply(data)
        require(
            encode_reply(self.session.view("loader")) == line,
            "repeated loader view is not idempotent on the wire",
        )
        self.lines.append(line)
        require(b"current_bay" not in line, "loader line exposes the current bay")
        return data

    def act(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(request))
        data = self.session.apply("loader", request)
        self.lines.append(encode_reply(data))
        return data


def _report(sender: str, view: dict[str, Any], bays: tuple[str, ...]) -> str | None:
    current = view["current_bay"]
    require(current in bays, "depot current bay is not on the menu")
    if sender == "truthful":
        return current
    if sender == "wrong":
        return next(bay for bay in bays if bay != current)
    require(sender == "silent", "unknown sender rule")
    return None


def _first(loader: _Loader) -> tuple[tuple[str, ...], str]:
    first = loader.view()
    require(
        first["turn"] == "stage" and first["active"] is True,
        "loader does not open on stage",
    )
    require(
        not {"notice", "ticket", "transition", "timing"} & first.keys(),
        "stage view leaks a window or arm",
    )
    bays = tuple(first["bays"])
    initial = first["initial_bay"]
    require(
        len(bays) == 2 and len(set(bays)) == 2 and initial in bays,
        "menu is not two distinct bays holding the initial bay",
    )
    require(
        all(type(bay) is str and len(bay) == 2 for bay in bays) and SILENT not in bays,
        "bay names are not fixed-width two characters",
    )
    return bays, initial


def _depot(session: Any, sender: str, bays: tuple[str, ...]) -> tuple[str, str | None]:
    view = session.view("depot")
    require(
        view["turn"] == "report" and view["active"] is True,
        "depot does not own the report turn",
    )
    require(
        not {"initial_bay", "transition", "timing", "notice", "ticket"} & view.keys(),
        "depot view leaks the arm",
    )
    report = _report(sender, view, bays)
    session.apply("depot", {"op": "report", "bay": report})
    return view["current_bay"], report


def _notice(timing: str, window: str, report: str | None) -> str:
    eligible = {"immediate": WINDOWS, "deferred": ("close",), "withheld": ()}
    if window in eligible[timing] and report is not None:
        return report
    return SILENT


def _window(loader: _Loader, window: str, ticket: str, bays: tuple[str, ...]) -> str:
    seen = loader.view()
    require(
        seen["turn"] == window and seen["active"] is True,
        f"{window} window is not open for the loader",
    )
    require(seen["ticket"] == ticket, f"{window} ticket is not the injected token")
    notice = seen["notice"]
    require(notice == SILENT or notice in bays, f"{window} notice is not legal")
    return notice


def _prefix(
    seed: int, transition: str, timing: str, sender: str, tickets: tuple[str, str]
) -> tuple[Any, _Loader, dict[str, Any]]:
    session = Session(seed, transition, timing, tickets=tickets)
    loader = _Loader(session)
    bays, initial = _first(loader)
    ack = loader.act({"op": "stage", "bay": initial})
    require(ack.get("turn") == "report", "stage ack does not hand over to the depot")
    waiting = loader.view()
    require(
        waiting["turn"] == "report" and waiting["active"] is False,
        "loader is not idle while the depot reports",
    )
    current, report = _depot(session, sender, bays)
    require(
        current == (initial if transition == "stay" else _other(bays, initial)),
        "native transition did not happen between stage and report",
    )
    notice = _window(loader, "commit", tickets[0], bays)
    require(notice == _notice(timing, "commit", report), "commit notice timing")
    for line in loader.lines[:-1]:
        require(tickets[0].encode() not in line, "commit token issued early")
    for line in loader.lines:
        require(tickets[1].encode() not in line, "close token issued early")
    facts = {
        "bays": bays,
        "initial": initial,
        "current": current,
        "report": report,
        "notice": notice,
    }
    return session, loader, facts


def _other(bays: tuple[str, ...], bay: str) -> str:
    return next(name for name in bays if name != bay)


def _cut(session: Any) -> dict[str, Any]:
    cut = session.snapshot()
    require(set(cut) == CUT_KEYS, "native cut keys differ from the contract")
    require(cut["schema"] == "harbor.dispatch-recall.cut/v1", "native cut schema")
    return cut


def _finish(
    seed: int,
    transition: str,
    timing: str,
    sender: str,
    tickets: tuple[str, str],
    index: int,
) -> dict[str, Any]:
    session, loader, facts = _prefix(seed, transition, timing, sender, tickets)
    decision = b"".join(loader.lines)
    requests = list(loader.requests)
    bay = facts["bays"][index]
    loader.act({"op": "commit", "bay": bay, "ticket": tickets[0]})
    committed = _cut(session)
    reward = committed["reward"]
    require(type(reward) is int and reward in (0, 1), "native commit is ungraded")
    require(reward == int(bay == facts["current"]), "native reward is not physical")
    require(committed["windows"]["close"] is None, "close window opened before viewing")
    late = _window(loader, "close", tickets[1], facts["bays"])
    require(late == _notice(timing, "close", facts["report"]), "close notice timing")
    loader.act({"op": "close", "ticket": tickets[1]})
    closed = _cut(session)
    require(closed["reward"] == reward, "late receipt changed the native reward")
    require(
        closed["actions"] == committed["actions"] + [{"role": "loader", "op": "close"}],
        "late receipt changed an accepted action",
    )
    _reject(session, {"op": "commit", "bay": _other(facts["bays"], bay)}, tickets[0])
    _story(closed, seed, transition, timing, facts, bay, late)
    done = session.view("loader")
    require(
        done["turn"] == "done" and done["active"] is False,
        "loader still has a turn after close",
    )
    return {
        "bay": bay,
        "lines": list(loader.lines),
        "decision": decision,
        "hindsight": b"".join(loader.lines),
        "requests": requests,
        "reward": reward,
        "facts": facts,
    }


def transcript(
    seed: int,
    transition: str,
    timing: str,
    sender: str,
    tickets: tuple[str, str],
    index: int,
) -> list[bytes]:
    require(sender in SENDERS, "unknown sender rule")
    require(type(index) is int and index in (0, 1), "commit index is not on the menu")
    return _finish(seed, transition, timing, sender, tickets, index)["lines"]


def _reject(session: Any, request: dict[str, Any], ticket: str) -> None:
    before = session.snapshot()
    try:
        session.apply("loader", {**request, "ticket": ticket})
    except ValueError:
        require(session.snapshot() == before, "rejected request changed the cut")
        return
    raise ValueError("native session accepted a revision after commit")


def _story(
    cut: dict[str, Any],
    seed: int,
    transition: str,
    timing: str,
    facts: dict[str, Any],
    bay: str,
    late: str,
) -> None:
    require(
        (cut["seed"], cut["transition"], cut["timing"]) == (seed, transition, timing),
        "cut condition differs from the session",
    )
    require(
        tuple(cut["bays"]) == facts["bays"]
        and cut["initial_bay"] == facts["initial"]
        and cut["current_bay"] == facts["current"],
        "cut layout differs from the observed views",
    )
    require(
        cut["actions"]
        == [
            {"role": "loader", "op": "stage", "bay": facts["initial"]},
            {"role": "depot", "op": "report", "bay": facts["report"]},
            {"role": "loader", "op": "commit", "bay": bay},
            {"role": "loader", "op": "close"},
        ],
        "cut actions differ from the accepted requests",
    )
    require(
        cut["windows"]
        == {
            "commit": {"notice": facts["notice"], "redeemed": True},
            "close": {"notice": late, "redeemed": True},
        },
        "cut windows differ from the viewed windows",
    )


def _transition_row(
    seed: int, transition: str, timing: str, sender: str, tickets: tuple[str, str]
) -> dict[str, Any]:
    runs = [
        _finish(seed, transition, timing, sender, tickets, index) for index in (0, 1)
    ]
    first = runs[0]
    for run in runs:
        require(
            run["decision"] == first["decision"]
            and run["requests"] == first["requests"]
            and run["facts"] == first["facts"],
            "precommit wire history depends on the later commit",
        )
        require(
            run["hindsight"] == first["hindsight"],
            "postcommit wire lines reveal the committed bay or its reward",
        )
    scores = {run["bay"]: run["reward"] for run in runs}
    require(
        tuple(scores) == first["facts"]["bays"]
        and sorted(scores.values()) == [0, 1]
        and scores[first["facts"]["current"]] == 1,
        "native score table is not one physical winner",
    )
    return {
        "menu": first["facts"]["bays"],
        "scores": scores,
        "decision": first["decision"],
        "hindsight": first["hindsight"],
        "requests": first["requests"],
        "facts": first["facts"],
    }


def _protocol(loader: str, facts: dict[str, Any]) -> str:
    initial = facts["initial"]
    if loader == "follow":
        return initial if facts["notice"] == SILENT else facts["notice"]
    if loader == "stay":
        return initial
    require(loader == "switch", "unknown loader protocol")
    return _other(facts["bays"], initial)


def _arm(
    seed: int, timing: str, sender: str, tickets: tuple[str, str]
) -> tuple[dict[str, Any], list[World], list[dict[str, Any]], int]:
    rows = {
        transition: _transition_row(seed, transition, timing, sender, tickets)
        for transition in TRANSITIONS
    }
    commits = 2 * len(rows)
    stay, move = rows["stay"], rows["move"]
    require(stay["menu"] == move["menu"], "legal menu differs across transitions")
    require(
        stay["facts"]["initial"] == move["facts"]["initial"],
        "initial bay depends on the transition",
    )
    require(stay["requests"] == move["requests"], "loader requests depend on the arm")
    require(
        len(stay["decision"]) == len(move["decision"])
        and len(stay["hindsight"]) == len(move["hindsight"]),
        "fixed-width wire lengths depend on the transition",
    )
    worlds = [
        World(name, 1, rows[name]["decision"], rows[name]["scores"])
        for name in TRANSITIONS
    ]
    decision = analyze(worlds, 1)
    require(
        decision["observed"] == _EXPECTED_DECISION[sender][timing],
        "exact decision optimum differs from the design",
    )
    later = [
        World(name, 1, rows[name]["hindsight"], rows[name]["scores"])
        for name in TRANSITIONS
    ]
    relation, _ = partition(worlds, later)
    split = len({world.observation for world in later}) == 2
    require(split == _HINDSIGHT_SPLIT[sender][timing], "close split differs")
    follow = {
        loader: str(
            sum(
                Fraction(
                    rows[name]["scores"][_protocol(loader, rows[name]["facts"])], 2
                )
                for name in TRANSITIONS
            )
        )
        for loader in LOADERS
    }
    expected = {"follow": _EXPECTED_FOLLOW[sender][timing], "stay": "1/2"}
    require(
        follow == {**expected, "switch": "1/2"},
        "fixed loader protocol rewards differ from the design",
    )
    summary = {
        "decision": decision["observed"],
        "classes": decision["observations"],
        "collide": worlds[0].observation == worlds[1].observation,
        "close_split": split,
        "after_commit": relation,
        "follow": follow["follow"],
        "always_stay": follow["stay"],
        "always_switch": follow["switch"],
        "replies": len(rows["stay"]["requests"]),
        "stay_decision": _digest(rows["stay"]["decision"]),
        "move_decision": _digest(rows["move"]["decision"]),
        "stay_full": _digest(rows["stay"]["hindsight"]),
        "move_full": _digest(rows["move"]["hindsight"]),
    }
    return summary, worlds, stay["requests"], commits


def _missing(seed: int, tickets: tuple[str, str]) -> dict[str, Any]:
    session = Session(seed, "move", "immediate", tickets=tickets)
    loader = _Loader(session)
    bays, initial = _first(loader)
    loader.act({"op": "stage", "bay": initial})
    unreported = _cut(session)
    require(
        unreported["actions"] == [{"role": "loader", "op": "stage", "bay": initial}]
        and unreported["reward"] is None
        and unreported["windows"] == {"commit": None, "close": None},
        "absent report is not preserved as absent",
    )
    _depot(session, "silent", bays)
    silent = _cut(session)
    require(
        silent["actions"][-1] == {"role": "depot", "op": "report", "bay": None}
        and silent["reward"] is None,
        "elected silence is not preserved as a filed None",
    )
    before = _cut(session)
    try:
        session.apply("loader", {"op": "commit", "bay": initial, "ticket": tickets[0]})
    except ValueError:
        require(_cut(session) == before, "premature ticket changed the cut")
    else:
        raise ValueError("ticket was redeemable before its window was viewed")
    notice = _window(loader, "commit", tickets[0], bays)
    unredeemed = _cut(session)
    require(
        notice == SILENT
        and unredeemed["windows"]["commit"] == {"notice": SILENT, "redeemed": False}
        and unredeemed["reward"] is None,
        "viewed but uncommitted window is not preserved as missing",
    )
    loader.act({"op": "commit", "bay": initial, "ticket": tickets[0]})
    wrong = _cut(session)
    require(wrong["reward"] == 0, "wrong physical commit is not graded 0")
    return {
        "unreported_recorded": False,
        "unreported_reward": None,
        "silent_recorded": True,
        "silent_reward": None,
        "premature_ticket_rejected": True,
        "unredeemed_reward": None,
        "wrong_commit_reward": 0,
    }


def _pooled(decisions: dict[tuple[str, str], list[World]], timing: str) -> str:
    worlds = [
        World(f"{sender}/{world.name}", world.mass, world.observation, world.scores)
        for sender in _POOLED
        for world in decisions[sender, timing]
    ]
    observed = analyze(worlds, 1)["observed"]
    require(observed == "1/2", "pooled unknown reporter is informative")
    return observed


def certify(seed: int) -> dict[str, Any]:
    tickets = phase_tickets(seed)
    arms: dict[str, dict[str, Any]] = {}
    decisions: dict[tuple[str, str], list[World]] = {}
    protocol: list[dict[str, Any]] | None = None
    commits = 0
    for sender in SENDERS:
        arms[sender] = {}
        for timing in TIMINGS:
            summary, worlds, requests, count = _arm(seed, timing, sender, tickets)
            require(
                protocol is None or requests == protocol,
                "loader protocol differs across arms",
            )
            protocol = requests
            arms[sender][timing] = summary
            decisions[sender, timing] = worlds
            commits += count
    reference = decisions["truthful", "immediate"]
    for worlds in decisions.values():
        require(
            [world.scores for world in worlds] == [world.scores for world in reference],
            "native score tables depend on sender or timing",
        )
    contrasts = {
        "withheld_to_deferred": compare(
            decisions["truthful", "withheld"], 1, decisions["truthful", "deferred"], 1
        ),
        "deferred_to_immediate": compare(
            decisions["truthful", "deferred"], 1, decisions["truthful", "immediate"], 1
        ),
        "silent_to_truthful": compare(
            decisions["silent", "immediate"], 1, decisions["truthful", "immediate"], 1
        ),
        "wrong_to_truthful": compare(
            decisions["wrong", "immediate"], 1, decisions["truthful", "immediate"], 1
        ),
    }
    require(
        [contrasts[name]["relation"] for name in contrasts]
        == ["equivalent", "refinement", "refinement", "equivalent"],
        "decision information contrasts differ from the design",
    )
    wrong = arms["wrong"]["immediate"]
    require(
        (wrong["decision"], wrong["follow"]) == ("1", "0"),
        "within-sender inversion differs from the design",
    )
    missing = _missing(seed, tickets)
    require(protocol is not None, "no arm was driven")
    return {
        "schema": SCHEMA,
        "seed": seed,
        "model_calls": 0,
        "harbor_trials": 0,
        "native_commits": commits + 1,
        "close_checked": commits,
        "schedule": ",".join(request["op"] for request in protocol),
        "bytes": "engine_replies",
        "arms": arms,
        "contrasts": {
            name: f"{value['relation']}:{value['gain']}"
            for name, value in contrasts.items()
        },
        "inversion": {
            "wrong_decision": wrong["decision"],
            "wrong_follow": wrong["follow"],
            **{f"pooled_{timing}": _pooled(decisions, timing) for timing in TIMINGS},
        },
        "missing": missing,
        "scope": _SCOPE,
    }
