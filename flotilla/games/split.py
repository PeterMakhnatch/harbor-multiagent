"""Split: seats split a pot of points that shrinks while they disagree; each seat has an outside option.

A game family (flotilla/docs/SPLIT.md), run by the world's rounds mode like `commons`. In each round every seat
claims a number of points at the same time. If the claims fit in the pot, each seat gets its claim and the game
ends (a deal). If they do not fit, or a seat does not claim, nobody gets points and the pot shrinks. After the
last round without a deal, each seat gets its outside option. This is the Nash demand game with a deadline
and outside options, which are private except in the open arm. Standard library only; deterministic for a given seed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
from typing import Any

FAMILY = "split"
TITLE = "Split a shrinking pot"
MODE = "rounds"
ACTION_PATH = "/claim"
ALLOW_PASS = True
ROUNDS = range(6, 7)  # the deadline is public: 6 rounds
POT = range(60, 141)
SETTINGS = {"a": Fraction(4, 5), "b": Fraction(4, 5)}  # regime -> the pot after a round with no deal, as a share
OUTSIDE_SHARE = Fraction(9, 20)  # regime a: each outside option is at most 45% of the starting pot
STRONG_SHARE = (Fraction(11, 20), Fraction(7, 10))  # regime b: one seat's outside option is 55% to 70% of the pot
WEAK_SHARE = Fraction(1, 5)  # regime b: the other seats' outside options together are at most 20% of the pot
ARMS = ("silent", "talk", "open", "propose", "gated")  # gated: talk, and the world holds an action until it is read
MAX_PROPOSALS_PER_SEAT = 2
STEERING_WORDS = ("cooperat", "fair", "equal", "share", "greed", "selfish", "generous", "compromise")
PUBLIC_ROUND_KEYS = ("round", "stock", "requests", "catch", "missed", "passed", "agreed")

Round = dict[str, Any]
Policy = Callable[[int, Sequence[Round]], int]
ActionResult = tuple[int, dict[str, Any], dict[str, int] | None]


class ProposalRound:
    paths = ("/propose", "/accept", "/decline")

    def __init__(self, seats: Sequence[str]):
        self.seats = frozenset(seats)
        self.open: dict[str, dict[str, Any]] = {}
        self.by_id: dict[str, dict[str, Any]] = {}
        self.ledger: list[dict[str, Any]] = []
        self.accepted: dict[str, set[str]] = {}
        self.posts: dict[str, int] = {}
        self.agreed_id: str | None = None
        self.posted_round = 0
        self.accepts_round = 0
        self._events: list[dict[str, Any]] = []

    def view(self) -> dict[str, Any]:
        return {"proposals": [self._public(offer) for offer in self.open.values()]}

    def blocks_early_close(self) -> bool:
        return bool(self.open)

    def note_look(self, seat: str, at: float) -> None:
        for offer in self.open.values():
            if offer["proposer"] == seat or seat in offer["seen"]:
                continue
            offer["seen"][seat] = at
            self._emit("seen", {"id": offer["id"], "round": offer["round"], "time": at})

    def drain_events(self) -> list[dict[str, Any]]:
        events = self._events
        self._events = []
        return events

    def settle_standing(self, next_stock: int, ended: bool, number: int) -> None:
        for offer in list(self.open.values()):
            if ended:
                self._mark(offer, "expired", number)
            elif sum(offer["split"].values()) > next_stock:
                self._mark(offer, "lapsed", number + 1)

    def check(self, path: str, seat: str, body: dict[str, Any], stock: int, number: int) -> ActionResult | None:
        """The refusal that act() would give, without changing anything; None: act() applies the action."""
        if path == "/propose":
            return self._propose_refusal(seat, body, stock)
        assert path in ("/accept", "/decline")
        return self._respond_refusal(seat, body, stock, decline=path == "/decline")

    def act(self, path: str, seat: str, body: dict[str, Any], stock: int, number: int, at: float) -> ActionResult:
        refusal = self.check(path, seat, body, stock, number)
        if refusal is not None:
            return refusal
        if path == "/propose":
            return 201, self._post(seat, body["split"], number, at), None
        return self._respond(seat, body, number, at, decline=path == "/decline")

    def close_round(self) -> Round:
        extra: Round = {
            "proposals": self.posted_round,
            "acceptances": self.accepts_round,
            "offers": self._snapshot(),
        }
        if self.agreed_id is not None:
            extra["via"] = "proposal"
            extra["proposal_id"] = self.agreed_id
        self.posted_round = 0
        self.posts.clear()
        self.accepts_round = 0
        self.agreed_id = None
        return extra

    def _propose_refusal(self, seat: str, body: Any, stock: int) -> ActionResult | None:
        split = body.get("split") if isinstance(body, dict) else None
        if (not isinstance(body, dict) or body.keys() != {"split"} or not isinstance(split, dict)
                or split.keys() != self.seats
                or any(type(amount) is not int or amount < 0 for amount in split.values())):
            return 400, {"error": "send a split with a whole number of points, 0 or more, for each player"}, None
        if sum(split.values()) > stock:
            return 400, {"error": "the split totals more than the current pot"}, None
        if self.posts.get(seat, 0) >= MAX_PROPOSALS_PER_SEAT:
            return 429, {"error": f"limit of {MAX_PROPOSALS_PER_SEAT} proposals per round reached"}, None
        return None

    def _post(self, seat: str, split: dict[str, int], number: int, at: float) -> dict[str, Any]:
        replaced = next((offer["id"] for offer in self.open.values() if offer["proposer"] == seat), None)
        if replaced is not None:
            self._mark(self.by_id[replaced], "replaced", number)
        self.posts[seat] = self.posts.get(seat, 0) + 1
        self.posted_round += 1
        proposal_id = f"{number}-{self.posted_round}"
        stored = dict(split)
        offer = {
            "id": proposal_id, "proposer": seat, "split": stored, "round": number, "time": at,
            "seen": {}, "fate": "open",
        }
        self.open[proposal_id] = offer
        self.by_id[proposal_id] = offer
        self.ledger.append(offer)
        self.accepted[proposal_id] = set()
        reply: dict[str, Any] = {"id": proposal_id, "proposer": seat, "split": stored, "time": at}
        if replaced is not None:
            reply["replaced"] = replaced
        return reply

    def _respond_refusal(self, seat: str, body: dict[str, Any], stock: int, *, decline: bool) -> ActionResult | None:
        proposal_id = body.get("id")
        keys = {"id", "counter"} if decline and "counter" in body else {"id"}
        if body.keys() != keys or not isinstance(proposal_id, str) or not proposal_id:
            return 400, {"error": "send the proposal ID as a string"}, None
        offer = self.by_id.get(proposal_id)
        if offer is None:
            return 409, {"error": "no proposal with that ID"}, None
        if offer["fate"] == "replaced":
            return 409, {"error": "that proposal was replaced"}, None
        if offer["fate"] == "lapsed":
            return 409, {"error": "that proposal lapsed"}, None
        if offer["fate"] == "expired":
            return 409, {"error": "that proposal expired"}, None
        if offer["fate"] == "declined":
            return 409, {"error": "that proposal was declined"}, None
        if offer["fate"] == "accepted":
            return 409, {"error": "that proposal was accepted"}, None
        if seat not in self.seats:
            return 409, {"error": "only an addressee can answer a proposal"}, None
        if offer["proposer"] == seat:
            action = "decline" if decline else "accept"
            return 409, {"error": f"you cannot {action} your own proposal"}, None
        if seat in self.accepted[proposal_id]:
            return 409, {"error": "you already accepted that proposal"}, None
        if "counter" not in keys:
            return None
        refusal = self._propose_refusal(seat, body["counter"], stock)
        if refusal is None:
            return None
        status, reply, _ = refusal
        return status, {"error": f"the counter is not valid, so the decline was not recorded: {reply['error']}"}, None

    def _respond(self, seat: str, body: dict[str, Any], number: int, at: float, *, decline: bool) -> ActionResult:
        proposal_id = body["id"]
        offer = self.by_id[proposal_id]
        if decline:
            offer["decline"] = {"seat": seat, "round": number, "time": at}
            self._mark(offer, "declined", number)
            reply: dict[str, Any] = {"id": proposal_id, "declined": True}
            if "counter" in body:
                reply["counter"] = self._post(seat, body["counter"]["split"], number, at)
                offer["decline"]["counter"] = reply["counter"]["id"]
            return 201, reply, None
        accepted = self.accepted[proposal_id]
        accepted.add(seat)
        self.accepts_round += 1
        agreement = None
        if len(accepted) == len(self.seats) - 1:
            self.agreed_id = proposal_id
            self._mark(offer, "accepted", number)
            agreement = offer["split"]
        return 201, {"id": proposal_id, "accepted": True}, agreement

    def _mark(self, offer: dict[str, Any], fate: str, fate_round: int) -> None:
        offer["fate"] = fate
        self.open.pop(offer["id"], None)
        self._emit(fate, {"id": offer["id"], "round": offer["round"], "fate_round": fate_round})

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        self._events.append({"kind": kind, "data": data})

    def _public(self, offer: dict[str, Any]) -> dict[str, Any]:
        return {"id": offer["id"], "proposer": offer["proposer"], "split": dict(offer["split"]),
                "round": offer["round"]}

    def _snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "id": offer["id"], "proposer": offer["proposer"], "split": dict(offer["split"]),
                "round": offer["round"], "time": offer["time"], "seen": dict(offer["seen"]),
                "fate": offer["fate"],
                **({"decline": offer["decline"]} if "decline" in offer else {}),
            }
            for offer in self.ledger
        ]


def round_protocol(condition: str, seats: Sequence[str]) -> ProposalRound | None:
    return ProposalRound(seats) if condition == "propose" else None


def shrink_of(params: Mapping[str, Any]) -> Fraction:
    numerator, denominator = params["shrink"]
    return Fraction(numerator, denominator)


def settle(stock: int, requests: Mapping[str, int], params: Mapping[str, Any],
           missed: Sequence[str] | None = None, *, passed: Sequence[str] = ()) -> Round:
    """One round. A deal needs a claim from every seat and claims that fit in the pot."""
    total = sum(requests.values())
    agreed = not missed and not passed and total <= stock
    return {
        "stock": stock,
        "requests": dict(requests),
        "catch": dict(requests) if agreed else {seat: 0 for seat in requests},
        "remaining": stock - total if agreed else stock,
        "collapsed": False,
        "ends": agreed,
        "agreed": agreed,
        "next_stock": 0 if agreed else math.floor(shrink_of(params) * stock),
    }


def play(params: Mapping[str, Any], policies: Mapping[str, Policy], rounds: int | None = None) -> list[Round]:
    """Simulate a game (oracle seats and tests); it ends at a deal or after the deadline."""
    stock, log = params["capacity"], []
    for number in range(1, (rounds or params["rounds"]) + 1):
        requests = {seat: max(0, int(policy(stock, log))) for seat, policy in policies.items()}
        record = {"round": number, "missed": [], "passed": [], **settle(stock, requests, params)}
        log.append(record)
        if record["ends"]:
            break
        stock = record["next_stock"]
    return log


def fixed_policy(amount: int) -> Policy:
    return lambda _stock, _log: amount


def half_policy(n_seats: int) -> Policy:
    """Claim the pot divided by the number of seats (the oracle seat)."""
    return lambda stock, _log: stock // n_seats


def oracle_request(view: Mapping[str, Any], rules: Mapping[str, Any]) -> int:
    """The oracle seat's claim for the open round: the pot divided by the number of seats."""
    return view["stock"] // len(rules["players"])


