import json
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from examples.seatlab.capsule import (
    encode_view,
    extract_view,
    make_capsule,
    verify_capsule,
    view_hash,
)
from examples.seatlab.export_capsules import reconstructed_capsules, reconstructed_views

if __package__:
    from .test_evidence import _TRIAL_ID, _dossier, _native, _write_trial
else:
    from test_evidence import _TRIAL_ID, _dossier, _native, _write_trial

_SEATLAB = Path(__file__).resolve().parents[1]
_ENVIRONMENT = _SEATLAB.parent / "tasks/dispatch-handoff/environment"
World = run_path(str(_ENVIRONMENT / "seatworld.py"))["World"]


def _published_view_bytes(view: object) -> bytes:
    # The /view response in serve.py does not sort object keys.
    return json.dumps(view, separators=(",", ":")).encode("utf-8")


def _completed_world(
    condition: str, *, truthful_reports: bool = False, chosen: str = "A"
) -> tuple[dict[str, Any], dict[str, Any]]:
    world = World(
        {
            "schema": "seatlab.scenario/v1",
            "condition": condition,
            "seed": 17,
            "episodes": 2,
        }
    )
    expected = {}
    if condition != "pooled":
        inventory = world.view("inventory")
        expected["inventory"] = inventory
        outgoing = deepcopy(inventory["cases"])
        if not truthful_reports:
            for row in outgoing:
                for report in row["reports"]:
                    report["available"] = [
                        bay for bay in "ABCD" if bay not in report["available"]
                    ]
        world.submit("inventory", {"rows": outgoing})
    dispatch = world.view("dispatch")
    expected["dispatch"] = dispatch
    world.submit(
        "dispatch",
        {"rows": [{"id": row["id"], "bay": chosen} for row in dispatch["cases"]]},
    )
    # serve.py publishes dossier keys in sorted order, unlike the /view body.
    manifest = json.loads(json.dumps(world.evidence(), sort_keys=True))
    return expected, manifest


def _cli(
    script: str, *args: str, data: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-B", str(_SEATLAB / script), *args],
        input=data,
        capture_output=True,
        check=False,
    )


