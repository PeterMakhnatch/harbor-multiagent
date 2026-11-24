import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from ..native_evidence import Incomplete, Invalid, read_bytes, same, strict_object
from ..native_study import require
from . import controls, replay
from . import inflight_verify as verify

USAGE = "usage: python3.12 -BS -m examples.ship_inflight.export QUESTION TASK ROOT [ROOT ...]"
GRADE_SOURCE = "native_reward_equals_replay"
RECIPIENT = {
    "positive": "pass",
    "zero": "pass",
    "mismatch": "fail",
    "native_error": "fail",
    "disposition_unknown": "exit_unknown",
}
NON_RETURN = frozenset(controls.DISPOSITIONS) - {"returned"}
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
Record = dict[str, Any]

assert set(RECIPIENT) == set(controls.GRADED)
assert tuple(replay.EXPECTED) == tuple(controls.EXPECTED)
assert tuple(replay.DISPOSITIONS) == tuple(controls.DISPOSITIONS)
assert replay.SEAT_KEYS == frozenset(controls.ROLES)
assert replay.PUBLIC_CUT_KEYS == verify.CUT_KEYS - {"run"}


def _seats(row: Record) -> dict[str, str | None]:
    ticket = row["ticket"]
    dispositions = row["dispositions"]
    require(
        type(dispositions) is dict and set(dispositions) == set(controls.ROLES),
        f"{ticket} seat dispositions are not one per role",
    )
    seats: dict[str, str | None] = {}
    for role in controls.ROLES:
        value = dispositions[role]
        require(
            value == "unknown" or value in controls.DISPOSITIONS,
            f"{ticket} {role} disposition is not a contract value",
        )
        seats[role] = None if value == "unknown" else value
    return seats


def _exportable(row: Record) -> bool:
    status, ticket = row["status"], row["ticket"]
    if status == "missing":
        return False
    require(status in RECIPIENT, f"{ticket} raw evidence is {status}: {row['reason']}")
    require(
        row["grade_source"] == GRADE_SOURCE and type(row["observed"]) is dict,
        f"{ticket} grade is not verified by replay",
    )
    if status == "native_error":
        seats = _seats(row)
        require(
            any(seats[role] in NON_RETURN for role in controls.ROLES),
            f"{ticket} native fault is not a seat non-return a public row carries:"
            f" {row['reason']}",
        )
    return True


def _locate(roots: list[controls.Root], tickets: list[str]) -> dict[str, Path]:
    claims, _ = controls._claims(roots, frozenset(tickets))
    trials: dict[str, Path] = {}
    for ticket in tickets:
        found = claims.get(ticket, [])
        require(
            len(found) == 1 and found[0][1],
            f"{ticket} is not one plain trial on reread",
        )
        trials[ticket] = roots[found[0][0]].path / "trials" / ticket
    return trials


def _reread(trial: Path, ticket: str) -> tuple[Record, Record, Record, Record]:
    try:
        raw = read_bytes(trial, controls.CUT, verify.stock.MAX_CUT_BYTES)
        cut = cast(Record, verify.stock.parse_cut(raw))
        evaluation = verify.evaluate(cut)
        published = strict_object(
            read_bytes(trial, controls.OBSERVATION, controls.MAX_OBSERVATION_BYTES),
            "observation",
        )
        recorded = strict_object(
            read_bytes(trial, controls.REWARD, controls.MAX_SMALL_BYTES),
            "verifier reward",
        )
    except (Incomplete, Invalid) as error:
        raise ValueError(f"{ticket} reread failed: {error}") from None
    except verify.CutError:
        raise ValueError(f"{ticket} reread cut no longer replays") from None
    return cut, evaluation, published, recorded


