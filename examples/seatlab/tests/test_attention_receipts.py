import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from examples.seatlab.attention import receipts
from examples.seatlab.attention.__main__ import _plan
from examples.seatlab.handoff.external_audit import _MAX_REPORT
from examples.seatlab.tests import test_attention_audit as native

audit = native.audit
protocol = native.protocol

_COMMANDS_DIGEST = sha256(b'{"commands":{}}').hexdigest()
_MODEL = "openai-codex/gpt-9.2-sol"
_DIAGNOSTIC = "Cannot verify native attention receipt evidence.\n"


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@dataclass
class Study:
    report: dict[str, Any]
    report_path: Path
    directories: dict[str, Path]

    def publish(self, *, summarize: bool = True) -> None:
        if summarize:
            self.report["summary"] = audit.summarize(
                list(protocol.grid()), self.report["rows"]
            )
        _write(self.report_path, self.report)

    def turns(self) -> list[dict[str, Any]]:
        return [
            turn
            for row in self.report["rows"]
            if row["outcome"] is not None
            for turn in row["outcome"]["turns"]
        ]

    def receipt(self, turn: dict[str, Any], n: int) -> Path:
        path = self.directories[turn["role"]] / f"{n:032x}.json"
        _write(
            path,
            {
                "schema": "seatlab.omp-seat-receipt/v1",
                "served_model": _MODEL,
                "prompt_sha256": turn["prompt_sha256"],
                "reply_sha256": turn["response_sha256"],
                "usage": {
                    "prompt_tokens": 10 + n,
                    "cached_tokens": n,
                    "completion_tokens": 4,
                    "total_tokens": 14 + n,
                },
            },
        )
        path.chmod(0o600)
        return path

    def run(self, *, dispatch: Path | None = None) -> int:
        return receipts.main(
            [
                str(self.report_path),
                "--inventory-receipts",
                str(self.directories["inventory"]),
                "--dispatch-receipts",
                str(dispatch or self.directories["dispatch"]),
                "--model",
                _MODEL,
            ]
        )


@pytest.fixture
def study(tmp_path: Path) -> Study:
    root = tmp_path.resolve()
    trial, config, named, _, dispatch = native._trial(root, "commands")
    outcome = audit.inspect_trial(trial, config, named, "commands")
    cells = list(protocol.grid())
    count = next(
        cell
        for cell in cells
        if cell["seed"] == named["seed"]
        and cell["repetition"] == named["repetition"]
        and cell["arm"] == "count"
    )
    paired = deepcopy(outcome)
    paired["trial_id"] = str(uuid5(NAMESPACE_URL, count["cell_id"]))
    for turn in paired["turns"]:
        turn["run"] = f"{count['cell_id']}-{turn['stage']}"
    prompt = protocol.select_prompt(
        dispatch["cases"], count["seed"], count["repetition"], "count"
    )
    paired["turns"][1]["prompt_sha256"] = sha256(prompt.encode("utf-8")).hexdigest()
    outcomes = {named["cell_id"]: outcome, count["cell_id"]: paired}
    rows = [
        {
            **cell,
            "state": "complete" if cell["cell_id"] in outcomes else "unclaimed",
            "outcome": outcomes.get(cell["cell_id"]),
        }
        for cell in cells
    ]
    dirs = {role: root / role for role in ("inventory", "dispatch")}
    for directory in dirs.values():
        directory.mkdir(mode=0o700)
    subject = Study(
        {
            "schema": "seatlab.native-attention-report/v1",
            "plan": _plan("commands", _COMMANDS_DIGEST),
            "rows": rows,
        },
        root / "report.json",
        dirs,
    )
    subject.publish()
    for i, turn in enumerate(subject.turns(), 1):
        subject.receipt(turn, i)
    return subject


def _reject(study: Study, capsys, *, dispatch: Path | None = None) -> None:
    assert study.run(dispatch=dispatch) == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", _DIAGNOSTIC)


def test_exact_duplicate_multiset_and_sanitized_usage(study: Study, capsys) -> None:
    first, second = study.turns()[::3]
    assert first["prompt_sha256"] == second["prompt_sha256"]
    assert first["response_sha256"] == second["response_sha256"]
    assert study.run() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "schema": "seatlab.native-attention-external/v1",
        "verified_served_model": _MODEL,
        "native_trials": 2,
        "known_fields": ["input_tokens", "output_tokens", "cache_tokens"],
        "roles": {
            "inventory": {
                "matched": 2,
                "input_tokens": 25,
                "output_tokens": 8,
                "cache_tokens": 5,
            },
            "dispatch": {
                "matched": 4,
                "input_tokens": 56,
                "output_tokens": 16,
                "cache_tokens": 16,
            },
        },
        "native_model_usage_declared": False,
    }
    duplicate = study.directories["inventory"] / f"{4:032x}.json"
    duplicate.unlink()
    _reject(study, capsys)
    study.receipt(second, 4)
    study.receipt(second, 7)
    _reject(study, capsys)


