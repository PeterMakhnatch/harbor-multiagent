import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from examples.seatlab.native_report import Record, read_report, summarize_attention

_ROOT = Path(__file__).resolve().parents[3]
_RESULTS = _ROOT / "examples/seatlab/results"
_DIRECT = _RESULTS / "attention-direct-live.json"
_COMMANDS = _RESULTS / "attention-subscription-live.json"


def _load(path: Path = _DIRECT) -> Record:
    return json.loads(path.read_text(encoding="utf-8"))


class TestNativeReport(unittest.TestCase):
    def test_rejects_score_inconsistent_with_actions(self) -> None:
        report = _load()
        rows = report["rows"]
        rows[0]["outcome"]["native_score"] = 0.0
        with self.assertRaisesRegex(ValueError, "reward differs"):
            summarize_attention(report["plan"]["cells"], rows)

    def test_rejects_invalid_register_and_incomplete_state(self) -> None:
        report = _load()
        cells, rows = report["plan"]["cells"], report["rows"]
        with self.assertRaisesRegex(ValueError, "duplicate registered cell"):
            summarize_attention([*cells[:-1], cells[0]], rows)
        with self.assertRaisesRegex(ValueError, "exactly the registered cells"):
            summarize_attention(cells, rows[:-1])
        with self.assertRaisesRegex(ValueError, "state and outcome disagree"):
            summarize_attention(cells, [dict(rows[0], state="claimed"), *rows[1:]])
        with self.assertRaisesRegex(ValueError, "state and outcome disagree"):
            summarize_attention(cells, [dict(rows[0], outcome=None), *rows[1:]])

    def test_missing_pairs_preserve_known_actions_and_finite_bounds(self) -> None:
        report = _load(_COMMANDS)
        rows = report["rows"]
        keep = {
            (6401, 0, "named"),
            (6401, 0, "count"),
            (6402, 0, "named"),
        }
        scores = {}
        for row in rows:
            identity = row["seed"], row["repetition"], row["arm"]
            if identity in keep:
                scores[identity] = row["outcome"]["native_score"]
            else:
                row.update(state="unclaimed", outcome=None)
        paired_delta = scores[6401, 0, "named"] - scores[6401, 0, "count"]
        lone_treatment = scores[6402, 0, "named"]
        summary = summarize_attention(report["plan"]["cells"], rows)
        self.assertEqual(summary["missing_cells"], 21)
        self.assertEqual(summary["secondary"]["named"]["inventory_model_unreported"], 2)
        primary = summary["primary"]
        self.assertIsNone(primary["estimate"])
        self.assertEqual(primary["matched_pairs"], 1)
        self.assertEqual(primary["paired_seeds"], [6401])
        self.assertEqual(primary["seed_blocks"][0]["mean"], paired_delta)
        bounds = primary["registered_bounds"]
        self.assertAlmostEqual(
            bounds["lower"], (paired_delta + lone_treatment - 11) / 12
        )
        self.assertAlmostEqual(
            bounds["upper"], (paired_delta + lone_treatment + 10) / 12
        )

    def test_rejects_invalid_json_and_transport_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            malformed = ('{"a":1,"a":2}', '{"a":1e999}', '{"a":NaN}', "{}{}")
            for payload in malformed:
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "bounded JSON file"):
                        read_report(path)
            report = _load()
            report["rows"][0]["outcome"]["turns"][0]["usage"] = None
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "usage for the native transport"):
                read_report(path)

    def test_cli_recomputes_each_published_summary_without_site_packages(self) -> None:
        child = subprocess.run(
            [
                sys.executable,
                "-E",
                "-S",
                "-m",
                "examples.seatlab.native_report",
                "--json",
                str(_DIRECT),
                str(_COMMANDS),
            ],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            json.loads(child.stdout),
            [_load(_DIRECT)["summary"], _load(_COMMANDS)["summary"]],
        )