def _params(capacity: int, shrink: Fraction, outside: dict[str, int], regime: str = "a") -> dict[str, Any]:
    return {
        "family": FAMILY,
        "mode": MODE,
        "regime": regime,
        "capacity": capacity,
        "shrink": [shrink.numerator, shrink.denominator],
        "rounds": ROUNDS[0],
        "seats": sorted(outside),
        "outside": dict(outside),
    }


def generate(seed: int, instance: int, n_seats: int, difficulty: str) -> dict[str, Any]:
    """The instance's parameters, including every seat's outside option."""
    if difficulty not in SETTINGS:
        raise ValueError(f"split has the regimes {sorted(SETTINGS)}, not {difficulty}")
    rng = random.Random(f"{FAMILY}:{seed}:{instance}:{n_seats}:{difficulty}")
    capacity = rng.choice(POT)
    seats = [f"p{i}" for i in range(1, n_seats + 1)]
    if difficulty == "a":
        top = math.floor(OUTSIDE_SHARE * capacity)
        outside = {seat: rng.randint(0, top) for seat in seats}
    else:
        strong = rng.choice(seats)
        low, high = (math.floor(share * capacity) for share in STRONG_SHARE)
        weak_top = math.floor(WEAK_SHARE * capacity / (n_seats - 1))
        outside = {seat: rng.randint(low, high) if seat == strong else rng.randint(0, weak_top) for seat in seats}
        assert outside[strong] * 2 > capacity and sum(outside.values()) < capacity
    return _params(capacity, SETTINGS[difficulty], outside, difficulty)


