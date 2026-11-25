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
        rpc(act(view, policy))


if __name__ == "__main__":
    main()
