# PettingZoo seats

This adapter turns an installed discrete PettingZoo world into a stock Harbor seat task. It does not patch Harbor. The world imports the specified factory inside its task container. Use trusted factories and dependencies; this adapter does not isolate untrusted code.

Stock seats follow the trial contract at commit `6aeb609e31fd77e5f89ce3bcb018db8045520fb7`.

## Materialize

`OUT` must not exist. Bind every role. Policies are `low`, `high`, `rps-copy`, and `rps-beat-last`. `low` is the first legal action. `high` is the last. `rps-copy` and `rps-beat-last` read an integer observation in 0..2. Otherwise they play 0. Omit `--requirements` to use the built-in pins, including `pettingzoo==1.26.1`. A pin file is `name[extras]==version`, one line each. The image copies that file.

```bash
python adapters/pettingzoo-seats/materialize.py SPEC OUT --policy p0=low --policy p1=high [--requirements FILE]
```

A spec uses schema `seatlab.pz-world/v1`. Keys are `schema`, `api`, `factory`, `kwargs`, `roles`, `seed`, and `max_world_steps`. `api` is `parallel` or `aec-turns`. `factory` is `module:function`. `roles` maps 2 to 8 names `p0` through `p7` to unique agent ids. Actions must be Discrete. An AEC game is rejected unless `metadata["is_parallelizable"]` is false.

## Three games

Run these commands from the repository root.

```bash
python adapters/pettingzoo-seats/materialize.py adapters/pettingzoo-seats/presets/rps.json out/rps --policy p0=low --policy p1=rps-beat-last
python adapters/pettingzoo-seats/materialize.py adapters/pettingzoo-seats/presets/connect-four.json out/connect-four --policy p0=low --policy p1=high
python adapters/pettingzoo-seats/materialize.py adapters/pettingzoo-seats/presets/simple-spread.json out/simple-spread --policy p0=low --policy p1=low --policy p2=high --requirements adapters/pettingzoo-seats/presets/simple-spread-requirements.txt
```

Rock-paper-scissors is parallel. Connect Four is turn-taking. Simple spread is parallel, with 3 discrete-action seats, and needs its pin file. Each task writes `instructions/<seat>.md`. Compose sets `POLICY`. `solution/solve.sh` runs `python /app/actor.py`.

## Run named seats

Docker must be running. Use unmodified `harbor trial start -c`. The file implements `harbor.models.trial.config:TrialConfig`. Name every generated seat. Do not set top-level `agent` or `user_agent`.

Save this object as `out/rps-trial.json`:

```json
{
  "schema_version": 1,
  "task": {"path": "out/rps"},
  "trials_dir": "trials",
  "trial_name": "rps-low-beat",
  "participants": [
    {"seat": "p0", "name": "oracle"},
    {"seat": "p1", "name": "oracle"}
  ],
  "environment": {"type": "docker", "force_build": true, "delete": true}
}
```

```bash
harbor trial start -c out/rps-trial.json
```

Connect Four uses the same two seats. Give it its own `task.path` and `trial_name`. Simple spread adds `{"seat": "p2", "name": "oracle"}`.

## Returns and scope

The world writes `/logs/world/pettingzoo.json` with schema `seatlab.pz-native/v1`. Keys are `schema`, `case_sha256`, `roles`, `steps`, `returns`, `terminated`, and `truncated`. `returns` are raw per-role payoffs. The world adds each step reward to that role. It does not sum roles. An AEC step uses instantaneous `env.rewards`, not cumulative `last()`.

The verifier writes `/logs/verifier/reward.txt` as `1` only when the protocol is complete. That scalar is transport acceptance, not the payoff. A draw, a loss, or a recorded truncation is still complete. A missing game is not payoff 0. Completion needs the schema, the case digest, and the declared roles. Each role needs one legal decision, a finite return, and a terminal flag. The actor audit must match the accepted decisions.

Scope is one episode. The roster is fixed at reset. The world rejects agent birth and a seat that leaves before a terminal flag.

## Replay

The adapter has no analysis dependency. For an optional comparison, install [SeatLab](https://github.com/PeterMakhnatch/harbor-seatlab) in your analysis environment. Download the [independent exporter from the measured revision](https://raw.githubusercontent.com/PeterMakhnatch/harbor-multiagent/52ca9cf42fc2110a130ba84eb81b5ba5e65f6975/adapters/pettingzoo-seats/export.py) as `export.py`. Run the same case twice with different trial names. Export their native directories into 2 new output directories:

```bash
python export.py trials/rps-source source
python export.py trials/rps-target target
seatlab team-align source/episodes.jsonl target/episodes.jsonl \
  --source-order source/order.json --target-order target/order.json
```

The exporter checks the native trial ID, the world transcript, and the accepted actions in each seat’s audit. `team-align` reads the exported data; it does not execute the task. A different policy needs a fresh native run to establish its real outcome.
