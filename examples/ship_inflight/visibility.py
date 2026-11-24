import json
import sys
from collections import Counter
from collections.abc import Sequence
from itertools import product
from pathlib import Path
from types import ModuleType
from typing import Any

from ..native_study import require
from ..seat_checkpoints.materialize import read_plain
from . import inflight_verify as trusted
from .controls import RELIEF_ACTIONS, load_pin

USAGE = "usage: python3.12 -BS -m examples.ship_inflight.visibility [--json]"
SCHEMA = "harbor.ship-inflight.visibility/v1"
QUESTION = "same offer, relief decision, receipt order and channel: are role replies through freeze identical in the fails and settles worlds?"
RUN = "00000000-0000-4000-8000-000000000000"
REFS = {"original": "0123456789abcdef" * 2, "draft": "fedcba9876543210" * 2}
TOKENS = {"outgoing": "public-visibility-outgoing-token-0001", "relief": "public-visibility-relief-token-00001"}
ENVIRONMENT = ("shipment", "world", "inflight_world")
VERIFIERS = (("tests/inflight_verify.py", trusted.__file__), ("tests/verify.py", trusted.stock.__file__))
OFFERS = ("absent", "none", "original", "draft")
ORDERS = {
    "receipt-first": ("hand", "receipt", "decide"),
    "decide-then-receipt": ("hand", "decide", "receipt"),
    "decide-before-handoff": ("decide", "hand", "receipt"),
}
READS = tuple((role, "GET", path, {}) for role in TOKENS for path in ("/brief", "/status"))
COUNTS = ("pairs", "sound", "visible_equal", "reward_differs", "outcome_differs", "visible_equal_reward_differs")
LIMITS = (
    "in-process pinned InflightWorld.handle/freeze; HTTP framing, bearer checks and seat containers are not exercised",
    "scripted role calls with fixed public references; resume/replace not fed by an earlier inbox receipt are held-reference protocol probes",
    "no model behavior or timing evidence",
)


def load_world(files: dict[str, bytes]) -> ModuleType:
    saved = {name: sys.modules.pop(name, None) for name in ENVIRONMENT}
    try:
        for name in ENVIRONMENT:
            module = sys.modules[name] = ModuleType(name)
            exec(compile(files[f"environment/{name}.py"], f"pinned-{name}", "exec"), module.__dict__)
        return module
    finally:
        for name, previous in saved.items():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous


def verified_world() -> ModuleType:
    _, _, files = load_pin()
    for relative, path in VERIFIERS:
        require(read_plain(Path(path))[0] == files[relative], "trusted verifier differs from the pin")
    return load_world(files)


def resolve_body(choice: str, replace: str | None) -> dict[str, object]:
    if replace is not None:
        return {"action": "replace", "reference": REFS[replace]}
    if choice in REFS:
        return {"action": "resume", "reference": REFS[choice]}
    return {"action": choice}


def play(module: ModuleType, fate: str, channel: str, offer: str, action: str, order: str) -> tuple[list[Any], Any, bool]:
    shipment = module.InflightShipment(fate, channel, RUN, REFS["original"], REFS["draft"])
    world = module.InflightWorld(shipment, dict(TOKENS), (REFS["original"], REFS["draft"]))
    choice, replace = RELIEF_ACTIONS[action]
    steps = {
        "hand": [] if offer == "absent" else [("outgoing", "POST", "/handoff", {"reference": REFS.get(offer)})],
        "receipt": [("relief", "GET", "/inbox", {})],
        "decide": [] if choice is None else [("relief", "POST", "/resolve", resolve_body(choice, replace))],
    }
    live = [*READS, *(step for name in ORDERS[order] for step in steps[name]), *READS]
    seen = [(role, path, *world.handle(role, method, path, body)) for role, method, path, body in live]
    cuts = (world.freeze(), world.freeze())
    after = [(role, path, *world.handle(role, method, path, body)) for role, method, path, body in (*READS, steps["receipt"][0])]
    sane = all(reply[2] == 200 for reply in seen) and all(reply[2] in (200, 409) for reply in after)
    return seen + after, cuts, sane and world.healthy()


def grade(cuts: Any, expected: tuple[str, ...]) -> dict[str, Any] | None:
    (status, data), again = cuts
    if status != 200 or again != (status, data):
        return None
    try:
        observation = trusted.evaluate(trusted.stock.parse_cut(data))
    except trusted.CutError:
        return None
    found = tuple(observation[key] for key in ("fate", "channel", "outgoing_handoff", "relief_action"))
    return observation if found == expected else None


def survey(module: ModuleType) -> dict[str, Counter[str]]:
    groups = {name: Counter({key: 0 for key in COUNTS}) for name in ("all", "cooperative", "probe")}
    for offer, action, channel, order in product(OFFERS, RELIEF_ACTIONS, trusted.CHANNELS, ORDERS):
        choice, replace = RELIEF_ACTIONS[action]
        target = replace or (choice if choice in REFS else None)
        received = order == "receipt-first" and channel == "deliver" and offer == target
        kind = "probe" if target is not None and not received else "cooperative"
        runs = {fate: play(module, fate, channel, offer, action, order) for fate in trusted.FATES}
        grades = {fate: grade(runs[fate][1], (fate, channel, offer, action)) for fate in trusted.FATES}
        sound = all(runs[fate][2] and grades[fate] is not None for fate in trusted.FATES)
        equal = runs["fails"][0] == runs["settles"][0]
        rewards = sound and grades["fails"]["reward"] != grades["settles"]["reward"]
        outcomes = sound and grades["fails"]["outcome"] != grades["settles"]["outcome"]
        row = (1, sound, equal, rewards, outcomes, equal and rewards)
        for name in ("all", kind):
            groups[name].update({key: int(value) for key, value in zip(COUNTS, row, strict=True)})
    return groups


def main(argv: Sequence[str]) -> int:
    if list(argv) not in ([], ["--json"]):
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        module = verified_world()
    except (ValueError, OSError):
        sys.stderr.write("visibility: pinned source did not verify\n")
        return 2
    groups = survey(module)
    full = groups["all"]
    verdict = "identical" if full["visible_equal"] == full["pairs"] else "differ"
    report = {"schema": SCHEMA, "question": QUESTION, "verdict": verdict, "groups": groups, "limits": list(LIMITS)}
    if argv:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        lines = [f"{name}: " + " ".join(f"{key}={value}" for key, value in counts.items()) for name, counts in groups.items()]
        sys.stdout.write("\n".join([QUESTION, *lines, f"verdict: {verdict}", *(f"limit: {limit}" for limit in LIMITS)]) + "\n")
    return 0 if verdict == "identical" and full["sound"] == full["pairs"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
