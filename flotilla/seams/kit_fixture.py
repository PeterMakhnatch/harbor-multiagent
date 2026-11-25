"""Test-only family: a logical shift change and delayed handoff messages."""

import json
import random
import re

FAMILY = "kit_fixture"
TITLE = "Transfer a partial sum"
VERSION = 1
SEATS = {"easy": 2, "hard": 2, "canary": 2, "standard": 2, "heavy": 3}
ARMS = ("solo", "talk", "talk-control", "talk-trap")
EXTRA_ARMS = ("team", "hint", "staged")
TWINS = (("talk-control", "talk-trap"),)
PAIRS = (("talk-trap", "hint"),)
ARM_WORDS = ("talk-control", "talk-trap", "kit_fixture")
HINT = "Wait for the number before committing."
NOTE_CAP = 12


def encode(value):
    return f"handoff {value}"


def decode(note):
    return int(note.removeprefix("handoff "))


def generate(seed, instance, n_seats, difficulty):
    count = SEATS[difficulty] if n_seats is None else n_seats
    if count not in (2, 3):
        raise ValueError("the fixture needs 2 or 3 seats")
    rng = random.Random(f"fixture:{seed}:{instance}:{difficulty}")
    low = 1000 if difficulty in ("easy", "canary") else 10000
    left, right = rng.randrange(low, 10 * low), rng.randrange(low, 10 * low)
    return {"seats": [f"p{i + 1}" for i in range(count)], "left": left, "right": right, "delay": rng.choice((1, 2))}


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in (*ARMS, *EXTRA_ARMS) or max_text < 20 or max_messages < (3 if arm == "team" else 2):
        raise ValueError("unknown arm or insufficient messaging budget")
    left, right = inst["left"], inst["right"]
    params = {"total": left + right, "delay": {"talk-control": 1, "talk-trap": 2, "hint": 2}.get(arm, inst["delay"]),
              "protocol": arm if arm in ("team", "staged") else "shift"}
    auth = ('Use $WORLD_URL and the header Authorization: Bearer $SEAT_TOKEN for every call. '
            'For POST requests use Content-Type: application/json.\n')
    commit = 'POST /commit with a JSON object whose `total` field is the sum.\n'
    if arm == "solo":
        return {"params": params, "messaging": False, "briefings": {"p1": {"role": "all", "left": left, "right": right}},
                "instructions": {"p1": auth + f"Add {left} and {right}. " + commit}}
    sender = (f"Your part is {left}. Send `handoff {left}` and `diagnostic` using POST /messages. "
              'Use a JSON object with `to` set to "p2" and `text` set to the message. '
              "The first message takes 1 or 2 ticks. The diagnostic is discarded. "
              "Then POST an empty object to /handoff. Your shift ends for good.\n")
    receiver = (f"Your part is {right}. GET /await?until=active waits for your shift. "
                "GET /board shows the current tick. GET /messages returns delivered messages in the `messages` list. "
                "Replies can also contain unread messages in `new_messages`. "
                "Your colleague sends `handoff` followed by a number; it takes 1 or 2 ticks. "
                "A diagnostic message is discarded. If the number has not arrived, POST an empty object to /advance "
                "and read the messages again. Add both parts. " + commit)
    if arm == "staged":
        return {"params": params, "initial_active": ["p1"], "start_after": {"p2": ["p1"]}, "messaging": False,
                "briefings": {"p1": {"role": "seal", "left": left}, "p2": {"role": "read", "right": right}},
                "instructions": {"p1": auth + f"POST /handoff with your part {left} as a string in `note`. "
                                 "The note must have at most 12 characters. End your shift.",
                                 "p2": auth + f"Your part is {right}. Read the sealed `note` in GET /board. " + commit}}
    if arm == "team":
        roles = {"p1": {"role": "round_sender", "left": left}, "p2": {"role": "round_receiver", "right": right}}
        instructions = {"p1": auth + f"Your part is {left}. Send `handoff {left}` to p2. Then POST /ready with {{}}.",
                        "p2": auth + f"Your part is {right}. POST /ready with {{}}. "
                        "Wait using GET /await?until=tick&after=0 if tick is 0. "
                        "Read all new_messages in replies. The board posts at tick 0 and 1. "
                        "Your colleague's number arrives at tick 1. " + commit}
        for seat in inst["seats"][2:]:
            roles[seat] = {"role": "round_waiter"}
            instructions[seat] = auth + "POST /ready with {} to record your part of the round."
        return {"params": params | {"delay": 1}, "briefings": roles, "instructions": instructions,
                "senders": ["board"], "max_calls": {"p2": 20}, "push": True}
    scripted = ["p1"] if arm in ("talk-control", "talk-trap", "hint") else []
    if arm == "hint":
        receiver += HINT + "\n"
    return {"params": params, "initial_active": ["p1"], "messaging": True,
            "briefings": {"p1": {"role": "sender", "left": left}, "p2": {"role": "receiver", "right": right}},
            "instructions": {"p1": auth + sender, "p2": auth + receiver}, "scripted_seats": scripted,
            "scripted_policies": {"p1": "sender"} if scripted else {}}


def pair_difference(first, second, views_a, views_b):
    normalized = {seat: dict(view) for seat, view in views_b.items()}
    instruction = normalized["p2"]["instruction"]
    if not instruction.endswith(HINT + "\n"):
        return ["missing protocol sentence"]
    normalized["p2"]["instruction"] = instruction.removesuffix(HINT + "\n")
    return [] if normalized == views_a else ["more than the protocol sentence differs"]


def leak_values(task_json):
    return [str(task_json["params"]["total"])]


def qa(task, views):
    total = task["params"]["total"]
    for seat, view in views.items():
        briefing = json.loads(view["env.SEAT_BRIEFING"])
        if total in briefing.values() or re.search(rf"\b{total}\b", view["instruction"]):
            yield f"{seat} sees the sum rather than the parts"