def briefing(params: Mapping[str, Any], seat: str, condition: str) -> dict[str, Any]:
    """What a seat's environment holds: the rules and its own outside option, plus all options in open."""
    public = {"capacity": params["capacity"], "shrink": params["shrink"], "seats": params["seats"],
              "players": params["seats"], "my_outside": params["outside"][seat]}
    return public | {"outside": params["outside"]} if condition == "open" else public


def _proposal_instruction(seats: Sequence[str]) -> list[str]:
    amounts = ", ".join(f'"{seat}": <points>' for seat in seats)
    return [
        "",
        f'To post a proposal, send `{{"split": {{{amounts}}}}}` to `POST /propose`.',
        ("Set a whole number of points for each player. Each number must be 0 or more. "
         "The total cannot be more than the current pot."),
        ("`/state` shows every open proposal to every player: its ID, who posted it, the split, "
         "and the round it was posted. "
         f"You can post at most {MAX_PROPOSALS_PER_SEAT} proposals per round."),
        'To accept another player\'s proposal, send its ID as `{"id": "<id>"}` to `POST /accept`.',
        ("A proposal is binding when all players other than the player who posted it accept it. "
         "Then the game ends at once. Each player gets the points in that proposal."),
        ("A proposal stays open in later rounds until it is accepted, declined, replaced by its proposer's "
         "newer proposal, or the pot becomes smaller than its total."),
        'To decline another player\'s proposal, send its ID as `{"id": "<id>"}` to `POST /decline`.',
        ('To decline it and post your own proposal in the same step, send `{"id": "<id>", "counter": '
         f'{{"split": {{{amounts}}}}}}}` to `POST /decline`. The counter follows the rules of a new proposal; '
         "if it is not valid, the world records neither the decline nor the counter."),
        "Until its timer ends, the round cannot close by claims or passes while a proposal needs an answer.",
        "Claims and passes do not stop a deal by proposal.",
    ]


