import argparse
import http.client
import json
import os
import sys
from typing import cast

MAX_BODY = 32 * 1024
MAX_REPLY = 3 * MAX_BODY + 4096


class RPCError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def rpc(
    path: str, payload: dict[str, object] | None = None, *, authenticate: bool = True
) -> dict[str, object]:
    headers = {}
    if authenticate:
        token = os.environ.get("AGENTSNET_CAPABILITY")
        if not token:
            raise ValueError("missing seat capability")
        headers["Authorization"] = f"Bearer {token}"
    method = "GET" if payload is None else "POST"
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_BODY:
            raise ValueError("request body exceeds limit")
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("world", 8080, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        data = response.read(MAX_REPLY + 1)
    finally:
        connection.close()
    if len(data) > MAX_REPLY:
        raise ValueError("reply body exceeds limit")
    result: object = json.loads(data)
    if not isinstance(result, dict) or not all(isinstance(key, str) for key in result):
        raise ValueError("reply body must be a JSON object")
    if status != 200:
        reason = result.get("error")
        if not isinstance(reason, str):
            raise ValueError("reply error must be a string")
        raise RPCError(status, reason)
    return cast(dict[str, object], result)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("observe")
    for command in ("messages", "answer"):
        commands.add_parser(command).add_argument("payload")
    args = parser.parse_args()
    try:
        if args.command == "observe":
            result = rpc("/observation")
        else:
            if len(args.payload) > MAX_BODY:
                raise ValueError("request body exceeds limit")
            payload = json.loads(args.payload)
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = rpc(f"/{args.command}", payload)
    except RPCError as error:
        print(json.dumps({"error": error.reason}), file=sys.stderr)
        return 1
    except (ValueError, RecursionError, OSError, http.client.HTTPException):
        print(json.dumps({"error": "rpc_failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
