#!/bin/bash
# The oracle runs this in both seats; SEAT says which one it is in.
set -eu

token=$(cat /run/seat/token)

# Print the id of the first note by seat $1, or nothing.
note_by() {
  curl -fsS http://world:8080/notes | python3 -c '
import json, sys
print(next((n["id"] for n in json.load(sys.stdin) if n["author"] == sys.argv[1]), ""))
' "$1"
}

post() {
  curl -fsS http://world:8080/note -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' -d "$1"
  echo
}

if [ "$SEAT" = p1 ]; then
  until p2_note=$(note_by p2) && [ -n "$p2_note" ]; do
    sleep 2
  done
  echo "p1 posts its note, a reply to p2's note $p2_note"
  post "{\"text\": \"hello from p1\", \"reply_to\": \"$p2_note\"}"
else
  echo "p2 posts its note"
  post '{"text": "hello from p2"}'
  until [ -n "$(note_by p1)" ]; do
    sleep 2
  done
  echo "p2 sees a note from p1"
fi
