"""Build small synthetic Flotilla job folders for the tests.

A trial folder holds what Harbor and the Flotilla world write: result.json (with seat_results), the
world's events.jsonl and messages.jsonl, the verifier's score.json and world-state.json, and an ATIF
trajectory per seat. ``task_dir`` writes a task folder like flotilla/build_tasks.py does (task.json,
family.py, docker-compose.yaml with SEAT_BRIEFING lines).
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEDULE = Path(__file__).resolve().parents[2] / "families" / "schedule.py"
EPOCH = datetime(2026, 11, 19, 9, 0, 0, tzinfo=UTC)

TOY_FAMILY = """
FAMILY = "toy"
TITLE = "Add two numbers"


def oracle_answer(private):
    return {"x": sum(part["value"] for part in private)}


def score(answer, key):
    correct = isinstance(answer, dict) and answer.get("x") == key["x"]
    return {"score": 1.0 if correct else 0.0, "fields_correct": int(correct), "fields_total": 1}
"""


def iso(seconds: float) -> str:
    return (EPOCH + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def seat_result(
    seat: str,
    *,
    agent: str = "terminus-2",
    model: str | None = "deepseek/deepseek-v4-flash",
    provider: str | None = "openrouter",
    usage: tuple[int | None, int | None, int | None, float | None] | None = (1000, 400, 100, 0.001),
    under_test: bool = True,
    exception: str | None = None,
    start: float = 10.0,
) -> dict[str, Any]:
    n_in, n_cache, n_out, cost = usage if usage is not None else (None, None, None, None)
    return {
        "seat": seat,
        "under_test": under_test,
        "agent_info": {
            "name": agent,
            "version": "1",
            "model_info": {"name": model, "provider": provider} if model else None,
        },
        "agent_result": {
            "n_input_tokens": n_in,
            "n_cache_tokens": n_cache,
            "n_output_tokens": n_out,
            "cost_usd": cost,
        },
        "agent_execution": {"started_at": iso(start), "finished_at": iso(start + 60)},
        "exception_info": {
            "exception_type": exception,
            "exception_message": "",
            "exception_traceback": "",
            "occurred_at": iso(70),
        }
        if exception
        else None,
    }


def trial(
    job: Path,
    name: str,
    *,
    label: str = "toy",
    instance: int = 0,
    condition: str = "talk",
    reward: float | None = 1.0,
    reward_valid: bool | None = None,
    finished: bool = True,
    seats: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    submissions: list[dict[str, Any]] | None = None,
    score: dict[str, Any] | None = None,
    trajectories: dict[str, list[dict[str, Any]]] | None = None,
    task_path: str | None = None,
    result_id: str | None = None,
    world_state: bool = True,
) -> Path:
    """Write one trial folder and return it. ``trajectories`` maps a seat to ATIF steps."""
    directory = job / name
    seat_rows = seats if seats is not None else [seat_result("p1"), seat_result("p2")]
    seat_names = [s["seat"] for s in seat_rows]
    task_name = f"flotilla/{label}-{instance:03d}-{condition}"
    write_json(
        directory / "result.json",
        {
            "id": result_id or f"id-{name}",
            "task_name": task_name,
            "trial_name": name,
            "task_id": {"path": task_path or f"/nonexistent/{label}/{task_name.split('/')[1]}"},
            "agent_info": seat_rows[0]["agent_info"],
            "agent_result": None,
            "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
            "exception_info": None,
            "started_at": iso(0),
            "finished_at": iso(100) if finished else None,
            "environment_setup": {"started_at": iso(0), "finished_at": iso(5)},
            "seat_results": seat_rows,
            "reward_valid": (reward is not None) if reward_valid is None else reward_valid,
        },
    )
    world = directory / "artifacts" / "logs" / "world"
    subs = submissions or []
    msgs = messages or []
    evts = (
        events
        if events is not None
        else (
            [
                {
                    "seq": 1,
                    "t": 0.0,
                    "seat": None,
                    "kind": "start",
                    "data": {"condition": condition, "seats": seat_names},
                }
            ]
            + [
                {
                    "seq": i + 2,
                    "t": s["t"],
                    "seat": s["seat"],
                    "kind": "submit",
                    "data": {"n": s["n"], "answer": s["answer"]},
                }
                for i, s in enumerate(subs)
            ]
        )
    )
    write_jsonl(world / "events.jsonl", evts)
    write_jsonl(world / "messages.jsonl", msgs)
    if reward is not None:
        final = subs[-1] if subs else None
        write_json(
            directory / "verifier" / "score.json",
            score
            or {
                "family": label.split(".")[0],
                "instance": instance,
                "condition": condition,
                "seats": seat_names,
                "score": reward,
                "answer": final["answer"] if final else None,
                "submitted_by": final["seat"] if final else None,
                "n_submissions": len(subs),
                "n_messages": len(msgs),
            },
        )
        if world_state:
            write_json(
                directory / "verifier" / "world-state.json",
                {
                    "condition": condition,
                    "frozen": True,
                    "seats": seat_names,
                    "messages": msgs,
                    "submissions": subs,
                },
            )
    for seat, steps in (trajectories or {}).items():
        write_json(
            directory / "seats" / seat / "agent" / "trajectory.json",
            {"schema_version": "ATIF-v1.6", "session_id": f"{name}__{seat}", "steps": steps},
        )
    return directory


def step(
    step_id: int,
    at: float,
    *,
    keystrokes: list[str] | None = None,
    observation: str = "",
    source: str = "agent",
    message: str = "",
) -> dict[str, Any]:
    """One ATIF step; ``at`` is seconds after the fixture epoch (the environment setup start)."""
    row: dict[str, Any] = {"step_id": step_id, "timestamp": iso(at), "source": source, "message": message}
    if source == "agent":
        row["tool_calls"] = [
            {
                "tool_call_id": f"c{step_id}_{i}",
                "function_name": "bash_command",
                "arguments": {"keystrokes": k, "duration": 0.1},
            }
            for i, k in enumerate(keystrokes or [])
        ]
        row["observation"] = {"results": [{"content": observation}]}
    return row


def task_dir(
    root: Path,
    *,
    label: str = "toy",
    instance: int = 0,
    condition: str = "talk",
    briefings: dict[str, Any],
    answer_key: dict[str, Any],
    family_source: str | Path = TOY_FAMILY,
    extra: dict[str, Any] | None = None,
    instructions: dict[str, str] | None = None,
) -> Path:
    family = label.split(".")[0]
    variant = label.split(".")[1] if "." in label else ""
    directory = root / label / f"{label}-{instance:03d}-{condition}"
    env = directory / "environment"
    env.mkdir(parents=True, exist_ok=True)
    write_json(
        env / "task.json",
        {
            "family": family,
            "variant": variant,
            "instance": instance,
            "condition": condition,
            "seats": sorted(briefings),
            "answer_key": answer_key,
            **(extra or {}),
        },
    )
    if isinstance(family_source, Path):
        shutil.copy(family_source, env / "family.py")
    else:
        (env / "family.py").write_text(family_source)
    lines = ["services:", "  main:", "    environment:", '      FLOTILLA_TOKENS: "p1:secret"']
    for seat in sorted(briefings):
        lines += [
            f"  {seat}:",
            "    environment:",
            f"      SEAT: {json.dumps(seat)}",
            '      SEAT_TOKEN: "secret"',
            f"      SEAT_BRIEFING: {json.dumps(json.dumps(briefings[seat], sort_keys=True))}",
        ]
    (env / "docker-compose.yaml").write_text("\n".join(lines) + "\n")
    for seat, text in (instructions or {}).items():
        (directory / "instructions").mkdir(exist_ok=True)
        (directory / "instructions" / f"{seat}.md").write_text(text)
    return directory
