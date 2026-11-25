"""A file-discovered extension uses the existing authenticated HTTP referee."""

import json
import shutil
import threading

from conftest import FLOTILLA, World, load


def test_extension_routes_and_lifecycle(tmp_path, monkeypatch):
    shutil.copy(FLOTILLA / "world" / "world.py", tmp_path / "world.py")
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "example.py").write_text('''
class Example:
    routes = {"/step": ("POST",), "/finish": ("POST",)}

    def __init__(self, host):
        self.host = host

    def message_delay(self, message, recipient):
        return None if message["text"] == "discard" else 1

    def handle(self, seat, method, path, body, query):
        if path == "/step":
            self.host.advance()
            self.host.retire(seat)
            self.host.activate("p2")
        else:
            self.host.record("finished", seat, body)
            self.host.freeze()
        return 200, {"tick": self.host.tick}


def create(task, host):
    return Example(host)
''')
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"extension": "example", "messaging": True, "initial_active": ["p1"]}))
    tokens = {"p1": "token1", "p2": "token2"}
    logs = tmp_path / "logs"
    for name, value in {"FLOTILLA_TASK_JSON": str(task), "FLOTILLA_LOGS": str(logs),
                        "FLOTILLA_TOKENS": "p1:token1,p2:token2", "FLOTILLA_SEATS": "p1,p2",
                        "FLOTILLA_ADMIN": "admin-token", "FLOTILLA_CONDITION": "talk"}.items():
        monkeypatch.setenv(name, value)
    module = load(tmp_path / "world.py")
    server = module.serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    world = World(module, server.server_address[1], tokens, "admin-token", logs, ordered=True)
    try:
        assert world.seat("p2", "GET", "/whoami")[1]["active"] == "waiting"
        assert world.seat("p2", "POST", "/finish", {})[0] == 409
        for text in ("deliver", "discard"):
            assert world.seat("p1", "POST", "/messages", {"text": text})[0] == 201
        assert world.seat("p1", "POST", "/step", {}) == (200, {"tick": 1})
        assert world.seat("p1", "GET", "/whoami")[1]["active"] == "retired"
        assert world.seat("p1", "POST", "/messages", {"text": "late"})[0] == 409
        assert [m["text"] for m in world.seat("p2", "GET", "/messages")[1]["messages"]] == ["deliver"]
        assert world.seat("p2", "POST", "/finish", {"value": 42})[0] == 200
        assert world.seat("p2", "POST", "/finish", {})[0] == 409
        assert world.seat("p2", "POST", "/messages", {"text": "late"})[0] == 409
        assert world.seat("p2", "POST", "/submit", {"answer": {}})[0] == 404
        events = world.events()
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert [e["t"] for e in events] == sorted(e["t"] for e in events)
        assert [e["data"] for e in events if e["kind"] == "finished"] == [{"value": 42}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
