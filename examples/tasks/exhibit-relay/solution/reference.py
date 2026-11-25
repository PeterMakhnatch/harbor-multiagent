import json
import subprocess
import sys

from policies import act


def main() -> None:
    sys.path.insert(0, "/app")
    from relay import rpc, wait

    role = rpc({"op": "view"})["role"]
    phases = {
        "loader": ("request", "dispatch"),
        "registrar": ("registrar",),
        "depot": ("depot",),
    }[role]
    for phase in phases:
        view = wait(phase)
        if view["phase"] != phase or view["turn"] != role:
            raise ValueError("reference missed its turn")
        policy = "unfiltered" if view["quota"] == 40 else "portfolio"
        action = act(view, policy)
        if role == "registrar" and "notes" in action:
            subprocess.run(
                [
                    sys.executable,
                    "/app/relay.py",
                    "file-notes",
                    json.dumps(action["notes"], separators=(",", ":")),
                    *action["ids"],
                ],
                check=True,
            )
        else:
            rpc(action)


if __name__ == "__main__":
    main()
