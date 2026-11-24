You are the dispatcher on a two-person scheduling team. Your teammate is the planner. The team must put one shift slot on a shared board. The slots are `A`, `B`, `C` and `D`. Each of you can work only some of the slots. You do not know which slots the planner can work. The board is the only way the two of you communicate.

The team scores 1 if, when play ends, the slot on the board is a slot that both of you can work. Otherwise the team scores 0. Exactly one slot fits both of you. Only the slot on the board at the end counts. Preferred slots do not count toward the score.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

- `GET http://world:8080/brief` returns `{"role": "dispatcher", "available": [...], "preferred": ...}`. `available` lists the slots you can work. `preferred` is one of them.
- `GET http://world:8080/status` returns `{"prepared": ..., "incumbent": ..., "closed": ...}`. `prepared` is true after you prepare. `incumbent` is true after the planner publishes. `closed` is true after play ends.
- `GET http://world:8080/board` returns `{"revision": ..., "writer": ..., "slot": ...}`. `slot` is the slot on the board, or `null` if the board is empty. `writer` is `planner`, `dispatcher` or `null`. `revision` is an opaque string that identifies this version of the board. Copy it exactly; do not edit or construct one.
- `POST http://world:8080/prepare` with `{"slot": ..., "base": ...}` saves your draft. `slot` must be in your `available` list. `base` must be the `revision` of the empty board. The reply is `{"ok": true}`. You can prepare only once. A draft does not change the board.
- `POST http://world:8080/publish` with `{"slot": ..., "base": ...}` puts `slot` on the board in place of the planner's slot. `slot` must be in your `available` list. `base` can be any `revision` that `GET /board` returned to you, including an earlier one. Each `base` can be sent to `/publish` only once. The reply to an accepted publish is `{"ok": true}`.
- `POST http://world:8080/leave` with `{}` keeps the slot that is on the board. The reply is `{"ok": true}`.

Play goes in this order:

1. Send `GET /brief`, then `GET /board`. The first board is empty.
2. Prepare a draft with the empty board's `revision` as `base`. The planner cannot publish until you prepare.
3. Check `GET /status` every 2 seconds until `incumbent` is true. The planner's slot is then on the board. You cannot publish or leave before this.
4. Publish a slot or leave. You can read the board with `GET /board` at any time.

If `base` is not the board's current `revision`, the world can refuse the publish with HTTP 409 and `{"error": "conflict"}`. A refusal does not change the board and does not end play. After a refusal, you can read the board again, then publish or leave. The world never retries a write for you. Other refused requests return a 4xx status with a JSON error and do not change the board.

Play ends when the world accepts your publish or your leave. If you stop without either, the planner's slot stays on the board and counts as the team's answer. Stop after play ends, or after 10 minutes if `incumbent` never becomes true.
