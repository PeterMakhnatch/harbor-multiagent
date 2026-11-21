import json
from hashlib import sha256
from itertools import islice
from pathlib import Path

from .. import decisions, evidence
from .. import native_panel as panel
from ..attention import protocol as aliases
from ..dispatch_answers import audit, protocol
from ..dispatch_answers.__main__ import experiment
from ..export_capsules import reconstructed_capsules

_PUBLIC = Path(__file__).resolve().parents[1] / "results/dispatch-answer-transfer.json"
_PUBLIC_SHA256 = "1b102fbd97cf095daf146742ba2673f262e03c366bd22b705935de817889faf0"


def _public_row(world: int) -> dict:
    raw = _PUBLIC.read_bytes()
    if sha256(raw).hexdigest() != _PUBLIC_SHA256:
        raise ValueError("The frozen dispatch transfer results changed")
    report = json.loads(raw)
    visible = [row for row in report["rows"] if row["arm"] == "visible"]
    if (
        sorted(row["world"] for row in visible) != list(protocol.SEEDS)
        or len({row["trial_ref"] for row in visible}) != len(visible)
        or len({row["result_sha256"] for row in visible}) != len(visible)
    ):
        raise ValueError("Expected exactly one frozen visible trial per world")
    row = next(row for row in visible if row["world"] == world)
    if (
        any(
            row[key] is not True
            for key in (
                "complete",
                "behavior_confirmed",
                "turn_usage_verified",
                "aggregate_usage_verified",
            )
        )
        or row["settled_calls"] != len(protocol.STAGES)
        or row["held_calls"] != 0
        or row["calls_without_reservation"] != 0
    ):
        raise ValueError("The frozen visible trial is incomplete")
    return row


def _check_calls(turns: list[dict], calls: list[dict]) -> None:
    for turn, call in zip(turns, calls, strict=True):
        usage = turn["usage"]
        if (
            call["status"] != "settled"
            or call["run"] != turn["run"]
            or call["role"] != turn["role"]
            or call["served_model"] != usage["served_model"]
            or call["cache_hit"] != usage["cache_tokens"]
            or call["cache_hit"] + call["cache_miss"] != usage["input_tokens"]
            or call["output_tokens"] != usage["output_tokens"]
        ):
            raise ValueError("Native turn usage differs from the frozen receipt")


def _source(
    study: Path, cell: dict, public: dict
) -> tuple[dict, evidence.TrialEvidence, dict]:
    root = panel._checked_study(study)
    teams = panel.teams
    teams._check_path(root, 0o700, directory=True)
    teams._check_path(root / "cells", 0o700, directory=True)
    spec = experiment()
    plan = panel._plan(spec, list(protocol.grid()), "direct", None)
    registration = json.loads(teams._read_sealed(root / "register.json"))
    if teams._bytes(registration["plan"]) != teams._bytes(plan):
        raise ValueError("Original study does not match the registered sources")

    trial = teams._trial_dir(root, cell)
    if trial.resolve(strict=True) != trial or list(
        islice(trial.parent.iterdir(), 2)
    ) != [trial]:
        raise ValueError("Expected exactly one native trial in the visible cell")
    result = trial / "result.json"
    if not result.is_file() or result.resolve(strict=True) != result:
        raise ValueError("Expected an original native result file")
    raw = result.read_bytes()
    if sha256(raw).hexdigest() != public["result_sha256"]:
        raise ValueError("Native result differs from the frozen result hash")
    native = json.loads(raw)
    trial_id = evidence._trial_id(native)
    reference = sha256(b"seatlab-confirmation:v1:" + trial_id.encode("ascii"))
    if reference.hexdigest() != public["trial_ref"]:
        raise ValueError("Native UUID differs from the frozen trial reference")
    observed = panel._observed(spec, root, cell, plan, None)
    if observed["state"] != "complete":
        raise ValueError("The native visible trial did not pass the dispatch audit")
    audit_result = observed["outcome"]
    receipt, dossier = evidence.read_trial_with_world(trial)
    if (
        audit_result["trial_id"] != trial_id
        or receipt["trial_id"] != trial_id
        or receipt["fingerprint"] != audit_result["fingerprint"]
        or audit_result["native_score"] != public["reward"]
        or receipt["score"]["optimal"] / protocol.EPISODES != public["reward"]
        or not audit_result["delivered_answer_sha256"]
        or len(audit_result["turns"]) != len(protocol.STAGES)
        or result.read_bytes() != raw
    ):
        raise ValueError("Native UUID, world, score, or answer delivery differs")
    _check_calls(audit_result["turns"], public["calls"])
    return native, receipt, dossier


def _packet(
    native: dict, receipt: evidence.TrialEvidence, dossier: dict, cell: dict
) -> tuple[dict, dict]:
    world, cell_id = cell["seed"], cell["cell_id"]
    records = evidence._by_role(native["seat_results"], "seat_results")
    capsules = reconstructed_capsules(dossier)
    _, inventory = audit._seat(records, capsules, dossier, cell_id, "inventory")
    _, dispatch = audit._seat(records, capsules, dossier, cell_id, "dispatch")
    cases = audit.attention._capsule_cases(capsules["dispatch"])
    mapping = aliases.aliases(cases, world, 0)
    question = protocol.parse_question(dispatch["question"][0])
    answers = protocol.parse_answers(inventory["answer"][0])
    text, turn = dispatch["decide"]
    prompt = protocol.decide_prompt(cases, world, question, answers)
    audit.attention._check_prompt(turn, prompt)
    context = json.loads(prompt.split("\n", 1)[1])

    selected = aliases.decide(text, cases, world, 0)
    by_id = {row["id"]: row["bay"] for row in selected["rows"]}
    stock = {row["id"]: row for row in decisions.describe_decisions(receipt, dossier)}
    draft = {alias: by_id[case_id] for alias, case_id in mapping.items()}
    grade = {
        alias: stock[case_id]["truth_optimal"] for alias, case_id in mapping.items()
    }
    if any(
        draft[alias] != stock[case_id]["chosen"]
        or (draft[alias] == grade[alias]) != stock[case_id]["is_optimal"]
        for alias, case_id in mapping.items()
    ):
        raise ValueError(
            "The reconstructed draft differs from the native decision or score"
        )
    return {**context, "draft": draft}, grade


def load(study: Path, world: int) -> dict:
    if type(world) is not int or world not in protocol.SEEDS:
        raise ValueError("Expected a registered dispatch world")
    public = _public_row(world)
    cell = next(
        cell
        for cell in protocol.grid()
        if cell["seed"] == world and cell["arm"] == "visible"
    )
    native, receipt, dossier = _source(study, cell, public)
    context, grade = _packet(native, receipt, dossier, cell)
    return {
        "world": world,
        "result_sha256": public["result_sha256"],
        "trial_ref": public["trial_ref"],
        "context": context,
        "grade": grade,
    }