def _public_cut(trial: Path, case: Record, observed: Record, reward: int) -> Record:
    ticket = case["ticket"]
    cut, evaluation, published, recorded = _reread(trial, ticket)
    require(
        (evaluation["fate"], evaluation["channel"]) == (case["fate"], case["channel"]),
        f"{ticket} reread cut world is not the registered case",
    )
    require(
        same({field: evaluation[field] for field in controls.EXPECTED}, observed),
        f"{ticket} reread cut changes the verified observation",
    )
    require(
        same(published, evaluation),
        f"{ticket} reread observation differs from the reread cut",
    )
    require(
        evaluation["reward"] == reward and same(recorded, {"reward": reward}),
        f"{ticket} reread reward differs from the verified native reward",
    )
    public = {key: value for key, value in cut.items() if key != "run"}
    require(set(public) == replay.PUBLIC_CUT_KEYS, f"{ticket} public cut fields differ")
    return public


def _public_row(row: Record, case: Record, trial: Path) -> Record:
    ticket = case["ticket"]
    observed, reward = row["observed"], row["reward"]
    require(
        type(reward) is int and reward in (0, 1) and reward == observed["reward"],
        f"{ticket} verified reward is not one scalar 0/1",
    )
    native = {"seats": _seats(row), "reward_valid": True, "reward": reward}
    public = {
        "ticket": ticket,
        "native": native,
        "cut": _public_cut(trial, case, observed, reward),
    }
    replay.check_native(native, ticket)
    received = replay.replay(case, public)
    require(
        received["status"] == RECIPIENT[row["status"]]
        and same(received["replay"], observed),
        f"{ticket} recipient replay reads another verdict than the raw score",
    )
    return public


def export(question: Path, task: str, paths: list[Path]) -> Record:
    question_sha, task_files, registered = replay.load_source(question)
    contract = controls.load_contract(question, task)
    require(
        contract.question == question_sha and contract.files == task_files,
        "question or task files changed between recipient and raw checks",
    )
    tickets = [case["ticket"] for case in contract.cases]
    require(
        tickets == [case["ticket"] for case in registered],
        "raw and recipient registrations order tickets differently",
    )
    before = controls.score(contract, paths)
    rows = list(zip(contract.cases, before["cases"], strict=True))
    require(
        all(case["ticket"] == row["ticket"] for case, row in rows),
        "score rows do not follow the registered case order",
    )
    chosen = [(case, row) for case, row in rows if _exportable(row)]
    roots = controls._roots(paths, contract.files)
    trials = _locate(roots, [case["ticket"] for case, _ in chosen])
    public = [_public_row(row, case, trials[case["ticket"]]) for case, row in chosen]
    after = controls.score(contract, paths)
    require(
        same(before, after),
        "trial roots changed during export; the snapshot is not stationary",
    )
    return {
        "schema": replay.SCHEMA,
        "question_sha256": question_sha,
        "task_files": task_files,
        "rows": public,
    }


def _labels(question: str, task: str, roots: list[str]) -> list[tuple[str, str]]:
    named = [(question, "QUESTION"), (task, "TASK")]
    named += [(root, f"ROOT{position}") for position, root in enumerate(roots, 1)]
    labels: dict[str, str] = {}
    for text, label in named:
        for spelling in (os.path.abspath(text), os.path.realpath(text)):
            if spelling != os.sep:
                labels.setdefault(spelling, label)
    return sorted(labels.items(), key=lambda item: len(item[0]), reverse=True)


def _fail(labels: list[tuple[str, str]], text: str) -> int:
    for spelling, label in labels:
        text = text.replace(spelling, label)
    sys.stderr.write(f"export: {controls._safe(UUID.sub('<uuid>', text))}\n")
    return 2


def main(argv: Sequence[str]) -> int:
    if len(argv) < 3 or any(arg.startswith("-") for arg in argv):
        sys.stderr.write(USAGE + "\n")
        return 2
    question, task, roots = argv[0], argv[1], list(argv[2:])
    labels = _labels(question, task, roots)
    try:
        pack = export(Path(question), task, [Path(root) for root in roots])
        text = json.dumps(pack, indent=2) + "\n"
        require(UUID.search(text) is None, "public pack would carry an identifier")
    except OSError as error:
        return _fail(labels, error.strerror or "storage failed")
    except ValueError as error:
        return _fail(labels, str(error))
    sys.stdout.write(text)
    sys.stdout.flush()
    sys.stderr.write(
        f"export: {len(pack['rows'])}/{controls.CASES} registered tickets exported;"
        " absent tickets omitted\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
