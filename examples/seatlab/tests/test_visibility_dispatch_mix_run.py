import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory
from unittest.mock import patch

from examples.seatlab.visibility import dispatch_mix_plan as plan
from examples.seatlab.visibility import dispatch_mix_run as runner
from examples.seatlab.visibility import native_pairs


def _executable(path: Path, script: str) -> Path:
    path.write_text(f"#!/bin/sh\nset -eu\n{script}\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _claude(home: Path, subscription: str) -> None:
    payload = json.dumps(
        {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": subscription,
        }
    )
    _executable(
        home / "claude",
        'test "$*" = "auth status --json"\n'
        'test -z "${DEEPSEEK_API_KEY+x}${ANTHROPIC_API_KEY+x}${SEATLAB_SPEND_DIR+x}"\n'
        f"printf '%s\\n' '{payload}'",
    )


class DispatchMixRunTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = self.enterContext(TemporaryDirectory())
        self.home = Path(scratch).resolve()
        self.root = self.home / "study"
        self.harbor = _executable(
            self.home / "harbor",
            'test -z "${DEEPSEEK_API_KEY+x}${ANTHROPIC_API_KEY+x}${SEATLAB_SPEND_DIR+x}"\n'
            'case "$*" in\n'
            "  --version) printf '%s\\n' '0.26.0' ;;\n"
            "  'trial schema') printf '%s\\n' '{\"properties\":{\"participants\":{}}}' ;;\n"
            "  *) exit 91 ;;\n"
            "esac",
        )
        self.secret = "private-dummy-value-for-test"
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "HOME": str(self.home),
                    "PATH": str(self.home),
                    "DOCKER_HOST": "unix:///offline-test.sock",
                    "DEEPSEEK_API_KEY": self.secret,
                    "ANTHROPIC_API_KEY": self.secret,
                    "SEATLAB_SPEND_DIR": str(self.home / "ledger"),
                },
                clear=True,
            )
        )
        _claude(self.home, "max")

    def test_plan_and_config_bind_rosters_to_real_native_roles(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(runner.main(["plan"]), 0)
        document = json.loads(out.getvalue())
        self.assertEqual(document["episodes"], 6)
        self.assertEqual(len(document["cells"]), 32)
        self.assertEqual(len({cell["cell_id"] for cell in document["cells"]}), 32)
        self.assertEqual(
            [(cell["condition"], cell["pairing"]) for cell in document["cells"][:4]],
            [
                ("fresh", "cd"),
                ("fresh", "dc"),
                ("reordered", "dc"),
                ("reordered", "cd"),
            ],
        )
        for cell in plan.grid():
            with self.subTest(cell=cell["cell_id"]):
                registered = runner.config(self.root, cell)
                claude = "anthropic/claude-sonnet-5-5"
                direct = "deepseek/deepseek-flash"
                expected = (
                    (claude, direct) if cell["pairing"] == "cd" else (direct, claude)
                )
                self.assertEqual(registered["environment"]["type"], "docker")
                self.assertEqual(registered["verifier"], {"disable": False})
                self.assertEqual(
                    registered["participants"],
                    [
                        {
                            "seat": role,
                            "name": None,
                            "import_path": "examples.seatlab.visibility.dispatch_mix_seat:MixedSeat",
                            "model_name": model,
                            "kwargs": {"run_id": cell["cell_id"]},
                        }
                        for role, model in zip(
                            ("inventory", "dispatch"), expected, strict=True
                        )
                    ],
                )
        self.assertFalse(self.root.exists())

    def test_first_attempt_is_private_and_preserves_native_exit(self) -> None:
        index = 2
        cell = plan.grid()[index]
        directory = self.root / "cells" / cell["cell_id"]
        args = [
            "run",
            "--root",
            str(self.root),
            "--index",
            str(index),
            "--harbor",
            str(self.harbor),
        ]
        errors = io.StringIO()
        with (
            patch.object(native_pairs, "_launch", return_value=23) as launch,
            redirect_stderr(errors),
        ):
            self.assertEqual(runner.main(args), 1)
            self.assertEqual(runner.main(args), 1)
        launch.assert_called_once()
        target, harbor, env = launch.call_args.args
        self.assertEqual((target, harbor), (directory, self.harbor))
        host_only = {"DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "SEATLAB_SPEND_DIR"}
        self.assertTrue(host_only.isdisjoint(env))
        self.assertNotIn(str(self.home), errors.getvalue())
        self.assertNotIn(self.secret, errors.getvalue())
        self.assertEqual(list(directory.parent.iterdir()), [directory])
        for folder in (self.root, directory.parent, directory):
            self.assertEqual(S_IMODE(folder.stat().st_mode), 0o700)
        for name in ("attempt.json", "config.json", "outcome.json"):
            path = directory / name
            self.assertEqual(S_IMODE(path.stat().st_mode), 0o400)
            self.assertNotIn(self.secret, path.read_text())
        self.assertEqual(
            json.loads((directory / "outcome.json").read_text())["returncode"], 23
        )
        self.assertEqual(
            json.loads(
                (directory / "dispatch-handoff/environment/scenario.json").read_text()
            ),
            {
                "schema": "seatlab.scenario/v1",
                "seed": 4001,
                "condition": "reordered",
                "episodes": 6,
            },
        )

    def test_unverified_subscription_cannot_launch_the_native_trial(self) -> None:
        _claude(self.home, "pro")
        cell = plan.grid()[0]
        with patch.object(native_pairs, "_launch") as launch:
            self.assertFalse(runner.run_cell(self.root, cell, self.harbor))
        launch.assert_not_called()
        directory = self.root / "cells" / cell["cell_id"]
        outcome = json.loads((directory / "outcome.json").read_text())
        self.assertIsNone(outcome["returncode"])

    def test_zero_native_exit_does_not_attest_missing_evidence(self) -> None:
        cell = plan.grid()[0]
        with patch.object(native_pairs, "_launch", return_value=0) as launch:
            self.assertFalse(runner.run_cell(self.root, cell, self.harbor))
        launch.assert_called_once()
        directory = self.root / "cells" / cell["cell_id"]
        outcome = json.loads((directory / "outcome.json").read_text())
        self.assertEqual(outcome["returncode"], 0)
        row = runner.report(self.root)["cells"][0]
        self.assertNotIn(row["status"], ("missing", "valid"))
        self.assertIsNone(row["native_score"])

    def test_report_is_read_only_and_distinguishes_missing_and_incomplete(self) -> None:
        initial = runner.report(self.root)
        self.assertEqual([row["status"] for row in initial["cells"]], ["missing"] * 32)
        self.assertFalse(self.root.exists())
        cell = plan.grid()[0]
        for path in (
            self.root,
            self.root / "cells",
            self.root / "cells" / cell["cell_id"],
        ):
            path.mkdir(mode=0o700)
        before = set(self.root.rglob("*"))
        cells = runner.report(self.root)["cells"]
        self.assertEqual(cells[0]["status"], "incomplete")
        self.assertEqual([row["status"] for row in cells[1:]], ["missing"] * 31)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_report_does_not_publish_a_tampered_sealed_config(self) -> None:
        cell = plan.grid()[0]
        with patch.object(native_pairs, "_launch", return_value=1):
            self.assertFalse(runner.run_cell(self.root, cell, self.harbor))
        config_file = self.root / "cells" / cell["cell_id"] / "config.json"
        config_file.chmod(0o600)
        config_file.write_text(json.dumps({"private": f"{self.home} {self.secret}"}))
        config_file.chmod(0o400)
        output = runner.report(self.root)
        row = output["cells"][0]
        self.assertEqual(row["status"], "invalid_evidence")
        self.assertIsNone(row["native_score"])
        self.assertNotIn(self.secret, json.dumps(output))
        self.assertNotIn(str(self.home), json.dumps(output))
