import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ADAPTER = Path(__file__).resolve().parents[1]
ROLES = ("m1", "m2", "m3", "m4", "m5")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frozen = load_module(
    "calendar_base", ADAPTER / "template" / "environment" / "calendar_world.py"
)
with patch.dict(sys.modules, {"calendar_base": frozen}):
    relay = load_module("interactive_world", ADAPTER / "interactive_world.py")


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(document):
    return hashlib.sha256(canonical(document)).hexdigest()


def read(path):
    payload = path.read_bytes()
    document = json.loads(payload)
    if canonical(document) != payload:
        raise ValueError(f"publication is not canonical: {path}")
    return document


class InteractiveWorldTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        self.ports = root / "ports"
        self.ports.mkdir()
        self.artifact = root / "logs" / "artifacts" / "world.calendar.json"
        self.case = {
            "case_id": "2/1",
            "horizon": 20,
            "duration": 2,
            "slots_per_day": 10,
            "members": dict(zip(ROLES, ([0], [1, 12], [3], [14], list(range(20))))),
        }
        self.world = relay.InteractiveWorld(
            self.case, "cursor", self.ports, self.artifact
        )
        self.views = {role: read(self.ports / role / "input.json") for role in ROLES}

    def write(self, role, name, document):
        path = self.ports / role / "out" / name
        with path.open("xb") as stream:
            stream.write(canonical(document))
        return path

    def query(self, number=1, bound=0, *, previous=None):
        receipt = self.views["m1"] if previous is None else previous
        self.write(
            "m1",
            f"query-{number:03d}.json",
            {"round": number, "bound": bound, "view_sha256": digest(receipt)},
        )
        self.assertFalse(self.world.advance())
        query = {"round": number, "bound": bound}
        for role in ROLES:
            path = self.ports / role / f"query-{number:03d}.json"
            self.assertEqual(read(path), query)
        return query

    def reply_message(self, role, query, tick):
        return {
            "role": role,
            "round": query["round"],
            "next": tick,
            "initial_sha256": digest(self.views[role]),
            "query_sha256": digest(query),
        }

    def reply(self, role, query, tick):
        return self.write(
            role,
            f"reply-{query['round']:03d}.json",
            self.reply_message(role, query, tick),
        )

    def bundle(self, query, ticks):
        path = self.ports / "m1" / f"round-{query['round']:03d}.json"
        for role, tick in ticks.items():
            if role != "m1":
                self.reply(role, query, tick)
        self.assertFalse(self.world.advance())
        self.assertFalse(path.exists())
        self.assert_not_finished()
        self.reply("m1", query, ticks["m1"])
        self.assertFalse(self.world.advance())
        self.assertFalse(self.world.advance())
        bundle = {**query, "replies": ticks}
        self.assertEqual(read(path), bundle)
        for role in ROLES[1:]:
            self.assertFalse((self.ports / role / path.name).exists())
        return bundle

    def choice(self, bundle, tick):
        return self.write(
            "m1",
            "choice.json",
            {"round": bundle["round"], "start": tick, "view_sha256": digest(bundle)},
        )

    def assert_not_finished(self):
        self.assertFalse(self.artifact.exists())
        self.assert_no_done()

    def assert_no_done(self):
        for role in ROLES:
            self.assertFalse((self.ports / role / "done.json").exists())

    def test_two_rounds_relay_all_five_and_record_only_actor_choice(self):
        for role, view in self.views.items():
            with self.subTest(role=role):
                self.assertEqual(
                    view,
                    {
                        "role": role,
                        "mode": "cursor",
                        "horizon": 20,
                        "duration": 2,
                        "slots_per_day": 10,
                        "busy": self.case["members"][role],
                    },
                )
        query1 = self.query()
        bundle1 = self.bundle(query1, dict(zip(ROLES, (8, 2, None, 4, 5))))
        # The broker accepts the actor's legal bound and choice without deciding
        # that the null reply ends play or that the ticks are actually feasible.
        query2 = self.query(2, 6, previous=bundle1)
        bundle2 = self.bundle(query2, dict(zip(ROLES, (10, 6, 10, 12, 9))))
        self.assert_not_finished()
        self.choice(bundle2, 19)
        expected = {
            "schema": "seatlab.calendar-interactive-world/v1",
            "case_id": "2/1",
            "mode": "cursor",
            "initial_view_sha256": {role: digest(self.views[role]) for role in ROLES},
            "rounds": [
                {
                    **bundle1,
                    "query_sha256": digest(query1),
                    "bundle_sha256": digest(bundle1),
                },
                {
                    **bundle2,
                    "query_sha256": digest(query2),
                    "bundle_sha256": digest(bundle2),
                },
            ],
            "choice": 19,
            "choice_view_sha256": digest(bundle2),
            "acknowledged": True,
        }
        publish = frozen.publish
        publications = []

        def observe(path, document):
            if path.name == "done.json":
                self.assertEqual(read(self.artifact), expected)
            publish(path, document)
            publications.append(path)

        with patch.object(frozen, "publish", side_effect=observe):
            self.assertTrue(self.world.advance())
            self.assertTrue(self.world.advance())
        self.assertEqual(
            publications,
            [self.artifact, *(self.ports / role / "done.json" for role in ROLES)],
        )
        self.assertEqual(read(self.artifact), expected)
        for role in ROLES:
            self.assertEqual(read(self.ports / role / "done.json"), {"done": True})

    def test_explicit_null_and_timeout_without_an_action_are_distinct(self):
        bundle = self.bundle(self.query(), dict.fromkeys(ROLES))
        with self.assertRaisesRegex(TimeoutError, "waiting for a coordinator action"):
            self.world.run(timeout=0)
        self.assert_not_finished()
        self.choice(bundle, None)
        self.assertTrue(self.world.advance())
        self.assertIsNone(read(self.artifact)["choice"])

    def test_timeout_identifies_missing_seats_without_artifact(self):
        query = self.query()
        for role in ROLES[1:-1]:
            self.reply(role, query, 5)
        self.assertFalse(self.world.advance())
        with self.assertRaisesRegex(TimeoutError, "round 1 replies: m1, m5"):
            self.world.run(timeout=0)
        self.assertFalse((self.ports / "m1" / "round-001.json").exists())
        self.assert_not_finished()

    def test_refuses_ahead_premature_and_off_role_messages(self):
        for role, name in (
            ("m1", "query-002.json"),
            ("m1", "query-099.json"),
            ("m2", "reply-001.json"),
            ("m4", "query-001.json"),
            ("m5", "choice.json"),
        ):
            with self.subTest(role=role, name=name):
                path = self.write(role, name, {})
                with self.assertRaisesRegex(ValueError, "unexpected or premature"):
                    self.world.advance()
                path.unlink()
        choice = self.write("m1", "choice.json", {})
        with self.assertRaisesRegex(ValueError, "before the first round"):
            self.world.advance()
        choice.unlink()
        self.query()
        for name in ("query-002.json", "reply-002.json", "choice.json"):
            with self.subTest(name=name):
                path = self.write("m1", name, {})
                with self.assertRaisesRegex(ValueError, "unexpected or premature"):
                    self.world.advance()
                path.unlink()
        self.assert_not_finished()

    def test_rejects_simultaneous_next_query_and_choice(self):
        bundle = self.bundle(self.query(), dict.fromkeys(ROLES))
        self.choice(bundle, None)
        self.write(
            "m1",
            "query-002.json",
            {"round": 2, "bound": 1, "view_sha256": digest(bundle)},
        )
        with self.assertRaisesRegex(ValueError, "ambiguous coordinator actions"):
            self.world.advance()
        self.assertFalse((self.ports / "m5" / "query-002.json").exists())
        self.assert_not_finished()

    def test_query_acknowledges_latest_coordinator_view_and_increases_bound(self):
        initial = {"round": 1, "bound": 0, "view_sha256": digest(self.views["m1"])}
        for overrides in (
            {"round": True},
            {"round": 2},
            {"view_sha256": digest(self.views["m2"])},
            {"role": "m1"},
            {"bound": True},
            {"bound": -1},
            {"bound": 20},
        ):
            with self.subTest(overrides=overrides):
                path = self.write("m1", "query-001.json", initial | overrides)
                with self.assertRaises(ValueError):
                    self.world.advance()
                path.unlink()
        bundle = self.bundle(self.query(bound=3), dict.fromkeys(ROLES, 3))
        for bound, receipt in (
            (4, digest(self.views["m1"])),
            (3, digest(bundle)),
            (2, digest(bundle)),
        ):
            with self.subTest(bound=bound, receipt=receipt):
                path = self.write(
                    "m1",
                    "query-002.json",
                    {"round": 2, "bound": bound, "view_sha256": receipt},
                )
                with self.assertRaises(ValueError):
                    self.world.advance()
                path.unlink()
        self.assertFalse((self.ports / "m2" / "query-002.json").exists())
        self.assert_not_finished()

    def test_each_reply_binds_its_own_view_role_round_and_forwarded_query(self):
        query = self.query(bound=3)
        reply = self.reply_message("m3", query, 4)
        envelope = {**query, "view_sha256": digest(self.views["m1"])}
        for overrides in (
            {"initial_sha256": digest(self.views["m2"])},
            {"query_sha256": digest(envelope)},
            {"role": "m2"},
            {"round": 2},
            {"round": True},
            {"next": 2},
            {"next": 20},
            {"next": True},
            {"next": 4.0},
        ):
            with self.subTest(overrides=overrides):
                path = self.write("m3", "reply-001.json", reply | overrides)
                with self.assertRaisesRegex(ValueError, "reply (does not bind|tick)"):
                    self.world.advance()
                path.unlink()
        self.assertFalse((self.ports / "m1" / "round-001.json").exists())
        self.assert_not_finished()

    def test_choice_must_bind_current_round_and_use_a_legal_tick(self):
        bundle1 = self.bundle(self.query(), dict.fromkeys(ROLES, 5))
        query2 = self.query(2, 5, previous=bundle1)
        bundle2 = self.bundle(query2, dict.fromkeys(ROLES, 5))
        choice = {"round": 2, "start": 5, "view_sha256": digest(bundle2)}
        for overrides in (
            {"view_sha256": digest(bundle1)},
            {"round": 1},
            {"round": 2.0},
            {"start": True},
            {"start": -1},
            {"start": 20},
        ):
            with self.subTest(overrides=overrides):
                path = self.write("m1", "choice.json", choice | overrides)
                with self.assertRaisesRegex(
                    ValueError, "choice (does not acknowledge|tick)"
                ):
                    self.world.advance()
                path.unlink()
        self.assert_not_finished()

    def test_changed_pending_reply_prevents_publication(self):
        query = self.query()
        path = self.reply("m2", query, 5)
        self.assertFalse(self.world.advance())
        path.write_bytes(canonical(self.reply_message("m2", query, 6)))
        with self.assertRaisesRegex(
            ValueError, "changed after acceptance: m2/reply-001"
        ):
            self.world.advance()
        self.assertFalse((self.ports / "m1" / "round-001.json").exists())
        self.assert_not_finished()

    def test_changed_coordinator_query_prevents_bundle(self):
        query = self.query()
        path = self.ports / "m1" / "out" / "query-001.json"
        original = read(path)
        path.write_bytes(canonical({**original, "bound": 1}))
        for role in ROLES:
            self.reply(role, query, 5)
        with self.assertRaisesRegex(
            ValueError, "changed after acceptance: m1/query-001"
        ):
            self.world.advance()
        self.assertFalse((self.ports / "m1" / "round-001.json").exists())
        self.assert_not_finished()

    def test_replaced_prior_reply_is_rejected_even_with_identical_bytes(self):
        bundle1 = self.bundle(self.query(), dict.fromkeys(ROLES, 5))
        query2 = self.query(2, 5, previous=bundle1)
        bundle2 = self.bundle(query2, dict.fromkeys(ROLES, 5))
        path = self.ports / "m4" / "out" / "reply-001.json"
        staged = path.with_name(".submit-replacement")
        staged.write_bytes(path.read_bytes())
        staged.replace(path)
        self.choice(bundle2, 5)
        with self.assertRaisesRegex(
            ValueError, "changed after acceptance: m4/reply-001"
        ):
            self.world.advance()
        self.assert_not_finished()

    def test_reject_duplicate_json_fields_and_unsafe_mailbox_entries(self):
        path = self.ports / "m1" / "out" / "query-001.json"
        path.write_bytes(b'{"round":1,"bound":1,"bound":0}')
        with self.assertRaisesRegex(ValueError, "invalid submission JSON"):
            self.world.advance()
        path.unlink()
        path.symlink_to(self.ports / "m1" / "input.json")
        with self.assertRaisesRegex(ValueError, "invalid submission file"):
            self.world.advance()
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(ValueError, "invalid submission file"):
            self.world.advance()
        path.unlink()
        path.write_bytes(b" " * (relay.MAX_MESSAGE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "invalid submission file"):
            self.world.advance()
        self.assert_not_finished()

    def test_accepts_a_linked_message_only_after_actor_finishes_staging(self):
        staging = self.write(
            "m1",
            ".submit-temporary",
            {"round": 1, "bound": 0, "view_sha256": digest(self.views["m1"])},
        )
        path = staging.with_name("query-001.json")
        os.link(staging, path)
        self.assertFalse(self.world.advance())
        self.assertFalse((self.ports / "m1" / "query-001.json").exists())
        staging.unlink()
        self.assertFalse(self.world.advance())
        query_path = self.ports / "m1" / "query-001.json"
        self.assertEqual(read(query_path), {"round": 1, "bound": 0})
        self.assertFalse(self.world.advance())

    def test_rejects_extra_staging_entries_and_caps_round_count(self):
        first = self.write("m3", ".submit-one", {})
        second = self.write("m3", ".submit-two", {})
        with self.assertRaisesRegex(ValueError, "too many mailbox entries"):
            self.world.advance()
        first.unlink()
        second.unlink()
        with patch.object(relay, "MAX_ROUNDS", 1):
            bundle = self.bundle(self.query(), dict.fromkeys(ROLES, 5))
            self.write(
                "m1",
                "query-002.json",
                {"round": 2, "bound": 5, "view_sha256": digest(bundle)},
            )
            with self.assertRaisesRegex(
                ValueError, "too many interactive calendar rounds"
            ):
                self.world.advance()
        self.assert_not_finished()

    def test_artifact_never_replaces_existing_evidence(self):
        bundle = self.bundle(self.query(), dict.fromkeys(ROLES))
        self.choice(bundle, None)
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"earlier evidence")
        with self.assertRaises(FileExistsError):
            self.world.advance()
        self.assertEqual(self.artifact.read_bytes(), b"earlier evidence")
        self.assert_no_done()
