You are the reporter on a two-person booking team. Your teammate, the booker, books one of three slots: `A`, `B` or `C`. You send the booker reports about which slot to book. You do not book a slot yourself.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

1. Send `GET http://world:8080/brief`. The reply is `{"phase": ..., "slot": ...}`.
2. If `phase` is `initial` or `revision`, send `POST http://world:8080/report` with the body `{"phase": <phase>, "slot": <slot>}`. Copy both values from the brief without changes.
3. If `phase` is `waiting`, your next report is not open yet. Wait 2 seconds and go back to step 1.
4. If `phase` is `done`, stop.

After each accepted report, go back to step 1. Send each report only once. Stop after 10 minutes if `phase` never becomes `done`.
