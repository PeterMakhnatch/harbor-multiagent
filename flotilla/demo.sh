#!/usr/bin/env bash
# Run five Flotilla oracle trials on local Docker and keep the results under TMPDIR.
# Usage: bash flotilla/demo.sh
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FLOTILLA=$REPO/flotilla
HARBOR=${FLOTILLA_DEMO_HARBOR:-$HOME/Developer/harbor-multiagent-flotilla/.venv/bin/harbor}
PY=$(dirname "$HARBOR")/python
SLOT=/tmp/crucible-docker-slot.sh
export HARBOR_TELEMETRY=0

if (($#)); then
    echo "usage: bash flotilla/demo.sh" >&2
    exit 2
fi
for executable in "$HARBOR" "$PY" "$SLOT"; do
    if [[ ! -x $executable ]]; then
        printf 'demo: missing executable %s\n' "$executable" >&2
        exit 2
    fi
done
harbor_dir=$(cd "$(dirname "$HARBOR")" && pwd)
HARBOR=$harbor_dir/$(basename "$HARBOR")
PY=$harbor_dir/python
"$PY" -c 'import yaml' || { echo "demo: the Harbor venv needs PyYAML" >&2; exit 2; }

out=$(mktemp -d "${TMPDIR:-/tmp}/flotilla-demo.XXXXXX")
out=$(cd "$out" && pwd -P)
printf 'Demo output: %s (kept on success or failure)\n' "$out"
trap 'printf "Demo failed. See %s\n" "$out" >&2' ERR
tasks=$out/tasks
jobs=$out/jobs
config=$out/demo-4seat.json
analysis=$out/probe.json

"$PY" "$FLOTILLA/build_tasks.py" --family probe --variant verify-readfirst --read-first \
    --instances 0-1 --conditions talk-quiet,talk-false --out "$tasks"
"$PY" "$FLOTILLA/build_tasks.py" --family probe --variant audit \
    --instances 0 --conditions talk-false --out "$tasks"
"$PY" "$FLOTILLA/qa.py" "$tasks/*/*" | tee "$out/qa.log"
"$PY" "$FLOTILLA/make_jobs.py" --tasks "$tasks/*/*" --agent oracle --env docker \
    --name demo --concurrent 1 --jobs-dir "$jobs" --out "$out"

"$PY" - "$config" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
config = json.loads(path.read_text())
seats = ["p1", "p2", "p3", "p4"]
# Proxy arguments keep the cached install layer usable but block package downloads on a cache miss.
proxy = {name: "http://127.0.0.1:9" for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
compose = {
    "services": {service: {"build": {"args": proxy}} for service in ["main", *seats]},
    "networks": {f"seat-{seat}": {"internal": True} for seat in seats},
}
overlay = path.with_name("compose-offline.json")
overlay.write_text(json.dumps(compose))
config["environment"]["extra_docker_compose"] = [str(overlay)]
path.write_text(json.dumps(config, indent=2) + "\n")
PY

cd "$out"
"$SLOT" bash -euc '
    if [[ -n ${DOCKER_CONTEXT:-} || -z ${DOCKER_HOST:-} ]]; then
        endpoint=$(docker context inspect --format "{{.Endpoints.docker.Host}}")
    else
        endpoint=$DOCKER_HOST
    fi
    if [[ $endpoint != unix://* ]]; then
        echo "demo: Docker must use a local unix socket" >&2
        exit 2
    fi
    if ! docker image inspect python:3.13-slim >/dev/null; then
        echo "demo: the local Docker image python:3.13-slim is required; the demo does not pull it" >&2
        exit 2
    fi
    exec "$@"
' demo-slot "$HARBOR" run -c "$config" 2>&1 | tee "$out/harbor.log"

"$PY" "$FLOTILLA/docs/results/scripts/probe_analysis.py" "$jobs" 'demo-*' --tasks "$tasks" \
    --models oracle --json "$analysis" >"$out/probe.md"

trial=$("$PY" - "$analysis" "$jobs" <<'PY'
import json
import sys
from pathlib import Path

analysis = json.loads(Path(sys.argv[1]).read_text())
jobs = Path(sys.argv[2])
rows = analysis["rows"]
if len(rows) != 5 or analysis["excluded"] or analysis["duplicates"] or analysis["unmapped"]:
    sys.exit("demo: expected five valid, unique trials; see probe.md")
for row in rows:
    trial = jobs / row["job"] / row["trial"]
    result = json.loads((trial / "result.json").read_text())
    reward = result["verifier_result"]["rewards"].get("reward")
    print(f"{row['kind']} {row['arm']} #{row['instance']}: reward {reward}", file=sys.stderr)
    if reward != 1 or not row["counted"] or row["model"] != "oracle":
        sys.exit(f"demo: unexpected result in {trial}")
    if row["kind"] == "audit":
        print(trial)
PY
)

export PYTHONPATH=$FLOTILLA/analysis
"$PY" -m flotilla_analysis.cards "$out/cards" --analysis "$analysis" --jobs "$jobs" --tasks "$tasks"
"$PY" -m flotilla_analysis.timeline "$trial" -o "$out/timeline.html" --tasks "$tasks"

printf 'Jobs:     %s\nAnalysis: %s\nCard:     %s\nTimeline: %s\n' \
    "$jobs" "$analysis" "$out/cards/index.html" "$out/timeline.html"