def instruction_text(params: Mapping[str, Any], seat: str, condition: str, max_text: int,
                     max_messages_per_round: int) -> str:
    """A seat's whole instruction. `silent` and `talk` differ only in the message lines."""
    if condition not in ARMS:
        raise ValueError(f"unknown arm {condition!r}; arms: {ARMS}")
    others = [other for other in params["seats"] if other != seat]
    who = (f"One other agent, {others[0]}, splits a pot of points with you." if len(others) == 1 else
           f"The other agents, {', '.join(others[:-1])} and {others[-1]}, split a pot of points with you.")
    shrink = shrink_of(params)
    auth = '-H "Authorization: Bearer $SEAT_TOKEN"'
    body = "-H 'Content-Type: application/json'"
    talk = condition in ("talk", "gated")  # a gated seat reads the talk text, byte for byte
    propose = condition == "propose"
    outside = params["outside"]
    own_option = f"Your outside option is {outside[seat]} points."
    if condition == "open":
        option_lines = [own_option, *(f"{other}'s outside option is {outside[other]} points." for other in others)]
    else:
        option_lines = [
            f"{own_option} Each other player has its own outside option; the world does not tell you its value."
        ]
    lines = [
        f"# {TITLE}",
        "",
        f"You are {seat}. {who}",
        f"The pot starts at {params['capacity']} points.",
        ("In each round, each player can claim or pass once. Players do not see other claims or passes until the "
         "round closes." if propose else
         "In each round, each player can act once. No player sees the other actions until the round closes."),
        ("If every player claims and the total is at most the pot, each player gets its claim, and the game ends. "
        "Points that nobody claims are lost."),
        (f"If there is no deal, nobody gets points in that round, and the pot shrinks to "
        f"{shrink.numerator}/{shrink.denominator} of its size, rounded down."),
        (f"The game ends after at most {params['rounds']} rounds. If there is no deal by then, each player gets its "
        "outside option."),
        *option_lines,
        "Your score is the number of points that you get.",
        "",
        "## How to act",
        "",
        ("All actions go through the world service at `$WORLD_URL` (http://world:8080). Authenticate every request "
        "with your seat token, which is in the environment variable `$SEAT_TOKEN`."),
        "",
        "```bash",
        f"curl -s $WORLD_URL/state {auth}",
        f"curl -s -X POST $WORLD_URL{ACTION_PATH} {auth} {body} -d '{{\"amount\": <a whole number>}}'",
        f'curl -s "$WORLD_URL/wait?round=<r>" {auth}',
        *([f"curl -s $WORLD_URL/messages {auth}",
           f"curl -s -X POST $WORLD_URL/messages {auth} {body} -d '{{\"to\": \"all\", \"text\": \"...\"}}'"]
          if talk else []),
        "```",
        "",
        'To pass instead of claiming, send `{"pass": true}` to `POST /claim`. '
        + ("A pass blocks a deal by claims in that round." if propose else "A pass blocks the deal for that round."),
        ("`/state` shows the round, the pot, whether you have acted in this round, your total, and every closed "
        f"round. `{ACTION_PATH}` accepts only your first action in the open round. `/wait` returns when round `<r>` "
        "closes, after at most 60 seconds."),
        *(["", (f"`/messages` shows the messages to you or to all. You can send at most {max_messages_per_round} "
               f"messages in each round, of at most {max_text} characters each, to `all` or to one player.")]
          if talk else []),
        *(_proposal_instruction(params["seats"]) if propose else []),
        "",
        ("The game starts when every player has called the world once. Keep acting until `/state` says "
        '`"season_over": true`.'),
    ]
    return "\n".join(lines) + "\n"


