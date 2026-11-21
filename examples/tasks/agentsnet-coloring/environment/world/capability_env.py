import os
import secrets
import sys


def main() -> None:
    command = sys.argv[1:]
    if not command:
        raise SystemExit("Usage: capability_env.py COMMAND [ARG ...]")
    env = dict(os.environ)
    for index in range(4):
        env[f"AGENTSNET_N{index}_CAPABILITY"] = secrets.token_urlsafe(32)
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    main()