@pytest.mark.parametrize(
    "mutation", ["model", "role", "cached", "sum", "usage_field", "permissions"]
)
def test_receipt_mutations_do_not_change_native_grade(
    study: Study, capsys, mutation
) -> None:
    inventory = study.directories["inventory"] / f"{1:032x}.json"
    entry = json.loads(inventory.read_bytes())
    native = study.report_path.read_bytes()
    if mutation == "model":
        entry["served_model"] = "openai-codex/gpt-9.2-mini"
    elif mutation == "role":
        dispatch = study.directories["dispatch"] / f"{2:032x}.json"
        inventory.write_bytes(dispatch.read_bytes())
        _write(dispatch, entry)
    elif mutation == "cached":
        entry["usage"]["cached_tokens"] = entry["usage"]["prompt_tokens"] + 1
    elif mutation == "sum":
        entry["usage"]["total_tokens"] += 1
    elif mutation == "usage_field":
        entry["usage"]["reasoning_tokens"] = 2
    else:
        inventory.chmod(0o640)
    if mutation != "role":
        _write(inventory, entry)
    _reject(study, capsys)
    assert study.report_path.read_bytes() == native


@pytest.mark.parametrize(
    "mutation",
    [
        "score",
        "summary",
        "unverified",
        "complete_without_outcome",
        "role",
        "order",
        "usage",
        "wrong_grid",
        "direct",
        "meter",
        "duplicate_trial",
    ],
)
def test_native_report_mutations_reject_real_receipt_set(
    study: Study, capsys, mutation
) -> None:
    first, second, missing, *_ = study.report["rows"]
    outcome = first["outcome"]
    turn = outcome["turns"][0]
    refresh = False
    if mutation == "score":
        outcome["native_score"] += 1 / 6
        refresh = True
    elif mutation == "summary":
        study.report["summary"]["primary"]["matched_pairs"] = 2
    elif mutation == "unverified":
        first["state"] = "unverified"
    elif mutation == "complete_without_outcome":
        missing["state"] = "complete"
    elif mutation == "role":
        turn["role"] = "dispatch"
    elif mutation == "order":
        outcome["turns"][1:] = outcome["turns"][:0:-1]
    elif mutation == "usage":
        turn["usage"] = {
            "served_model": _MODEL,
            "input_tokens": 11,
            "output_tokens": 4,
            "cache_tokens": 1,
        }
        refresh = True
    elif mutation == "wrong_grid":
        study.report["plan"]["cells"][0]["seed"] += 1
    elif mutation == "direct":
        study.report["plan"]["transport"] = "direct"
    elif mutation == "meter":
        study.report["meter"] = {}
    else:
        second["outcome"]["trial_id"] = outcome["trial_id"]
    study.publish(summarize=refresh)
    _reject(study, capsys)


def test_unverified_receipts_cannot_be_linked_to_remaining_trials(
    study: Study, capsys
) -> None:
    study.report["rows"][1].update(state="unverified", outcome=None)
    study.publish()
    _reject(study, capsys)
    for directory, numbers in (("inventory", (4,)), ("dispatch", (5, 6))):
        for n in numbers:
            (study.directories[directory] / f"{n:032x}.json").unlink()
    assert study.run() == 0
    assert json.loads(capsys.readouterr().out)["native_trials"] == 1


def test_all_unknown_never_attests_service(study: Study, capsys) -> None:
    for row in study.report["rows"]:
        row.update(state="unclaimed", outcome=None)
    for directory in study.directories.values():
        for path in directory.iterdir():
            path.unlink()
    study.publish()
    _reject(study, capsys)


def test_protected_distinct_directories_and_bounded_report(
    study: Study, capsys
) -> None:
    _reject(study, capsys, dispatch=study.directories["inventory"])
    alias = study.report_path.with_name("alias")
    alias.symlink_to(study.directories["dispatch"], target_is_directory=True)
    _reject(study, capsys, dispatch=alias)
    with study.report_path.open("r+b") as report:
        report.truncate(_MAX_REPORT + 1)
    _reject(study, capsys)


def test_generic_verifier_requires_receipts_for_four_registered_stages(
    study: Study,
) -> None:
    stages = (
        ("inventory", "inventory"),
        ("select", "dispatch"),
        ("brief", "dispatch"),
        ("decide", "dispatch"),
    )
    schema = "seatlab.four-stage-receipt-test/v1"
    study.report["schema"] = schema
    for i, row in enumerate(study.report["rows"][:2], 7):
        turns = row["outcome"]["turns"]
        turn = {
            **turns[1],
            "stage": "brief",
            "run": f"{row['cell_id']}-brief",
            "prompt_sha256": sha256(f"Prompt {i}".encode()).hexdigest(),
            "response_sha256": sha256(f"Brief {i}".encode()).hexdigest(),
        }
        turns.insert(2, turn)
        extra = study.receipt(turn, i)
    study.publish(summarize=False)

    def verify(document):
        if document["plan"] != study.report["plan"]:
            raise ValueError

    def collect():
        return receipts.audit_native_receipts(
            study.report_path,
            study.directories["inventory"],
            study.directories["dispatch"],
            _MODEL,
            report_schema=schema,
            verify=verify,
            stages=stages,
        )

    assert collect()["roles"]["dispatch"]["matched"] == 6
    extra.unlink()
    with pytest.raises(ValueError):
        collect()
