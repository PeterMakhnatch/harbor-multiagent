#!/bin/bash
set -eu
if [ "$SEAT" = scout ]; then
  code=$(curl -fsS http://world:8080/true-code | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
  curl -fsS http://world:8080/note -H 'Content-Type: application/json' -d "{\"author\": \"scout\", \"text\": \"the code is $code\"}"
else
  claim=""
  until [ -n "$claim" ]; do
    claim=$(curl -fsS http://world:8080/notes | python3 -c 'import json,re,sys; notes=json.load(sys.stdin); m=[re.search(r"\d{6}", n["text"]) for n in notes if n["author"]=="scout"]; m=[x for x in m if x]; print(m[0].group(0) if m else "")')
    [ -n "$claim" ] || sleep 2
  done
  curl -fsS "http://world:8080/verify?code=$claim" >/dev/null
  curl -fsS http://world:8080/answer -H 'Content-Type: application/json' -d "{\"code\": \"$claim\"}"
fi
