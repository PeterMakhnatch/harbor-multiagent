# PettingZoo seats

This adapter runs installed, trusted, discrete PettingZoo games on unmodified Olam Harbor at `6aeb609e31fd77e5f89ce3bcb018db8045520fb7`. The four contributor lanes below have separate inputs and outputs.

Start with the [measured results and offline verification](results.md). The [smaller contribution on the personal fork](https://github.com/PeterMakhnatch/harbor-multiagent/pull/22) contains only the reusable task generator, runtime, presets and component tests.

Registered protocols: [classic games](protocol.md), [three-role particles](particle-protocol.md), [external programs](program-protocol.md), [source-only branches](branch-protocol.md), [private policy history](window-experiment/protocol.md) and [cooperative communication](communication-experiment/protocol.md).

## Lane 1. Run one named cell

Prepare a new directory. Then run one cell. `--harbor` must be an absolute path. Each cell is claimed once. A repeated cell is refused. There is no retry.

```bash
python study.py prepare ROOT
python study.py run ROOT rps-ll-a --harbor /ABS/harbor
```

`prepare` writes `plan.json` with schema `seatlab.pz-plan/v1`. Cell ids, in order, are `rps-ll-a`, `rps-ll-b`, `rps-lh`, `rps-copy-beat`, `connect-ll-a`, `connect-ll-b`, `connect-lh`, and `connect-hl`.

The particle suite uses a separate directory. Do not put both suites in one plan.

```bash
python study.py prepare ROOT --suite particle
python study.py run ROOT spread-lll --harbor /ABS/harbor
```

Particle cells are `spread-lll` and `spread-llh`. That plan schema is `seatlab.pz-particle-plan/v1`.

`attempts/<id>/exit.json` records `exit_code` and `trial_name`. It does not record a payoff.

Offline checks use the installed game, not the adapter world:

```bash
python reference.py rps --policies low,rps-copy --seed 19
python reference.py connect-four --policies low,high --seed 19 --report /path/pettingzoo.json
python particle_reference.py REPORT --p2 low
```

`reference.py` prints schema `seatlab.pz-reference/v1`. `--policies` is `low`, `high`, `rps-copy`, or `rps-beat-last`. One name applies to both seats. `--seed` defaults to 19. `--report` compares a native world file. `particle_reference.py` requires `--p2 low` or `--p2 high`.

## Lane 2. Read export and the wire

The world writes `/logs/world/pettingzoo.json`. The schema is `seatlab.pz-native/v1`. The keys are `schema`, `case_sha256`, `roles`, `steps`, `returns`, `terminated`, and `truncated`. `returns` are the raw role payoffs. They are not summed.

The verifier writes `/logs/verifier/reward.txt` as `1` only when the protocol is complete. That scalar is transport acceptance. A draw, a loss, or a time limit is still a complete transport. A missing game is not payoff 0.

```bash
python export.py NATIVE_TRIAL NEW_OUTPUT_DIR
```

`NEW_OUTPUT_DIR` must not exist. The command writes `episodes.jsonl`, `order.json`, and `outcomes.json`. It prints the outcomes object. `outcomes.json` uses schema `seatlab.pz-outcomes/v1`. `completion` is 1. `returns`, `terminated`, and `truncated` stay per role.

`order.json` field `steps` is the world order. Each inner list is one world step.

Use `api` `parallel` when the game steps a joint action. One world step then lists every active role. Use `api` `aec-turns` only for a genuine turn-taking game. PettingZoo `metadata["is_parallelizable"]` must be false. The world drains dead AEC steps and does not record them as frames. A parallelizable AEC game is rejected. Use the Parallel API for that game.

## Lane 3. Add a world

Write a spec with schema `seatlab.pz-world/v1`. The keys are `schema`, `api`, `factory`, `kwargs`, `roles`, `seed`, and `max_world_steps`. `api` is `parallel` or `aec-turns`. `factory` is an installed `module:function`. The trusted world imports it. `roles` maps 2 to 8 names `p0` through `p7` to unique agent ids. Actions must be discrete.

Pin runtime packages as `name[extras]==exact version`. Pass that file with `--requirements`. The image copies `requirements.txt`. It does not put package text in the shell. Omit `--requirements` to use the built-in pins, including `pettingzoo==1.26.1`.

```bash
python materialize.py SPEC OUT --policy p0=low --policy p1=high --requirements PINS.txt
```

`--policy` is `ROLE=POLICY`. Bind every role. The world process writes the outcome file. You do not patch Harbor. Seed 19 is the registered seed. A new game, seed, or policy needs a new label.

## Lane 4. External ProgramSeat

A program reads one view JSON on stdin. It writes one object `{"action": <int>}`. The plugin joins the action to the step. The program does not see the seed, the other seat, or a model. The host executable is trusted by the operator. This path has no OS sandbox and no network isolation.

```bash
python program_study.py prepare ROOT --node /ABS/node --python /ABS/python
python program_study.py run ROOT program-mixed --harbor /ABS/harbor
```

`--node`, `--python`, and `--harbor` must be absolute executables. Cells are `program-mixed`, `program-both`, `program-second`, and `program-invalid`. The plan schema is `seatlab.pz-program-plan/v1`.

A program seat uses `import_path` `program_seat:ProgramSeat`, `model_name` null, and `kwargs.argv` as `[absolute runtime, absolute script, ...]`. `kwargs.call_timeout_sec` is 10. Connect Four last-legal passes the extra arg `high`. An oracle seat uses `name` `oracle`. Its compose policy is `low` unless that seat's registered oracle policy is different. There is no runtime model dependency.