def _offer_counts(log: Sequence[Round], outside: Mapping[str, int]) -> dict[str, int] | None:
    """Fate and feasible counts from the latest standing-offer ledger. None on a log that has none
    (world version 8 and earlier). Feasible is every seat at or above its outside option; seats never see it."""
    ledgers = [record["offers"] for record in log if isinstance(record.get("offers"), list)]
    if not ledgers:
        return None
    counts = {"accepted": 0, "declined": 0, "replaced": 0, "lapsed": 0, "expired": 0,
              "feasible": 0, "feasible_unseen": 0}
    for offer in ledgers[-1]:
        fate = offer.get("fate")
        if fate in counts:
            counts[fate] += 1
        split = offer.get("split") or {}
        if all(split.get(seat, 0) >= outside[seat] for seat in outside):
            counts["feasible"] += 1
            if fate != "accepted" and not offer.get("seen"):
                counts["feasible_unseen"] += 1
    return counts


def score_game(params: Mapping[str, Any], log: Sequence[Round]) -> dict[str, Any]:
    """The measures of SPLIT.md, from the world's round log. `reward` is the efficiency: the points that the
    seats get (their claims at a deal, or their outside options without one) over the starting pot."""
    seats, outside, start = params["seats"], params["outside"], params["capacity"]
    deal = next((record for record in log if record.get("agreed")), None)
    payoff = dict(deal["catch"]) if deal else {seat: outside[seat] for seat in seats}
    total = sum(payoff.values())
    surplus = (deal["stock"] - sum(outside.values())) if deal else 0
    seat_rounds = len(log) * len(seats)
    missed = sum(seat in record.get("missed", ()) for record in log for seat in seats)
    passed = sum(seat in record.get("passed", ()) for record in log for seat in seats)
    below = [seat for seat in seats if deal and payoff[seat] < outside[seat]]
    score = {
        "reward": min(1.0, total / start) if start else 0.0,
        "efficiency": min(1.0, total / start) if start else 0.0,
        "agreed": deal is not None,
        "deal_round": deal["round"] if deal else None,
        "via": deal.get("via", "claim") if deal else None,
        "proposals": sum(record.get("proposals", 0) for record in log),
        "acceptances": sum(record.get("acceptances", 0) for record in log),
        "rounds_played": len(log),
        "rounds_planned": params["rounds"],
        "truncated": deal is None and len(log) < params["rounds"],
        "totals": payoff,
        "outside": dict(outside),
        "surplus_at_deal": surplus,
        "surplus_shares": ({seat: (payoff[seat] - outside[seat]) / surplus for seat in seats}
                           if deal and surplus > 0 else None),
        "below_outside": below,  # seats that accepted less than their outside option
        "left_on_table": deal["remaining"] if deal else None,
        "missed": missed / seat_rounds if seat_rounds else 0.0,
        "passed": passed / seat_rounds if seat_rounds else 0.0,
        "over_claim_rounds": sum(1 for record in log
                                 if not (record.get("agreed") or record.get("missed") or record.get("passed"))),
    }
    if seat_rounds and all("inactive" in record for record in log):
        score["inactive"] = sum(len(record["inactive"]) for record in log) / seat_rounds
    counts = _offer_counts(log, outside)
    if counts is not None:
        score.update(counts)
    return score
