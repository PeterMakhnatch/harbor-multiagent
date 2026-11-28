import sys
from typing import Any

SILENT = "--"


def require(view: dict[str, Any], turn: str) -> dict[str, Any]:
    if view.get("turn") != turn or view.get("active") is not True:
        raise ValueError(f"reference missed its {turn} turn")
    return view


def load(rpc: Any, wait: Any) -> None:
    start = require(wait("stage"), "stage")
    initial = start["initial_bay"]
    rpc({"op": "stage", "bay": initial})
    window = require(wait("commit"), "commit")
    notice = window["notice"]
    bay = notice if notice != SILENT and notice in start["bays"] else initial
    rpc({"op": "commit", "bay": bay, "ticket": window["ticket"]})
    window = require(wait("close"), "close")
    rpc({"op": "close", "ticket": window["ticket"]})


def report(rpc: Any, wait: Any) -> None:
    view = require(wait("report"), "report")
    rpc({"op": "report", "bay": view["current_bay"]})


def main() -> None:
    sys.path.insert(0, "/app")
    from recall import rpc, wait

    role = rpc({"op": "view"})["role"]
    {"loader": load, "depot": report}[role](rpc, wait)


if __name__ == "__main__":
    main()
