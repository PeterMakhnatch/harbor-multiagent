import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from examples.seatlab.handoff import external_audit

_MODEL = "openai-codex/gpt-9.2-sol"
_PACKET = "a" * 64
_PRIVATE = "private-text-not-for-output"
_STAGES = {
    "note": "b",
    "bound-select": "c",
    "anonymous-select": "d",
    "both-decide": "e",
}


def _row(index, *, state="claimed", callback_error=False):
    case, rep = f"case-{index // 2}", index % 2
    block = f"ho-{_PACKET[:12]}-{case}-r{rep}"
    complete = state == "claimed" and not callback_error
    order = ("bound", "anonymous")
    if (index // 2 + rep) % 2:
        order = order[::-1]
    stages = (
        ("note", *(f"{arm}-select" for arm in order), "both-decide")
        if complete
        else ("note",)
        if callback_error
        else ()
    )
    return {
        "block": block,
        "case": case,
        "cluster": "cluster",
        "rep": rep,
        "fingerprint": _PACKET,
        "state": state,
        "outcome": {
            "note_status": "valid" if complete else "missing",
            "note_bytes": 5 if complete else None,
            "note_sha256": "1" * 64 if complete else None,
            "arms": {
                arm: {
                    "status": "complete" if complete else "no_note",
                    "lookup": "R" if complete else None,
                    "decision_stage": "both-decide" if complete else None,
                    "choices": {"R": "A", "S": "B"} if complete else None,
                }
                for arm in order
            },
        },
        "turns": [
            {
                "run": f"{block}-{stage}",
                "role": "route_review" if stage == "note" else "route_decide",
                "status": "callback_error" if callback_error else "received",
                "prompt_sha256": _STAGES[stage] * 64,
                "response_sha256": None if callback_error else "f" * 64,
                "usage": None,
            }
            for stage in stages
        ],
    }


class ExternalAuditTest(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(scratch).resolve()
        self.report = self.root / "handoff.json"
        self.writer = self.root / "writer"
        self.successor = self.root / "successor"
        self.writer.mkdir(mode=0o700)
        self.successor.mkdir(mode=0o700)
        self.receipts = 0

    def _report(self, *rows):
        self.report.write_text(
            json.dumps(
                {
                    "schema": "seatlab.handoff-report/v1",
                    "packet_sha256": _PACKET,
                    "outcomes": rows,
                    "primary": {"private": _PRIVATE},
                    "meter": {"provider_id": _PRIVATE, "known_usd_low": 12345.67},
                }
            ),
            encoding="utf-8",
        )

    def _receipt(
        self, turn, *, prompt=10, cached=2, output=3, model=_MODEL, directory=None
    ):
        self.receipts += 1
        if directory is None:
            directory = (
                self.writer if turn["role"] == "route_review" else self.successor
            )
        path = directory / f"{self.receipts:032x}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "seatlab.omp-seat-receipt/v1",
                    "served_model": model,
                    "prompt_sha256": turn["prompt_sha256"],
                    "reply_sha256": turn["response_sha256"],
                    "usage": {
                        "prompt_tokens": prompt,
                        "cached_tokens": cached,
                        "completion_tokens": output,
                        "total_tokens": prompt + output,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def _run(self, *indices):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = external_audit.main(
                [
                    str(self.report),
                    "--writer-receipts",
                    str(self.writer),
                    "--successor-receipts",
                    str(self.successor),
                    "--model",
                    _MODEL,
                    "--indices",
                    *(str(index) for index in indices),
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def _reject(self, *indices):
        self.assertEqual(
            self._run(*indices), (1, "", "Cannot verify handoff receipt evidence.\n")
        )

    def test_duplicate_content_has_physical_multiplicity_not_per_turn_usage(self):
        first, second = _row(0), _row(1)
        self._report(first, second, _row(2, state="unclaimed"), None)
        for turn in first["turns"]:
            self._receipt(turn)
        for turn in second["turns"][1:]:
            self._receipt(turn, prompt=12, cached=4, output=5)
        self._reject(0, 1)
        self._receipt(second["turns"][0], prompt=12, cached=4, output=5)
        code, out, err = self._run(1, 0)
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["selected"], result["not_selected"]), (2, 2))
        self.assertEqual(result["complete_deliveries"], {"bound": 2, "anonymous": 2})
        self.assertEqual(
            result["roles"],
            {
                "writer": {
                    "matched": 2,
                    "callback_errors": 0,
                    "input_tokens": 22,
                    "output_tokens": 8,
                    "cache_tokens": 6,
                },
                "successor": {
                    "matched": 6,
                    "callback_errors": 0,
                    "input_tokens": 66,
                    "output_tokens": 24,
                    "cache_tokens": 18,
                },
            },
        )
        self.assertEqual([block["index"] for block in result["blocks"]], [0, 1])
        for block in result["blocks"]:
            self.assertEqual(block.keys(), {"index", "state", "note_status", "arms"})
            self.assertEqual(
                block["arms"], {"bound": "complete", "anonymous": "complete"}
            )
        self.assertEqual(result["requested_model"], _MODEL)
        self.assertEqual(result["verified_served_model"], _MODEL)
        for secret in (
            _PRIVATE,
            str(self.root),
            "provider_id",
            "known_usd_low",
            "choices",
        ):
            self.assertNotIn(secret, out)

    def test_rejects_wrong_role_wrong_model_and_surplus(self):
        row = _row(0)
        self._report(row, _row(1, state="unclaimed"))
        note, *turns = row["turns"]
        for turn in turns:
            self._receipt(turn)
        misplaced = self._receipt(note, directory=self.successor)
        self._reject(0)
        misplaced.unlink()
        wrong_model = self._receipt(note, model="openai-codex/gpt-9.2-mini")
        self._reject(0)
        wrong_model.unlink()
        self._receipt(note)
        surplus = self._receipt(note)
        self._reject(0)
        surplus.unlink()
        self.assertEqual(self._run(0)[0], 0)

    def test_rejects_duplicate_runs_indices_and_out_of_selection_attempts(self):
        first, second = _row(0), _row(1)
        for turn in first["turns"]:
            self._receipt(turn)
        self._report(first, second)
        self._reject(0)
        self._report(first, _row(1, state="unbound"))
        self._reject(0)
        self._report(first, _row(1, state="unclaimed"))
        self._reject(0, 0)
        self._reject(2)
        first["turns"][2] = first["turns"][1].copy()
        self._report(first, None)
        self._reject(0)

    def test_unknown_slots_and_callback_errors_do_not_count_as_deliveries(self):
        stopped = _row(0, callback_error=True)
        self._report(stopped, _row(1, state="unclaimed"), None, None)
        code, out, err = self._run(0, 1, 2)
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["selected"], result["not_selected"]), (3, 1))
        self.assertEqual(result["complete_deliveries"], {"bound": 0, "anonymous": 0})
        self.assertEqual(result["roles"]["writer"]["callback_errors"], 1)
        self.assertEqual(result["roles"]["writer"]["matched"], 0)
        self.assertEqual(
            [b["note_status"] for b in result["blocks"]],
            ["missing", "unknown", "unknown"],
        )
        self._receipt(stopped["turns"][0] | {"response_sha256": "f" * 64})
        self._reject(0, 1, 2)

    def test_rejects_unsafe_receipt_files_and_duplicate_json(self):
        row = _row(0)
        self._report(row, None)
        note, *turns = row["turns"]
        for turn in turns:
            self._receipt(turn)
        receipt = self._receipt(note)
        good = receipt.read_bytes()
        receipt.chmod(0o640)
        self._reject(0)
        receipt.chmod(0o600)
        self.writer.chmod(0o500)
        self._reject(0)
        self.writer.chmod(0o700)
        receipt.write_bytes(
            good.replace(
                b'"cached_tokens": 2', b'"cached_tokens": 2, "cached_tokens": 2'
            )
        )
        self._reject(0)
        real = self.root / "real.json"
        receipt.rename(real)
        real.write_bytes(good)
        receipt.symlink_to(real)
        self._reject(0)
        receipt.unlink()
        os.link(real, receipt)
        self._reject(0)
        receipt.unlink()
        real.rename(receipt)
        self.assertEqual(self._run(0)[0], 0)

    def test_rejects_wrong_packet_fingerprint(self):
        row = _row(0)
        for turn in row["turns"]:
            self._receipt(turn)
        row["fingerprint"] = _PACKET[:-1] + "b"
        self._report(row, None)
        self._reject(0)
