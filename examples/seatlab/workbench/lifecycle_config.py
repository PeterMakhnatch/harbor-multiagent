import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROG = "python3.12 -BS -m examples.seatlab.workbench.lifecycle_config"
EXAMPLES = Path(__file__).resolve().parents[2]
RESULTS = EXAMPLES / "seatlab" / "results" / "seat-event-stream"
LIFECYCLE = "examples.seatlab.workbench.lifecycle_oracle:LifecycleOracle"
ENVIRONMENT = {"type": "docker", "delete": True, "force_build": False}
STOCK = {"outgoing": {"SH_POLICY": "original"}, "relief": {"SH_POLICY": "resume"}}
WORLD = {"ship-handover": {"SH_PRIOR": "dropped", "SH_CHANNEL": "deliver"}}
BEHAVIORS = {
    "support-return": ("delay", "return"),
    "support-held": ("delay", "hold"),
    "support-cancel": ("delay", "cancel"),
    "support-fault": ("delay", "raise"),
    "setup-refusal": ("setup_refusal", "return"),
    "team-deadline": ("hold", "hold"),
}
DEADLINE = {"team-deadline": 0.035}
EPILOG = (
    "CASE prints one native TrialConfig JSON document to stdout and writes nothing. "
    "Choose a fresh --trials-dir per case yourself; the trial name is fixed, so a reused directory collides. "
    "seat-events=on means pass --seat-events to `harbor trial start` explicitly; it is not a config field. "
    "This does not lock or reserve a registered native ticket."
)

Record = dict[str, Any]


def _load(name: str) -> Record:
    with open(RESULTS / name, encoding="utf-8") as file:
        document = json.load(file)
    assert type(document) is dict
    return document


def _roster() -> dict[str, Record]:
    cases: dict[str, Record] = {}
    for attempt in _load("registration.json")["native_attempts"]:
        case, task = attempt["case"], attempt["task"]
        behaviors = ("oracle", "oracle") if task == "ship-handover" else BEHAVIORS[case]
        seats = tuple(attempt["native_dispositions"])
        assert len(seats) == 2 and type(attempt["observe"]) is bool
        cases[case] = {
            "task": task,
            "name": f"seat-stream-{case}",
            "observe": attempt["observe"],
            "seats": dict(zip(seats, behaviors, strict=True)),
            "multiplier": DEADLINE.get(case),
        }
    for gate in _load("return-gate/registration.json")["cases"]:
        case = gate["name"]
        assert gate["task"] == "hello-seats" and gate["wait_for"] == "p2"
        pair = {"p1": gate["tested"], "p2": gate["support"]}
        cases[case] = {
            "task": gate["task"],
            "name": f"wait-{case}",
            "observe": True,
            "seats": pair,
            "multiplier": None,
        }
    assert len(cases) == 10 and BEHAVIORS.keys() < cases.keys()
    return cases


def _participant(seat: str, behavior: str) -> Record:
    if behavior == "oracle":
        return {"seat": seat, "name": "oracle", "env": dict(STOCK[seat])}
    return {"seat": seat, "import_path": LIFECYCLE, "kwargs": {"behavior": behavior}}


def config(case: Record, trials_dir: str) -> Record:
    task = EXAMPLES / "tasks" / case["task"]
    assert task.is_dir() and trials_dir
    document: Record = {
        "task": {"path": str(task)},
        "trial_name": case["name"],
        "trials_dir": os.path.abspath(trials_dir),
        "environment": {
            **ENVIRONMENT,
            **({"env": dict(WORLD[case["task"]])} if case["task"] in WORLD else {}),
        },
        "participants": [
            _participant(seat, behavior) for seat, behavior in case["seats"].items()
        ],
    }
    if case["multiplier"] is not None:
        document["agent_timeout_multiplier"] = case["multiplier"]
    return document


def main(argv: list[str]) -> int:
    cases = _roster()
    parser = argparse.ArgumentParser(
        prog=PROG, description="Print a native lifecycle TrialConfig.", epilog=EPILOG
    )
    parser.add_argument(
        "case", choices=["list", *cases], help="list, or one registered case"
    )
    parser.add_argument(
        "--trials-dir", help="fresh independent trials directory; required for CASE"
    )
    args = parser.parse_args(argv)
    if args.case == "list":
        if args.trials_dir is not None:
            parser.error("list takes no --trials-dir")
        for case, spec in cases.items():
            seats = " ".join(
                f"{seat}={behavior}" for seat, behavior in spec["seats"].items()
            )
            flag = "on" if spec["observe"] else "off"
            print(
                f"{case} task={spec['task']} trial_name={spec['name']} seat-events={flag} {seats}"
            )
        return 0
    if not args.trials_dir:
        parser.error("CASE needs --trials-dir")
    print(json.dumps(config(cases[args.case], args.trials_dir), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
