# Cramped Room parity

Run 2 scripted Harbor seats in the original Overcooked-AI kitchen. The seats act against the same world. The world advances only after both seats submit an action for the current tick. The task needs no model, API key, GPU, renderer, or external service during play.

From the repository root, with Docker and Compose running:

```sh
uv sync --locked
uv run harbor run -c examples/configs/features/cramped-room-parity.yaml
```

Open the resulting trial's `artifacts/logs/world/kitchen.jsonl`. It records 38 ordered joint actions, native post-action states, and native rewards and delivery events. At tick 38, `chef1` serves the only soup. Overcooked-AI awards 20 points.

The verifier reproduces every row from a fresh engine. It assigns `reward: 1.0` if the replay contains the delivery. It assigns `0.0` if the complete replay lacks it. Missing or inconsistent rows fail verification.

Each seat also writes a small scripted ATIF trace. These traces document the replay, not model inference. This example checks Harbor's transport and evidence collection. It does not measure teamwork quality or isolate the seats from untrusted opponents.

## Compare the two policies

Run both policies with the same job config. The second overlay makes `chef0` wait at every tick. It does not change `chef1` or the world.

```sh
for arm in tape wait; do
  uv run harbor run -c examples/configs/features/cramped-room-parity.yaml \
    --job-name "cramped-room-$arm" \
    --extra-docker-compose "examples/configs/features/cramped-room-$arm.override.yaml"
done
```

Both runs should retain 38 valid transitions and 2 seat traces. The `wait` run should finish without a delivery or an exception. Its expected Harbor reward is `0.0`, not a missing reward.

## Source and license

The world calls the original `OvercookedGridworld` transition code from [HumanCompatibleAI/overcooked_ai](https://github.com/HumanCompatibleAI/overcooked_ai) at `739950a079cdaed5a44fcc662efc40244c205d06`. The world image verifies the source archive and dependency hashes at build time. It retains the [MIT license](https://github.com/HumanCompatibleAI/overcooked_ai/blob/739950a079cdaed5a44fcc662efc40244c205d06/LICENSE) in `/opt/overcooked-ai/LICENSE`.
