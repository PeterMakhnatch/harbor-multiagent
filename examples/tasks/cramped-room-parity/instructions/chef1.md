You are `chef1` in Overcooked's `cramped_room`. Add the first onion, collect a dish, and serve one soup on transition 38.

Use `http://world:8080`. For each tick from 0 to 37:

1. Poll `GET /state` until the response reports that tick.
2. Send one JSON request to `POST /action/chef1`. Use `{"tick": 0, "action": [1, 0]}` for tick 0.
3. After HTTP 200, wait for `GET /state` to report the next tick. The world advances only after both chefs submit.

Use `N = [0, -1]`, `S = [0, 1]`, `E = [1, 0]`, and `W = [-1, 0]`.
Use `WAIT = [0, 0]` and `USE = "interact"`. Use the actions in order within each range:

| Tick before action | Action |
| --- | --- |
| 0–9 | E, USE, W, N, USE, S, W, S, USE, E |
| 10–14 | WAIT at each tick |
| 15 | N |
| 16–33 | WAIT at each tick |
| 34–37 | USE, E, S, USE |

Do not send an action for `chef0` or send your action twice. Use a time limit for polling.
Stop after `GET /state` reports tick 38. Do not submit an action at tick 38.
