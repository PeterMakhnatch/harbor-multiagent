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
ARMS = ("silent", "talk", "open")
STEERING_WORDS = ("cooperat", "fair", "equal", "share", "greed", "selfish", "generous", "compromise")
PUBLIC_ROUND_KEYS = ("round", "stock", "requests", "catch", "missed", "passed", "agreed")

Round = dict[str, Any]
Policy = Callable[[int, Sequence[Round]], int]


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
    talk = condition == "talk"
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
        "In each round, each player can act once. No player sees the other actions until the round closes.",
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
        'To pass instead of claiming, send `{"pass": true}` to `POST /claim`. A pass blocks the deal for that round.',
        ("`/state` shows the round, the pot, whether you have acted in this round, your total, and every closed "
        f"round. `{ACTION_PATH}` accepts only your first action in the open round. `/wait` returns when round `<r>` "
        "closes, after at most 60 seconds."),
        *(["", (f"`/messages` shows the messages to you or to all. You can send at most {max_messages_per_round} "
               f"messages in each round, of at most {max_text} characters each, to `all` or to one player.")]
          if talk else []),
        "",
        ("The game starts when every player has called the world once. Keep acting until `/state` says "
        '`"season_over": true`.'),
    ]
    return "\n".join(lines) + "\n"


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
    return {
        "reward": min(1.0, total / start) if start else 0.0,
        "efficiency": min(1.0, total / start) if start else 0.0,
        "agreed": deal is not None,
        "deal_round": deal["round"] if deal else None,
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
