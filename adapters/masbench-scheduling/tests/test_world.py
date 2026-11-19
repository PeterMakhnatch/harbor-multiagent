import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

TEMPLATE = Path(__file__).resolve().parents[1] / "template"
ROLES = ("m1", "m2", "m3", "m4", "m5")


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, TEMPLATE / relative_path)
    if spec is None or spec.loader is None:
        raise ImportError(relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


broker = load_module("calendar_world", "environment/calendar_world.py")
client = load_module("calendar_client", "solution/client.py")
verifier = load_module("calendar_verifier", "tests/verify.py")


def canonical(document):
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def digest(document):
    return hashlib.sha256(canonical(document)).hexdigest()


def read(path):
    payload = path.read_bytes()
    document = json.loads(payload)
    if payload != canonical(document):
        raise ValueError(f"publication is not canonical: {path}")
    return document


class CalendarWorldTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ports = self.root / "ports"
        self.ports.mkdir()
        self.artifact = self.root / "logs" / "world" / "calendar.json"
        self.case = {
            "schema": "seatlab.calendar-case/v1",
            "case_id": "2/1",
            "horizon": 20,
            "duration": 2,
            "slots_per_day": 10,
            "members": {"m1": [0], "m2": [1, 12], "m3": [3], "m4": [14], "m5": [15]},
            "gold": [4, 9],
        }
        self.world = broker.CalendarWorld(self.case, "full", self.ports, self.artifact)

    def message(self, role, filename):
        return self.ports / role / "out" / filename

    def report(self, role, starts):
        view = read(self.ports / role / "input.json")
        client._submit(
            self.message(role, "report.json"),
            {"role": role, "view_sha256": digest(view), "starts": starts},
        )

    def report_all(self, starts):
        for role in ROLES:
            self.report(role, starts)
        self.assertFalse(self.world.advance())
        return read(self.ports / "m1" / "proposals.json")

    def choose(self, start, proposals):
        client._submit(
            self.message("m1", "choice.json"),
            {"view_sha256": digest(proposals), "start": start},
        )

    def completed_evidence(self, start):
        proposals = self.report_all([])
        self.choose(start, proposals)
        self.assertTrue(self.world.advance())
        return read(self.artifact)

    def assert_not_finished(self):
        self.assertFalse(self.artifact.exists())
        for role in ROLES:
            self.assertFalse((self.ports / role / "done.json").exists())

    def test_two_phases_and_artifact_before_done_for_all_seats(self):
        reports = {}
        for role in reversed(ROLES):
            with self.subTest(role=role):
                port = self.ports / role
                self.assertEqual(
                    {entry.name for entry in port.iterdir()}, {"input.json", "out"}
                )
                view = read(port / "input.json")
                self.assertEqual(
                    view,
                    {
                        "role": role,
                        "mode": "full",
                        "horizon": 20,
                        "duration": 2,
                        "slots_per_day": 10,
                        "busy": self.case["members"][role],
                    },
                )
                reports[role] = client.report_starts(view)
                self.report(role, reports[role])
                self.assertFalse(self.world.advance())
                self.assert_not_finished()
                if role != "m1":
                    self.assertFalse((self.ports / "m1" / "proposals.json").exists())

        proposals = read(self.ports / "m1" / "proposals.json")
        self.assertEqual(proposals, {"role": "m1", "mode": "full", "reports": reports})
        for role in ROLES[1:]:
            self.assertFalse((self.ports / role / "proposals.json").exists())
        start = client.choose_start(proposals["reports"])
        self.assertEqual(start, 4)
        self.choose(start, proposals)

        publications = []
        publish = broker.publish

        def observe_publication(path, document):
            if path.name == "done.json":
                self.assertTrue(self.artifact.is_file())
                self.assertEqual(read(self.artifact)["choice"], start)
            publications.append(path)
            publish(path, document)

        with patch.object(broker, "publish", side_effect=observe_publication):
            self.assertTrue(self.world.advance())
            self.assertTrue(self.world.advance())
        self.assertEqual(
            publications,
            [self.artifact, *(self.ports / role / "done.json" for role in ROLES)],
        )
        for role in ROLES:
            self.assertEqual(read(self.ports / role / "done.json"), {"done": True})
        evidence = read(self.artifact)
        self.assertEqual(evidence["proposal_sha256"], digest(proposals))
        self.assertEqual(
            evidence["initial_view_sha256"],
            {role: digest(read(self.ports / role / "input.json")) for role in ROLES},
        )
        self.assertEqual(
            verifier.verify_world(self.case, evidence),
            {
                "case_id": "2/1",
                "mode": "full",
                "choice": 4,
                "official_exact_match": True,
                "physically_feasible": True,
                "reward": 1,
            },
        )

    def test_null_is_an_explicit_choice_not_an_incomplete_run(self):
        proposals = self.report_all([])
        self.assertIsNone(client.choose_start(proposals["reports"]))
        self.assertFalse(self.world.advance())
        self.assert_not_finished()
        with self.assertRaisesRegex(TimeoutError, "waiting for m1 choice"):
            self.world.run(timeout=0)
        self.choose(None, proposals)
        self.assertTrue(self.world.advance())
        score = verifier.verify_world(self.case, read(self.artifact))
        self.assertIsNone(score["choice"])
        self.assertIsNone(score["physically_feasible"])
        self.assertFalse(score["official_exact_match"])
        self.assertEqual(score["reward"], 0)

    def test_full_and_first8_follow_day_boundaries(self):
        view = read(self.ports / "m1" / "input.json")
        view["busy"] = [0, 11]
        full = client.report_starts(view)
        first8 = client.report_starts({**view, "mode": "first8"})
        self.assertEqual(full, [*range(1, 9), *range(12, 19)])
        self.assertEqual(first8, list(range(1, 9)))
        self.assertEqual(client.report_starts({**view, "busy": list(range(20))}), [])

    def test_score_choice_without_requiring_truthful_or_intersecting_reports(self):
        evidence = self.completed_evidence(4)
        self.assertEqual(verifier.verify_world(self.case, evidence)["reward"], 1)
        for start, official, feasible in (
            (8, False, True),  # A meeting may end exactly at the day boundary.
            (9, True, False),  # The official reference can cross a day boundary.
            (12, False, False),  # The period overlaps a member's calendar.
            (19, False, False),  # The choice is a tick but its end exceeds the horizon.
        ):
            with self.subTest(start=start):
                score = verifier.verify_world(self.case, dict(evidence, choice=start))
                self.assertIs(score["official_exact_match"], official)
                self.assertIs(score["physically_feasible"], feasible)
                self.assertEqual(score["reward"], int(official))

    def test_timeout_with_missing_report_names_the_seat(self):
        for role in ROLES[:-1]:
            self.report(role, [])
        self.assertFalse(self.world.advance())
        with self.assertRaisesRegex(TimeoutError, "waiting for reports: m5"):
            self.world.run(timeout=0)
        self.assert_not_finished()

    def test_rejects_wrong_actor_or_input_receipt(self):
        own = read(self.ports / "m1" / "input.json")
        other = read(self.ports / "m2" / "input.json")
        path = self.message("m1", "report.json")
        for role, acknowledgment in (("m2", digest(own)), ("m1", digest(other))):
            with self.subTest(role=role, acknowledgment=acknowledgment):
                client._submit(
                    path, {"role": role, "view_sha256": acknowledgment, "starts": []}
                )
                with self.assertRaisesRegex(ValueError, "its own input"):
                    self.world.advance()
                self.assert_not_finished()
                path.unlink()

    def test_rejects_duplicate_fields_and_replacement_report(self):
        path = self.message("m1", "report.json")
        path.write_bytes(b'{"role":"m2","role":"m1","starts":[]}')
        with self.assertRaisesRegex(ValueError, "invalid submission JSON"):
            self.world.advance()
        path.unlink()
        proposals = self.report_all([])
        with self.assertRaises(FileExistsError):
            self.report("m1", [4])
        path.unlink()
        self.report("m1", [4])
        self.choose(4, proposals)
        with self.assertRaisesRegex(ValueError, "report changed after acceptance: m1"):
            self.world.advance()
        self.assert_not_finished()

    def test_rejects_non_file_submissions(self):
        report = self.message("m1", "report.json")
        report.symlink_to(self.ports / "m2" / "input.json")
        with self.assertRaises(OSError):
            self.world.advance()
        report.unlink()
        os.mkfifo(report)
        with self.assertRaisesRegex(ValueError, "invalid submission file"):
            self.world.advance()
        self.assert_not_finished()

    def test_rejects_early_choice_and_nonleader_choice(self):
        choice = {"view_sha256": "wrong", "start": 4}
        early = self.message("m1", "choice.json")
        client._submit(early, choice)
        with self.assertRaisesRegex(ValueError, "before proposals"):
            self.world.advance()
        early.unlink()
        client._submit(self.message("m2", "choice.json"), choice)
        with self.assertRaisesRegex(ValueError, "only m1 can submit a choice"):
            self.world.advance()
        self.assert_not_finished()

    def test_rejects_unbound_final_choice(self):
        self.report_all([])
        client._submit(
            self.message("m1", "choice.json"), {"view_sha256": "wrong", "start": 4}
        )
        with self.assertRaisesRegex(ValueError, "does not acknowledge the proposals"):
            self.world.advance()
        self.assert_not_finished()

    def test_rejects_malformed_starts_or_choice(self):
        path = self.message("m1", "report.json")
        for starts in ([1, 1], [5, 4], [20], [True], ["4"]):
            with self.subTest(starts=starts):
                self.report("m1", starts)
                with self.assertRaisesRegex(ValueError, "invalid report starts"):
                    self.world.advance()
                path.unlink()
        proposals = self.report_all([])
        for start in (True, -1, 20, 4.0, "4"):
            with self.subTest(start=start):
                self.choose(start, proposals)
                with self.assertRaisesRegex(ValueError, "invalid choice start"):
                    self.world.advance()
                self.message("m1", "choice.json").unlink()
        self.assert_not_finished()

    def test_independent_verifier_rejects_missing_or_changed_receipts(self):
        evidence = self.completed_evidence(4)
        for missing in (
            "choice",
            "acknowledged",
            "initial_view_sha256",
            "proposal_sha256",
            "reports",
        ):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                verifier.verify_world(
                    self.case,
                    {key: value for key, value in evidence.items() if key != missing},
                )
        with self.assertRaises(ValueError):
            verifier.verify_world(self.case, evidence, mode="first8")
        with self.assertRaises(ValueError):
            verifier.verify_world(self.case, dict(evidence, acknowledged=1))
        changed = deepcopy(evidence)
        changed["initial_view_sha256"]["m1"] = changed["initial_view_sha256"]["m2"]
        with self.assertRaisesRegex(ValueError, "input acknowledgments"):
            verifier.verify_world(self.case, changed)
        changed = deepcopy(evidence)
        changed["reports"]["m1"] = [4]
        with self.assertRaisesRegex(ValueError, "leader acknowledgment"):
            verifier.verify_world(self.case, changed)
        changed["reports"].pop("m5")
        with self.assertRaisesRegex(ValueError, "invalid calendar reports"):
            verifier.verify_world(self.case, changed)

    def test_native_wrapper_writes_no_reward_for_incomplete_or_corrupt_evidence(self):
        config = self.root / "case.json"
        config.write_bytes(canonical({"case": self.case, "mode": "full"}))
        reward = self.root / "reward.txt"
        paths = {
            "/opt/calendar/case.json": config,
            "/logs/world/calendar.json": self.artifact,
            "/logs/verifier/reward.txt": reward,
        }
        with patch.object(verifier, "Path", side_effect=paths.__getitem__):
            with self.assertRaisesRegex(SystemExit, "invalid calendar evidence"):
                verifier.main()
            self.assertFalse(reward.exists())
            self.completed_evidence(None)
            with patch("builtins.print"):
                verifier.main()
            self.assertEqual(reward.read_text(), "0\n")
            self.artifact.unlink()
            self.artifact.write_text('{"acknowledged":true,"acknowledged":false}')
            with self.assertRaisesRegex(SystemExit, "duplicate JSON field"):
                verifier.main()
            self.assertFalse(reward.exists())
