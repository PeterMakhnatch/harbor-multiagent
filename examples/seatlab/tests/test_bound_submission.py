import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from examples.seatlab.capsule import view_hash
from examples.seatlab.export_capsules import reconstructed_capsules

_ENVIRONMENT = (
    Path(__file__).resolve().parents[2] / "tasks/dispatch-handoff/environment"
)
_HEADER = "X-SeatLab-View-SHA256"
_CHOICES = {"rows": [{"id": 0, "bay": "A"}]}


def _load_module(name: str) -> ModuleType:
    spec = spec_from_file_location(name, _ENVIRONMENT / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Dispatch Handoff module {name}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_WORLD = _load_module("seatworld")
with patch.dict(sys.modules, {"seatworld": _WORLD}):
    _SERVE = _load_module("serve")


class _MemoryRequest:
    """Supply raw HTTP to the real handler without binding a socket."""

    def __init__(self, request: bytes) -> None:
        self.input = BytesIO(request)
        self.output = bytearray()

    def makefile(self, mode: str, buffering: int) -> BytesIO:
        return self.input

    def sendall(self, data: bytes) -> None:
        self.output.extend(data)


class BoundSubmissionTest(TestCase):
    def setUp(self) -> None:
        self.publish = self.enterContext(patch.object(_SERVE, "_publish"))
        self.enterContext(patch.object(_SERVE.Handler, "timeout", None))
        self.enterContext(patch.object(_SERVE.Handler, "log_message"))
        self.server = self._server("fresh")

    def _server(self, condition: str, *, episodes: int = 1) -> Any:
        world = _WORLD.World(
            {
                "schema": "seatlab.scenario/v1",
                "seed": 17,
                "episodes": episodes,
                "condition": condition,
            }
        )
        with patch.object(_SERVE.ThreadingHTTPServer, "__init__", return_value=None):
            return _SERVE.WorldServer(world)

    def _request(
        self,
        method: str,
        role: str,
        *,
        body: Any = None,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> tuple[int, Any]:
        path = "/view" if method == "GET" else "/submit"
        request = [f"{method} {path} HTTP/1.0"]
        data = b""
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request += [
                "Content-Type: application/json",
                f"Content-Length: {len(data)}",
            ]
        request.extend(f"{name}: {value}" for name, value in headers)
        transport = _MemoryRequest(
            "\r\n".join([*request, "", ""]).encode("ascii") + data
        )
        with patch.object(_SERVE.Handler, "_role", return_value=role):
            _SERVE.Handler(transport, ("127.0.0.1", 0), self.server)
        head, response = transport.output.split(b"\r\n\r\n", 1)
        return int(head.split(b" ", 2)[1]), json.loads(response)

    def _view(self, role: str) -> Any:
        status, view = self._request("GET", role)
        self.assertEqual(status, 200)
        self.assertEqual(set(view), {"role", "condition", "cases"})
        self.assertEqual(view["role"], role)
        return view

    def _submit(self, role: str, body: object, ack: str | None = None) -> int:
        headers = ((_HEADER, ack),) if ack is not None else ()
        status, response = self._request("POST", role, body=body, headers=headers)
        if status == 200:
            self.assertEqual(response, {"accepted": True})
        return status

    def _dossier(self) -> dict[str, Any]:
        self.publish.assert_called_once()
        self.assertNotIn("observation_receipts", self.server.world.evidence())
        return self.publish.call_args.args[0]

    def test_each_role_binds_its_own_view_and_can_retry(self) -> None:
        inventory = self._view("inventory")
        reports = {"rows": inventory["cases"]}
        inventory_ack = view_hash(inventory)
        self.assertEqual(self._submit("inventory", reports, inventory_ack), 200)
        self.assertEqual(self._submit("inventory", reports, inventory_ack), 200)
        self.assertEqual(self._submit("inventory", reports), 400)
        self.publish.assert_not_called()

        dispatch = self._view("dispatch")
        dispatch_ack = view_hash(dispatch)
        self.assertEqual(self._submit("dispatch", _CHOICES, dispatch_ack), 200)
        self.assertEqual(self._submit("dispatch", _CHOICES, dispatch_ack), 200)
        self.assertEqual(self._submit("dispatch", _CHOICES), 400)
        different = {"rows": [{"id": 0, "bay": "B"}]}
        self.assertEqual(self._submit("dispatch", different, dispatch_ack), 400)

        dossier = self._dossier()
        self.assertEqual(dossier["cases"][0]["chosen"], "A")
        self.assertEqual(
            dossier["observation_receipts"],
            {
                "inventory": {"ack_sha256": inventory_ack},
                "dispatch": {"ack_sha256": dispatch_ack},
            },
        )
        capsules = reconstructed_capsules(dossier)
        for role, view in (("inventory", inventory), ("dispatch", dispatch)):
            self.assertEqual(capsules[role]["basis"], "bound")
            self.assertEqual(capsules[role]["view"], view)

    def test_duplicate_and_malformed_headers_do_not_submit(self) -> None:
        inventory = self._view("inventory")
        reports = {"rows": inventory["cases"]}
        ack = view_hash(inventory)
        for headers in (
            ((_HEADER, ack), (_HEADER.lower(), ack)),
            ((_HEADER, f"{ack},{ack}"),),
            ((_HEADER, ""),),
            ((_HEADER, ack.upper()),),
            ((_HEADER, f"{ack} extra"),),
        ):
            with self.subTest(headers=headers):
                status, _ = self._request(
                    "POST", "inventory", body=reports, headers=headers
                )
                self.assertEqual(status, 400)
                self.assertEqual(self._request("GET", "dispatch")[0], 409)
        self.assertEqual(self._submit("inventory", reports, ack), 200)

    def test_wrong_role_and_stale_view_do_not_bind_dispatch(self) -> None:
        earlier = self._server("fresh", episodes=2).world
        earlier.submit("inventory", {"rows": earlier.view("inventory")["cases"]})
        stale_ack = view_hash(earlier.view("dispatch"))
        self.assertEqual(self._submit("dispatch", _CHOICES, stale_ack), 409)

        inventory = self._view("inventory")
        self.assertEqual(self._submit("inventory", {"rows": inventory["cases"]}), 200)
        dispatch_ack = view_hash(self._view("dispatch"))
        for ack in (view_hash(inventory), stale_ack):
            with self.subTest(ack=ack):
                self.assertNotEqual(ack, dispatch_ack)
                self.assertEqual(self._submit("dispatch", _CHOICES, ack), 400)
                self.assertFalse(self.server.world.evidence()["complete"])
        self.publish.assert_not_called()

        self.assertEqual(self._submit("dispatch", _CHOICES, dispatch_ack), 200)
        self.assertEqual(
            self._dossier()["observation_receipts"],
            {"dispatch": {"ack_sha256": dispatch_ack}},
        )

    def test_invalid_actions_do_not_bind_and_legacy_cannot_upgrade(self) -> None:
        inventory = self._view("inventory")
        inventory_ack = view_hash(inventory)
        reports = {"rows": inventory["cases"]}
        self.assertEqual(self._submit("inventory", {"rows": []}, inventory_ack), 400)
        self.assertEqual(self._request("GET", "dispatch")[0], 409)
        self.assertEqual(self._submit("inventory", reports), 200)
        self.assertEqual(self._submit("inventory", reports, inventory_ack), 400)
        self.assertEqual(self._submit("inventory", reports), 200)

        dispatch_ack = view_hash(self._view("dispatch"))
        self.assertEqual(self._submit("dispatch", {"rows": []}, dispatch_ack), 400)
        self.publish.assert_not_called()
        self.assertEqual(self._submit("dispatch", _CHOICES), 200)
        self.assertEqual(self._submit("dispatch", _CHOICES, dispatch_ack), 400)
        self.assertEqual(self._submit("dispatch", _CHOICES), 200)

        dossier = self._dossier()
        self.assertEqual(dossier["observation_receipts"], {})
        for capsule in reconstructed_capsules(dossier).values():
            self.assertEqual(capsule["basis"], "reconstructed")

    def test_pooled_inventory_cannot_bind(self) -> None:
        self.server = self._server("pooled")
        inventory = self._view("inventory")
        reports = {"rows": inventory["cases"]}
        inventory_ack = view_hash(inventory)
        self.assertEqual(self._submit("inventory", reports, inventory_ack), 400)
        dispatch_ack = view_hash(self._view("dispatch"))
        self.assertEqual(self._submit("dispatch", _CHOICES, dispatch_ack), 200)
        dossier = self._dossier()
        self.assertIsNone(dossier["cases"][0]["reports"])
        self.assertEqual(
            dossier["observation_receipts"],
            {"dispatch": {"ack_sha256": dispatch_ack}},
        )
