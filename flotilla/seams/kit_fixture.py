"""Test-only family: a logical shift change and delayed handoff messages."""

import json
import random
import re

FAMILY = "kit_fixture"
TITLE = "Transfer a partial sum"
SEATS = 2
ARMS = ("solo", "talk", "talk-control", "talk-trap")
TWINS = (("talk-control", "talk-trap"),)


def generate(seed, instance, n_seats, difficulty):
    if n_seats != 2 or difficulty not in ("easy", "hard"):
        raise ValueError("the fixture needs 2 seats and easy or hard difficulty")
    rng = random.Random(f"fixture:{seed}:{instance}:{difficulty}")
    low = 1000 if difficulty == "easy" else 10000
    left, right = rng.randrange(low, 10 * low), rng.randrange(low, 10 * low)
    return {"seats": ["p1", "p2"], "left": left, "right": right, "delay": rng.choice((1, 2))}


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in ARMS or max_text < 20 or max_messages < 2:
        raise ValueError("unknown arm or insufficient messaging budget")
    left, right = inst["left"], inst["right"]
    params = {"total": left + right, "delay": {"talk-control": 1, "talk-trap": 2}.get(arm, inst["delay"])}
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
    receiver = (f"Your part is {right}. Poll GET /whoami until the `active` field equals \"active\". "
                "GET /board shows the current tick. GET /messages returns delivered messages in the `messages` list. "
                "Your colleague sends `handoff` followed by a number; it takes 1 or 2 ticks. "
                "A diagnostic message is discarded. If the number has not arrived, POST an empty object to /advance "
                "and read the messages again. Add both parts. " + commit)
    scripted = ["p1"] if arm in ("talk-control", "talk-trap") else []
    return {"params": params, "initial_active": ["p1"], "messaging": True,
            "briefings": {"p1": {"role": "sender", "left": left}, "p2": {"role": "receiver", "right": right}},
            "instructions": {"p1": auth + sender, "p2": auth + receiver}, "scripted_seats": scripted,
            "scripted_policies": {"p1": "sender"} if scripted else {}}


def qa(task, views):
    total = task["params"]["total"]
    for seat, view in views.items():
        briefing = json.loads(view["env.SEAT_BRIEFING"])
        if total in briefing.values() or re.search(rf"\b{total}\b", view["instruction"]):
            yield f"{seat} sees the sum rather than the parts"
