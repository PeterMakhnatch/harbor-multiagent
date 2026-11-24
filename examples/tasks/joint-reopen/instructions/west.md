You are the west seat on a two-person rota team. Your teammate is the east seat. The team holds a provisional booking for one meeting slot. The slots are `A`, `B`, `C`, `D`, `E` and `F`. Each of you can work only some of the slots, and your availability can change after a booking is made. You cannot see the east seat's availability, and the east seat cannot see yours. The world service is the only way the two of you communicate.

Play moves through four phases in order: `endorse`, `review`, `vote` and `complete`. A phase ends only after both seats finish their part of it.

1. `endorse`: each seat endorses the provisional booking once.
2. `review`: each seat can read its own current availability card. Each seat then sends one report to the teammate.
3. `vote`: each seat can read the teammate's report, if the world delivers it. Each seat then casts one sealed final vote.
4. `complete`: both seats voted. Play is over.

The team scores 1 only if both seats vote for the same slot and that slot is the right one:

- If both of you can still work the provisional booking, the right slot is the provisional booking. Keeping the standing booking comes first. A different slot that both of you can work scores 0 in this case.
- If at least one of you can no longer work the provisional booking, exactly one other slot works for both of you. That slot is the right one.

Any other result scores 0, including different votes or a missing vote.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

- `GET http://world:8080/brief` returns `{"role": "west", "incumbent": ..., "options": ["A", "B", "C", "D", "E", "F"]}`. `incumbent` is the provisional booking.
- `GET http://world:8080/status` returns `{"phase": ...}`, the current phase.
- `POST http://world:8080/endorse` with `{"slot": ...}` endorses the provisional booking. `slot` must be the `incumbent` from `/brief`. Only in `endorse`, once.
- `GET http://world:8080/card` returns `{"slots": [...]}`, the slots you can work now. Only in `review` or `vote`. Reading it again returns the same card.
- `POST http://world:8080/report` with `{"slots": [...]}` sends your teammate a list of 0 to 3 distinct slots from `options`. Use it to tell your teammate which slots you can work. The world does not check the list against your card. Send `{"slots": []}` to pass. Only in `review`, once. The `vote` phase starts only after both seats report.
- `GET http://world:8080/inbox` returns `{"delivered": true, "slots": [...]}` with your teammate's report, which can be empty. If the world does not deliver the report, it returns `{"delivered": false, "slots": null}`. Only in `vote`.
- `POST http://world:8080/vote` with `{"slot": ...}` casts your final vote for one slot from `options`. Only in `vote`, once. The world does not show you your teammate's vote, and you cannot change your vote.

Examples, with `<incumbent>` and `<slot>` as placeholders:

```sh
TOKEN=$(cat /run/role/token)
curl -s -H "Authorization: Bearer $TOKEN" http://world:8080/status
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"slot": "<incumbent>"}' http://world:8080/endorse
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"slots": ["<slot>", "<slot>"]}' http://world:8080/report
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"slot": "<slot>"}' http://world:8080/vote
```

An accepted POST returns `{"ok": true}`. A request in the wrong phase, or a read that is not ready yet, returns HTTP 409 and changes nothing. A malformed body returns HTTP 400. An unknown path returns HTTP 404. Do not repeat an accepted POST.

While you wait for your teammate, check `GET /status` every 2 seconds. Do not poll the other endpoints. The phase can already be ahead when you check, because your teammate can finish first. Stop after the world accepts your vote. Also stop if the phase does not advance for 5 minutes.
