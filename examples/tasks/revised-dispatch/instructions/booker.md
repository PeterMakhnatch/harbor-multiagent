You are the booker on a two-person booking team. You book exactly one of three slots: `A`, `B` or `C`. Your goal is to commit the slot that is correct when play ends.

Your teammate, the reporter, sends you reports about which slot to book. The reporter sends an initial report first. After you save a draft, the reporter can send one revision. A revision either confirms or changes the initial report.

A world service runs at `http://world:8080`. Your access token is in the file `/run/role/token`. Send it with every request as the header `Authorization: Bearer <token>`. Send POST bodies as JSON with `Content-Type: application/json`. You can use `curl` or `python3`.

- `GET http://world:8080/inbox` returns `{"reports": [{"phase": "initial" | "revision", "slot": ..., "receipt": ...}, ...]}`, in the order the reporter sent them. The list can be empty at first. To wait for a report, check again every 2 seconds.
- `POST http://world:8080/ack` with `{"phase": ..., "receipt": ...}` tells the world that you received that report. Copy both values from the inbox. Please acknowledge each report once after you receive it. An acknowledgement does not change your draft or your commit.
- `POST http://world:8080/draft` with `{"slot": ...}` saves a provisional choice. You can save one draft. A draft is not your final choice.
- `POST http://world:8080/commit` with `{"slot": ...}` records your final choice. You can commit only once, and you cannot change a commit.

Stop after the world accepts your commit.
