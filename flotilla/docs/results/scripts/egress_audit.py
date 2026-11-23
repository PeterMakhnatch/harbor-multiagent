"""List every host other than the world that a seat's commands or tool output name, per trial.

python3 flotilla/docs/results/scripts/egress_audit.py <jobs_dir> '<pattern>'

Seats have network access. A request to any host but `world` (the game service) could fetch public facts,
for example this repository's published pots and outside options. A host counts when it follows a URL scheme
or a fetch command (curl, wget, git clone, ...) and ends in a public top-level domain; a private address or a
name without a dot (another container) is reported as `internal:<name>`. Prints one line per trial with such a
host, then a total. Exit code 1 if any trial names a public host."""
import json
import re
import sys
from pathlib import Path

HOST = r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|org|net|io|ai|dev|app|co|gov|edu|info|me|xyz|cloud|sh|us|uk|de|fr|cn|jp|ru|in)"
URL = re.compile(r"\b(?:https?|ftp)://([A-Za-z0-9.-]+)", re.IGNORECASE)
FETCH = re.compile(r"\b(?:curl|wget|git\s+clone|pip\s+install|nc|ssh|ping|nslookup|dig)\b[^\n|;&>]*?\s(" + HOST + r")\b")
PRIVATE = re.compile(r"^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)")
FILE_SUFFIXES = (".py", ".json", ".jsonl", ".md", ".txt", ".sh", ".log", ".csv", ".yaml", ".yml", ".toml", ".cast")
ALLOWED = {"world", "localhost", "127.0.0.1", "0.0.0.0"}


def strings(value) -> list[str]:
    stack, found = [value], []
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def hosts_in(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        found |= {match.group(1).lower().rstrip(".") for match in URL.finditer(line)}
        found |= {match.group(1).lower().rstrip(".") for match in FETCH.finditer(line)}
    result = set()
    for host in found:
        name = host.split(":")[0]
        if not name or name in ALLOWED or name.startswith("world.") or name.endswith(FILE_SUFFIXES):
            continue
        result.add(f"internal:{name}" if PRIVATE.match(name) or "." not in name else name)
    return result


def seat_hosts(trial: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in sorted(trial.glob("seats/*/agent/trajectory.json")):
        steps = json.loads(path.read_text()).get("steps") or []
        hosts = set().union(*(hosts_in(text) for text in strings(steps))) if steps else set()
        if hosts:
            result[path.parts[-3]] = hosts
    return result


def main() -> int:
    jobs, pattern = Path(sys.argv[1]), sys.argv[2]
    public = 0
    internal = 0
    trials = 0
    for job in sorted(jobs.glob(pattern)):
        for trial in sorted(path for path in job.iterdir() if (path / "result.json").is_file()):
            trials += 1
            found = seat_hosts(trial)
            if not found:
                continue
            hosts = set().union(*found.values())
            public += any(not host.startswith("internal:") for host in hosts)
            internal += any(host.startswith("internal:") for host in hosts)
            print(f"{job.name}/{trial.name}: " + "; ".join(f"{seat} {sorted(names)}" for seat, names in found.items()))
    print(f"{public} of {trials} trials name a public host; {internal} name a private address")
    return 1 if public else 0


if __name__ == "__main__":
    sys.exit(main())
