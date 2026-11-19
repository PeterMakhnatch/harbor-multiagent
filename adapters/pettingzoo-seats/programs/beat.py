import json
import sys

obs = json.loads(sys.stdin.read()).get("observation")
move = obs if type(obs) is int and 0 <= obs <= 2 else None
sys.stdout.write(json.dumps({"action": 0 if move is None else (move + 1) % 3}) + "\n")
