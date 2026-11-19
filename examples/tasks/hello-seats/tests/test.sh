#!/bin/bash
# Grades p1 on the world's own record. The world sets each note's author from
# the token the request sent, so a note by p1 came from p1's container. The
# seats' logs in /logs/seats are not evidence: each seat writes its own.

python3 - <<'PY'
import json
from pathlib import Path

try:
    lines = Path("/logs/world/notes.jsonl").read_text().splitlines()
    notes = [json.loads(line) for line in lines if line.strip()]
except (OSError, ValueError):
    notes = []

p1_notes = [note for note in notes if note.get("author") == "p1"]
p2_note_ids = {note.get("id") for note in notes if note.get("author") == "p2"}
reward = int(
    len(p1_notes) == 1
    and p1_notes[0].get("text") == "hello from p1"
    and p1_notes[0].get("reply_to") in p2_note_ids
)
print(f"p1's notes: {p1_notes}; ids of p2's notes: {p2_note_ids}")
Path("/logs/verifier/reward.txt").write_text(f"{reward}\n")
PY
