"""Simulated four-turn receipts only. Never export these fixtures as experiment data."""

import json
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, override
from unittest import TestCase
from uuid import NAMESPACE_URL, uuid5

from examples.seatlab.briefing import protocol, receipts
from examples.seatlab.native_receipts import audit_import_path

_MODEL = "openai-codex/gpt-0.0-simulated"
_STAGES = (
    ("inventory", "inventory"),
    ("brief", "dispatch"),
    ("push", "inventory"),
    ("decide", "dispatch"),
)


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _plan(cells: list[dict[str, Any]]) -> dict[str, Any]:
    with audit_import_path():
        from examples.seatlab.run_panel import _source_names

    return {
        "schema": "seatlab.native-briefing-plan/v1",
        "transport": "commands",
        "command_config_sha256": _sha("simulated config"),
        "episodes": 6,
        "max_direct_calls": 0,
        "sources": {name: _sha(name) for name in _source_names("arrival")},
        "seat_sources": {
            name: _sha(name)
            for name in (
                "agent.py",
                "protocol.py",
                "channel.py",
                "shared/attention/agent.py",
                "shared/attention/protocol.py",
                "shared/seat_agent.py",
                "shared/visibility/native_pairs.py",
                "shared/visibility/route_cli.py",
                "shared/visibility/seat_command.py",
            )
        },
        "cells": cells,
    }


def _turn(cell_id: str, stage: str, role: str) -> dict[str, Any]:
    run = f"{cell_id}-{stage}"
    digest_key = stage if stage in ("inventory", "brief") else run
    return {
        "stage": stage,
        "run": run,
        "role": role,
        "status": "received",
        "prompt_sha256": _sha(f"simulated prompt {digest_key}"),
        "response_sha256": _sha(f"simulated reply {digest_key}"),
        "usage": None,
    }


def _outcome(cell: dict[str, Any]) -> dict[str, Any]:
    cell_id = cell["cell_id"]
    return {
        "trial_id": str(uuid5(NAMESPACE_URL, f"simulated-{cell_id}")),
        "fingerprint": _sha(f"simulated world {cell['seed']}"),
        "native_score": 4 / 6,
        "actions": [
            {"id": i, "choice": "A", "optimal": i < 4, "feasible": True}
            for i in range(6)
        ],
        "opened": ["Q1", "Q2"],
        "opened_report_changes": 1,
        "total_report_changes": 3,
        "opened_stock_changes": 0,
        "opened_sensitive": 0,
        "source_copy_errors": 2,
        "brief_correct": 5,
        "brief_slots": 6,
        "turns": [_turn(cell_id, stage, role) for stage, role in _STAGES],
    }


class TestSimulatedBriefingReceipts(TestCase):
    @override
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.report_path = root / "report.json"
        self.directories = {role: root / role for role in ("inventory", "dispatch")}
        for directory in self.directories.values():
            directory.mkdir(mode=0o700)
        cells = list(protocol.grid())
        self.report: dict[str, Any] = {
            "schema": "seatlab.native-briefing-report/v1",
            "plan": _plan(cells),
            "rows": [
                {
                    **cell,
                    "state": "complete" if i < 2 else "unclaimed",
                    "outcome": _outcome(cell) if i < 2 else None,
                }
                for i, cell in enumerate(cells)
            ],
        }
        self._publish()
        self.paths: dict[tuple[int, str], Path] = {}
        for i, cell in enumerate(cells[:2]):
            for j, (stage, role) in enumerate(_STAGES):
                turn = _turn(cell["cell_id"], stage, role)
                path = self.directories[role] / f"{4 * i + j:032x}.json"
                _write(
                    path,
                    {
                        "schema": "seatlab.omp-seat-receipt/v1",
                        "served_model": _MODEL,
                        "prompt_sha256": turn["prompt_sha256"],
                        "reply_sha256": turn["response_sha256"],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 4,
                            "cached_tokens": 3,
                            "total_tokens": 16,
                        },
                    },
                )
                path.chmod(0o600)
                self.paths[i, stage] = path

    def _publish(self) -> None:
        with audit_import_path():
            from examples.seatlab.briefing import audit

        self.report["summary"] = audit.summarize(
            self.report["plan"]["cells"], self.report["rows"]
        )
        _write(self.report_path, self.report)

    def _collect(self) -> dict[str, Any]:
        return receipts.audit_receipts(
            self.report_path,
            self.directories["inventory"],
            self.directories["dispatch"],
            _MODEL,
        )

    def test_all_four_stages_attest_only_service_usage(self) -> None:
        original = self.report_path.read_bytes()
        totals = {
            "matched": 4,
            "input_tokens": 48,
            "output_tokens": 16,
            "cache_tokens": 12,
        }
        self.assertEqual(
            self._collect(),
            {
                "schema": "seatlab.native-briefing-external/v1",
                "verified_served_model": _MODEL,
                "native_trials": 2,
                "known_fields": ["input_tokens", "output_tokens", "cache_tokens"],
                "roles": {"inventory": totals, "dispatch": totals},
                "native_model_usage_declared": False,
            },
        )
        self.assertEqual(self.report_path.read_bytes(), original)

    def test_crossed_private_receipts_return_only_a_generic_diagnostic(self) -> None:
        push, brief = self.paths[0, "push"], self.paths[0, "brief"]
        push_body, brief_body = push.read_bytes(), brief.read_bytes()
        push.write_bytes(brief_body)
        brief.write_bytes(push_body)
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = receipts.main(
                [
                    str(self.report_path),
                    "--inventory-receipts",
                    str(self.directories["inventory"]),
                    "--dispatch-receipts",
                    str(self.directories["dispatch"]),
                    "--model",
                    _MODEL,
                ]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(), "Cannot verify native briefing receipt evidence.\n"
        )

    def test_each_stage_requires_a_receipt_even_for_repeated_prompts(self) -> None:
        for stage, _ in _STAGES:
            with self.subTest(stage=stage):
                path = self.paths[1, stage]
                body = path.read_bytes()
                path.unlink()
                with self.assertRaises(ValueError):
                    self._collect()
                path.write_bytes(body)
                path.chmod(0o600)

    def test_extra_copy_of_a_matching_receipt_is_rejected(self) -> None:
        source = self.paths[0, "brief"]
        extra = source.with_name(f"{8:032x}.json")
        extra.write_bytes(source.read_bytes())
        extra.chmod(0o600)
        with self.assertRaises(ValueError):
            self._collect()

    def test_incomplete_attempt_cannot_extend_the_verified_scope(self) -> None:
        self.report["rows"][1].update(state="unverified")
        with self.assertRaises(ValueError):
            self._publish()
        _write(self.report_path, self.report)
        with self.assertRaises(ValueError):
            self._collect()
        self.report["rows"][1]["outcome"] = None
        self._publish()
        with self.assertRaises(ValueError):
            self._collect()
        for stage, _ in _STAGES:
            self.paths[1, stage].unlink()
        self.assertEqual(self._collect()["native_trials"], 1)

    def test_wrong_served_model_or_invalid_service_usage_is_rejected(self) -> None:
        path = self.paths[0, "push"]
        body = path.read_bytes()
        for change in ("served_model", "total_tokens"):
            with self.subTest(change=change):
                receipt = json.loads(body)
                if change == "served_model":
                    receipt[change] = "openai-codex/gpt-0.1-simulated"
                else:
                    receipt["usage"][change] += 1
                _write(path, receipt)
                with self.assertRaises(ValueError):
                    self._collect()
