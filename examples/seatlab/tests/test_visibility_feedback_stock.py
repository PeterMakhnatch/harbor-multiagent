import json
from pathlib import Path
from runpy import run_path
from typing import Any

from examples.seatlab.visibility import feedback_stock


def _report() -> dict[str, Any]:
    seed = 5201
    asset = Path(__file__).resolve().parents[2] / (
        "tasks/dispatch-handoff/environment/seatworld.py"
    )
    world = run_path(str(asset))["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": 20,
            "condition": "pooled",
        }
    )
    rows = []
    for step, case in enumerate(world.evidence()["cases"][1::2]):
        utility = case["utilities"].__getitem__
        sensitive = max(case["before"], key=utility) != max(case["after"], key=utility)
        for arm in ("warm",) if step < 2 else ("lifetime", "recent"):
            rows.append(
                {
                    "arm": arm,
                    "step": step,
                    "native_id": case["id"],
                    "desk": ("X", "Y")[step] if step < 2 else None,
                    "current_desk": "Y",
                    "exact": (step == 1) if step < 2 else None,
                    "copied": True if step < 2 else None,
                    "bay": min(case["after"], key=utility) if step == 0 else None,
                    "optimal": False if step == 0 else None,
                    "feasible": True if step == 0 else None,
                    "source_sensitive": sensitive,
                    "status": ("complete", "missing_decide")[step]
                    if step < 2
                    else "blocked_history",
                }
            )
    return {
        "schema": "seatlab.feedback-report/v1",
        "blocks": [
            {
                "seed": seed,
                "regime": "stable",
                "rep": 0,
                "block": f"hz-s{seed}-stable-r0",
                "state": "claimed",
                "rows": rows,
            },
            {
                "seed": seed,
                "regime": "switch",
                "rep": 0,
                "block": f"hz-s{seed}-switch-r0",
                "state": "unclaimed",
                "rows": [],
            },
        ],
    }


def test_stock_cli_rejects_an_observed_nonoptimal_bay_reported_as_optimal(
    tmp_path, capsys
):
    report = _report()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert feedback_stock.main([str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mismatches"] == 0
    assert result["actual_decisions"] == 1
    assert result["distinct_native_worlds"] == 1
    assert result["registered_shipments"] == 10
    assert result["coverage"]["switch"]["unclaimed_blocks"] == 1
    assert result["coverage"]["switch"]["actual_decisions"] == 0

    rows = report["blocks"][0]["rows"]
    rows[0]["optimal"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    assert feedback_stock.main([str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["mismatches"] == 1
    assert result["mismatches_by_field"] == {
        "optimal": 1,
        "feasible": 0,
        "source_sensitive": 0,
    }

    rows[0]["optimal"] = False
    rows.append(rows[0].copy())
    path.write_text(json.dumps(report), encoding="utf-8")
    assert feedback_stock.main([str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "incorrect block row count"
