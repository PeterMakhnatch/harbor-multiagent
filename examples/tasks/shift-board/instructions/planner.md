You are the planner on a two-person scheduling team. Your teammate is the dispatcher. The team must put one shift slot on a shared board. The slots are `A`, `B`, `C` and `D`. Each of you can work only some of the slots. You do not know which slots the dispatcher can work. The board is the only way the two of you communicate.

The team scores 1 if, when play ends, the slot on the board is a slot that both of you can work. Otherwise the team scores 0. Exactly one slot fits both of you. Only the slot on the board at the end counts. Preferred slots do not count toward the score.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

- `GET http://world:8080/brief` returns `{"role": "planner", "available": [...], "preferred": ...}`. `available` lists the slots you can work. `preferred` is one of them.
- `GET http://world:8080/status` returns `{"prepared": ..., "incumbent": ..., "closed": ...}`. `prepared` is true after the dispatcher prepares a draft. `incumbent` is true after you publish. `closed` is true after play ends.
- `GET http://world:8080/board` returns `{"revision": ..., "writer": ..., "slot": ...}`. `slot` is the slot on the board, or `null` if the board is empty. `writer` is `planner`, `dispatcher` or `null`. `revision` is an opaque string. Copy it exactly; do not edit or construct one.
- `POST http://world:8080/publish` with `{"slot": ..., "base": ...}` puts `slot` on the board. `slot` must be in your `available` list. `base` must be the `revision` from your latest `GET /board`. The reply is `{"ok": true}`. You can publish only once.

The board opens only after the dispatcher prepares. Check `GET /status` every 2 seconds until `prepared` is true. Then send `GET /board` and publish one of your available slots with that board's `revision` as `base`.

A refused request returns a 4xx status with a JSON error and does not change the board. Do not repeat an accepted publish.

Your part ends after the world accepts your publish. After that, only the dispatcher can change the board. Stop after your publish is accepted, when `closed` is true, or after 10 minutes if `prepared` never becomes true.
