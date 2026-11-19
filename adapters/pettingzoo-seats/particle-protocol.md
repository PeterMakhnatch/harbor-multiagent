# Separate extension: does the same adapter work for a third package and a third seat?

The first bridge experiment uses 8 cells and only 2 players. This extension does not change that denominator. It uses the separately distributed `mpe2==1.1.1`, not an example from `pettingzoo.classic`.

Use `mpe2.simple_spread_v3:parallel_env`, 3 discrete-action agents, 4 cycles and seed 19. Map Harbor `p0`, `p1`, `p2` to `agent_0`, `agent_1`, `agent_2`. Register 2 new native trials: all 3 choose the lowest action; only p2 chooses the highest action. The trusted author supplies the pinned MPE2 dependencies. Use the existing bridge, seat actor, task generator and exporter without changes specific to MPE2.

Check each actual role observation, legal action, accepted action and payoff against the official engine. Check that the bridge counts 4 simultaneous groups and 12 decisions per complete trial. Each last step is a time limit, not a terminal win. On the first changing group, the 2 unchanged seats should have identical inputs and actions. Use numeric tolerances only for the documented cross-platform floating-point comparison. Report false mismatches instead of relaxing a check after viewing the native result.

Prior feasibility check: the host engine with all actions zero produced 4 groups of 3 agents and marked all 3 truncated. Each agent's observation contained 18 finite scalars. No native trial was run in that check. The game payoff is not a protocol completion score.