class CapsuleTest(TestCase):
    def test_canonical_bytes_ignore_key_order_but_not_list_order(self) -> None:
        view = {"z": 2, "a": ["é", 1]}
        equivalent = {"a": ["é", 1], "z": 2}
        canonical = b'{"a":["\\u00e9",1],"z":2}'
        self.assertEqual(encode_view(view), canonical)
        self.assertEqual(view_hash(view), sha256(canonical).hexdigest())
        self.assertEqual(view_hash(view), view_hash(equivalent))
        self.assertNotEqual(view_hash(view), view_hash({"a": [1, "é"], "z": 2}))
        sealed = make_capsule("inventory", view)
        self.assertEqual(extract_view(sealed), equivalent)
        view["z"] = 3
        self.assertEqual(extract_view(sealed), equivalent)

    def test_reconstructed_and_bound_envelopes_have_distinct_required_fields(
        self,
    ) -> None:
        view = {"cases": []}
        reconstructed = make_capsule("inventory", view)
        self.assertEqual(reconstructed["basis"], "reconstructed")
        self.assertNotIn("acknowledged_sha256", reconstructed)
        verify_capsule(reconstructed)
        with self.assertRaises(ValueError):
            make_capsule("inventory", view, basis="bound")
        with self.assertRaises(ValueError):
            make_capsule("inventory", view, acknowledged_sha256=view_hash(view))
        bound = make_capsule(
            "inventory", view, basis="bound", acknowledged_sha256=view_hash(view)
        )
        verify_capsule(bound)
        for invalid in (
            {**reconstructed, "schema": "seatlab.capsule/v2"},
            {**reconstructed, "basis": "bound"},
            {**reconstructed, "basis": "captured"},
            {**reconstructed, "role": "  "},
            {**reconstructed, "acknowledged_sha256": None},
            {**reconstructed, "score": 0},
            {**reconstructed, "view": {"cases": [1]}},
            {**bound, "acknowledged_sha256": "0" * 64},
            {**bound, "acknowledged_sha256": None},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                verify_capsule(invalid)

    def test_cli_rejects_duplicate_keys_and_nonfinite_input(self) -> None:
        for bad in (b'{"a":{"x":1,"x":2}}', b"NaN", b"Infinity", b"1e999"):
            with self.subTest(bad=bad):
                result = _cli("capsule.py", "seal", "--role", "dispatch", data=bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
        sealed = encode_view(make_capsule("dispatch", []))
        duplicate_role = b'{"role":"inventory",' + sealed[1:]
        result = _cli("capsule.py", "verify", data=duplicate_role)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")


class DispatchCapsuleTest(TestCase):
    def test_reconstructs_active_views_in_original_field_and_delivery_order(
        self,
    ) -> None:
        arrivals = {
            "silent": [],
            "stale": [1],
            "fresh": [2],
            "reordered": [2, 1],
            "pooled": [],
        }
        for condition, revisions in arrivals.items():
            with self.subTest(condition=condition):
                expected, manifest = _completed_world(condition)
                views = reconstructed_views(manifest)
                self.assertEqual(views, expected)
                for role, view in views.items():
                    self.assertEqual(
                        _published_view_bytes(view),
                        _published_view_bytes(expected[role]),
                    )
                dispatch = views["dispatch"]["cases"][0]
                self.assertEqual(
                    [report["revision"] for report in dispatch["messages"]], revisions
                )
                if condition == "pooled":
                    self.assertNotIn("inventory", views)
                    self.assertEqual(
                        dispatch["pooled_available"], manifest["cases"][0]["after"]
                    )
                else:
                    inventory = views["inventory"]["cases"][0]
                    self.assertCountEqual(inventory, ("id", "reports"))
                    self.assertNotEqual(
                        inventory["reports"], manifest["cases"][0]["reports"]
                    )
                    self.assertIsNone(dispatch["pooled_available"])
                self.assertCountEqual(
                    dispatch, ("id", "utilities", "messages", "pooled_available")
                )

    def test_choices_never_enter_views_and_reports_enter_only_visible_view(
        self,
    ) -> None:
        for condition in ("silent", "reordered"):
            with self.subTest(condition=condition):
                _, first = _completed_world(condition, chosen="A")
                _, different_choice = _completed_world(condition, chosen="D")
                _, different_reports = _completed_world(
                    condition, truthful_reports=True
                )
                self.assertNotEqual(
                    first["cases"][0]["chosen"], different_choice["cases"][0]["chosen"]
                )
                self.assertNotEqual(
                    first["cases"][0]["reports"],
                    different_reports["cases"][0]["reports"],
                )
                baseline = reconstructed_views(first)
                self.assertEqual(baseline, reconstructed_views(different_choice))
                revised = reconstructed_views(different_reports)
                self.assertEqual(baseline["inventory"], revised["inventory"])
                if condition == "silent":
                    self.assertEqual(baseline["dispatch"], revised["dispatch"])
                else:
                    self.assertNotEqual(baseline["dispatch"], revised["dispatch"])

    def test_receipt_must_explicitly_match_the_canonical_role_view(self) -> None:
        expected, manifest = _completed_world("reordered")
        dispatch = expected["dispatch"]
        digest = view_hash(dispatch)
        wire_digest = sha256(_published_view_bytes(dispatch)).hexdigest()
        self.assertNotEqual(digest, wire_digest)
        self.assertEqual(
            reconstructed_capsules(manifest)["dispatch"]["basis"], "reconstructed"
        )
        manifest["observation_receipts"] = {"dispatch": {"ack_sha256": digest}}
        capsules = reconstructed_capsules(manifest)
        self.assertEqual(capsules["inventory"]["basis"], "reconstructed")
        self.assertEqual(capsules["dispatch"]["basis"], "bound")
        self.assertEqual(extract_view(capsules["dispatch"]), dispatch)
        for invalid in (wire_digest, view_hash(expected["inventory"]), None):
            manifest["observation_receipts"]["dispatch"]["ack_sha256"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reconstructed_capsules(manifest)
        manifest["observation_receipts"]["dispatch"] = {}
        with self.assertRaises(ValueError):
            reconstructed_capsules(manifest)

    def test_native_export_verify_and_extract_cli_pipeline(self) -> None:
        with TemporaryDirectory() as directory:
            trial = Path(directory)
            dossier = _dossier("fresh")
            native = _native("fresh")
            _write_trial(trial, native, dossier)
            exported = _cli("export_capsules.py", str(trial), "--role", "dispatch")
            self.assertEqual(exported.returncode, 0, exported.stderr)
            row = json.loads(exported.stdout)
            self.assertEqual(
                {key: row[key] for key in row if key != "capsule"},
                {
                    "native_trial_id": _TRIAL_ID,
                    "seed": 17,
                    "condition": "fresh",
                    "role": "dispatch",
                },
            )
            verified = _cli("capsule.py", "verify", data=encode_view(row["capsule"]))
            self.assertEqual(verified.returncode, 0, verified.stderr)
            extracted = _cli("capsule.py", "view", data=verified.stdout)
            self.assertEqual(extracted.returncode, 0, extracted.stderr)
            self.assertEqual(
                extracted.stdout, encode_view(reconstructed_views(dossier)["dispatch"])
            )
            sealed = _cli(
                "capsule.py", "seal", "--role", "dispatch", data=extracted.stdout
            )
            self.assertEqual(sealed.returncode, 0, sealed.stderr)
            self.assertEqual(json.loads(sealed.stdout), row["capsule"])
            native["verifier_result"]["rewards"]["reward"] = 1
            _write_trial(trial, native, dossier)
            failed = _cli("export_capsules.py", str(trial))
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(failed.stdout, b"")
            self.assertNotIn(str(trial).encode(), failed.stderr)
