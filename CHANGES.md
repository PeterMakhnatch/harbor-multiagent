# Changes from Harbor

This repository is upstream Harbor ([harbor-framework/harbor](https://github.com/harbor-framework/harbor))
merged up to `2ea52267` (v0.26.0 plus 34 commits), plus multi-agent tasks:
several Harbor agents in one environment, each in its own Compose service
(its seat). See [docs-mintlify/tasks/multi-agent.mdx](docs-mintlify/tasks/multi-agent.mdx).

## Harbor files touched

| File | Why |
|---|---|
| `.github/workflows/*.yml` | Harbor runs its CI on Blacksmith runners (`blacksmith-<n>vcpu-<os>` labels), which only a repository with the Blacksmith GitHub App installed can use; this repository does not have it, so those jobs would wait in the queue and never start. Each job runs on the GitHub-hosted runner of the same OS instead (`ubuntu-26.04`, `ubuntu-24.04`, `windows-2025`); the steps are Harbor's. The `agents` runtime suite's test VM gets 4 CPUs and 12 GB, as Harbor's other two runtime suites do, where Harbor gives it 8 and 24: a GitHub-hosted Linux runner has 4 CPUs and 16 GB, and QEMU fails to start a VM larger than its host. The non-runtime suites run one pytest worker per CPU (`-n auto`) where Harbor runs eight: eight workers on four CPUs made the relay tests' 2 to 5 s waits time out; with half the CPUs the Windows suite takes 13 to 16 minutes, so both non-runtime jobs get 30 minutes where Harbor gives 15. Tradeoff: the suites run slower than on the Blacksmith sizes Harbor picked. |
| `README.md` | Describes this fork and how to install it. Harbor's README is at [harbor-framework/harbor](https://github.com/harbor-framework/harbor). |
| `src/harbor/models/task/config.py` | `SeatConfig` and `TaskConfig.agents` (`[agents.<service>]`), with the seat refusals at task load, including a seat named `main`. |
| `src/harbor/models/task/paths.py` | `seat_instruction_path`: `instructions/<seat>.md`. |
| `src/harbor/models/task/task.py` | Reads each seat's instruction and does not require `instruction.md` for a seat task. |
| `src/harbor/models/trial/config.py` | `Participant` (an `AgentConfig` plus `seat`) and `TrialConfig.participants`, refusing duplicate seats and per-agent settings seats cannot honor. |
| `src/harbor/models/job/config.py` | `JobConfig.participants`. |
| `src/harbor/job_plan.py` | Passes the job's participants into each trial config, as it does `user_agent`. |
| `src/harbor/models/job/lock.py` | Records participants in `lock.json` and its equality, so a changed roster is a changed run; the roster compares in seat order, so the same roster listed in another order is the same run. |
| `src/harbor/models/trial/result.py` | `SeatResult` and `TrialResult.seat_results`; seat errors count as recorded errors and seat usage counts in the token and cost totals. The cost total is `None` when a seat that ran a model reported no cost, so an unknown cost does not read as the other seats' sum; a seat without a model costs nothing. |
| `src/harbor/environments/base.py` | `service_upload_file` and `service_upload_dir`, raising the existing unsupported-service error off `main`, and `wait_for_services`, which raises it on an environment without Compose. A seat trial calls it on the agents' services before installing agents, so they are running, and healthy if they have a healthcheck, as Compose judges; it does not wait on every service because a project may have one-shot services that exit. |
| `src/harbor/environments/docker/docker.py` | Docker's `service_upload_file`, `service_upload_dir` and `wait_for_services`. |
| `src/harbor/environments/docker/docker_unix.py` | The upload helpers take a service; they were hard-coded to `main`. |
| `src/harbor/environments/dind_compose.py` | `upload_file` and `upload_dir` take a service, as the downloads do, and the transport gains `service_upload_file` and `service_upload_dir`, so every cloud environment that runs Compose through this layer can host multi-agent tasks. `upload_dir` creates its target directory first (`mkdir -p`, as root), as the local docker environment's tar upload does; Harbor's `docker compose cp` fails when the target's parent does not exist. `wait_for_services` runs `docker compose up --wait` on the named services. |
| `src/harbor/environments/compose_service_ops.py` | `service_upload_file`, `service_upload_dir` and `wait_for_services` on the shared mixin, dispatching to `main` or the compose transport like the downloads. |
| `src/harbor/environments/gke.py`, `tests/unit/environments/test_gke.py` | The Compose (DinD) strategy's retrying `upload_file` and `upload_dir` overrides take the `service` argument through, which uploads into an agent's own service need. Exec environment forwarding also differs as described below; otherwise multi-agent tasks on `gke` have the limits of Harbor's GKE environment. |
| `src/harbor/environments/sail.py`, `tests/unit/environments/test_sail.py`, `pyproject.toml`, `uv.lock` | Harbor's Sail environment picks a Sailbox through the SDK's size labels (`Sailbox.size_for`), which stop at 8 vCPUs, so a task or an `override_cpus` above 8 fails before a Sailbox is created; a multi-agent task that runs a whole Compose project in one VM needs more. Sail's SDK now sizes by vCPU count, 1 to 64, and has dropped `size_for`, so the provider passes the task's CPUs as `vcpu_count` and its memory and storage as whole-GiB limits, and the `sail` extra requires `sail>=0.18.1`. The provider also takes `docker_config_path`, a Docker `config.json`, as the GKE environment does: Sail builds a task's images on its own builders, which have no registry login, so a private image (a compose service's `image:`, a Dockerfile's base, or the task's `docker_image`) failed the build. Each login in the file is passed to Sail's image build as a value sent only with that request. For a Compose task, Harbor's Sail environment has Sail build the project's service images ahead of time into the VM's image (`Image.from_compose`) and only starts them in the VM. Sail's build refuses a service `image:` that carries a digest ("is pinned by digest, but a stored image is found by tag only"), so a project that pins its prebuilt images cannot start. Here a Compose task boots Sail's devbox image, which has Docker, and `docker compose build` and `up` run inside the VM, as in the Daytona, Modal and GKE environments; `docker_config_path` is staged as that Docker's login. Building and pulling need the network, so a Compose VM is created open and gets the task's network policy once its services are up; a single-container VM still has it from creation. The `auths` entries without a credential (written when a credential helper holds it) are skipped, a Docker Hub shorthand image (`org/image`) matches the `docker.io` login, and `docker_config_path` may start with `~`. `max_lifetime_seconds` defaults to 86400, where Harbor's default is no limit, so a Sailbox whose Harbor process dies is still terminated; a caller can pass another value. The Compose resources override reserves no memory for `main`: Harbor's reserves the task's memory, which Docker turns into `memory.low` on main's cgroup, and when that is larger than the VM's memory the kernel reclaims every page from the project's other services, which made a database service time out connections; a Sailbox runs one trial, so the reservation protects nothing. A readiness wait that times out names the last attempt's output. Sail refuses a disk under `MIN_DISK_GIB` (8 GiB) and memory under the least its vCPU count allows (16 GiB), so a task that asks for less, such as a verifier environment with `storage_mb = 4096`, failed at create; the provider raises memory and disk to those minimums, as the SDK's own environment does. Tradeoff: this file differs from Harbor's in sizing, in how a Compose project starts, in its lifetime default and in its memory reservation; a Compose trial builds and pulls on every start, where Sail's build cached the result; Sail's own environment in its SDK (`sail.harbor:SailboxEnvironment`) sizes by vCPU count but has the same digest rule and takes no registry login. |
| `src/harbor/environments/sail.py` (options) | `SailOptions` declares `docker_config_path`. |
| `src/harbor/trial/regrade.py` | `_compute_agent_setup_timeout_sec` takes the optional `agent_config` argument that `Trial`'s takes for seats. |
| `docs-mintlify/docs.json` | Lists `tasks/multi-agent` in the Tasks group. |
| `src/harbor/cli/trials.py` | `harbor trial start` lists a seat trial's participants (`seat: harness model`, agents under test marked) where it prints `Agent:` for one agent, and preflights each participant instead of the unused default agent. SIGTERM raises `KeyboardInterrupt`, as `harbor run` already makes it do, so a terminated trial takes the cancellation path and writes `result.json` with the logs, tokens and cost collected so far; under Harbor, SIGTERM ends `harbor trial start` without a result. |
| `src/harbor/job.py`, `src/harbor/cli/jobs.py` | A job with `participants` preflights them instead of the unused default agent. |
| `src/harbor/trial/trial.py` | `Trial.create` routes seat tasks to `SeatsTrial`, and refuses `participants` on a task without seats and a regrade of a seat task. `_scrub_jobs_dir` takes its secrets from a new `_secrets_to_scrub`, so a seat trial scrubs once with every seat's secrets. `_secrets_to_scrub`, `_validate_agent_capabilities` and `_compute_agent_setup_timeout_sec` take an optional agent and agent config, defaulting to `self.agent` and `self.config.agent`, so a seat trial passes each seat's instead of swapping trial fields; Harbor's callers are unchanged. A hook every trial registers (`_journal_event`) appends each lifecycle event (`{"event": "agent-start", "timestamp": ...}`) to `trial-events.jsonl` in the trial directory, so a process watching the directory can tell a single-agent or multi-agent trial's phase; Harbor writes no such file. |

Readers of `agent_info` are untouched: a seat trial fills it with its first
agent under test. Readers of `exception_info` are untouched too: a seat
trial puts a seat's error there, as a single-agent trial puts its agent's,
unless the verifier or the trial itself fails after it.

## Exec environment forwarding

Original Harbor puts exec environment values in `-e NAME=VALUE` arguments and,
on some remote transports, in the outer shell command. Process listings and
command diagnostics can expose agent credentials. Exec now passes `-e NAME`
with values in the CLI process environment. Remote providers use their native
environment mapping; transports without one use stdin or a staged shell file
inside a private directory. This keeps values out of both the Docker argv and
the outer transport argv while preserving per-exec overrides and empty values.
Tradeoff: these execution paths differ from Harbor, and providers that need a
staged environment file add an upload and cleanup for each exec. Environment
values remain accessible to processes allowed to inspect process environments;
this change prevents argument disclosure, not access by a privileged host.
Name-only flags share the client's process environment: variables such as
`DOCKER_HOST`, `COMPOSE_*` and `PATH` can also affect that client. Local Docker
keeps its existing credential-store protection by selecting the host's
`DOCKER_CONFIG` through `--config` when a seat overrides that variable.

The upstream files changed for this behavior are:

| Files | Change |
|---|---|
| `src/harbor/environments/docker/docker.py`, `src/harbor/environments/apple_container.py`, `src/harbor/environments/sbx/local.py` | Name-only flags and the subprocess environment. |
| `src/harbor/environments/dind_compose.py` | Name-only Compose exec flags, per-exec environment forwarding, and shared stdin/staged-file transport helpers. |
| `src/harbor/environments/beam.py`, `src/harbor/environments/blaxel.py`, `src/harbor/environments/daytona/environment.py`, `src/harbor/environments/hyperbrowser/environment.py`, `src/harbor/environments/islo.py`, `src/harbor/environments/kernel_docker.py`, `src/harbor/environments/langsmith.py`, `src/harbor/environments/modal.py`, `src/harbor/environments/novita.py`, `src/harbor/environments/prime.py`, `src/harbor/environments/runta/environment.py`, `src/harbor/environments/sail.py`, `src/harbor/environments/sbx/compose.py`, `src/harbor/environments/superserve.py`, `src/harbor/environments/tensorlake.py`, `src/harbor/environments/vercel/compose.py` | Forward exec values through the provider's environment mapping instead of command arguments. |
| `src/harbor/environments/ack.py`, `src/harbor/environments/boxd.py`, `src/harbor/environments/cua_cloud.py`, `src/harbor/environments/cwsandbox.py`, `src/harbor/environments/ec2.py`, `src/harbor/environments/gke.py`, `src/harbor/environments/openshift.py` | Forward values through stdin or private staged files where the remote exec interface has no environment mapping. |
| `src/harbor/environments/aca_sandbox.py`, `src/harbor/environments/runloop.py`, `src/harbor/environments/use_computer.py` | Keep user-switch and desktop exec environment values in staged scripts rather than nested shell arguments. |
| `tests/unit/environments/test_seat.py`, `tests/unit/environments/test_aca_sandbox.py`, `tests/unit/environments/test_prime.py`, `tests/unit/environments/test_runloop.py`, `tests/unit/environments/test_use_computer.py`, `tests/unit/environments/cwsandbox/conftest.py`, `tests/unit/environments/cwsandbox/test_cwsandbox.py` | Cover separate environment transport and retain the existing exec semantics. |
| `tests/unit/environments/test_ack.py`, `tests/unit/environments/test_apple_container.py`, `tests/unit/environments/test_beam.py`, `tests/unit/environments/test_blaxel.py`, `tests/unit/environments/test_boxd.py`, `tests/unit/environments/test_cua_cloud.py`, `tests/unit/environments/test_daytona.py`, `tests/unit/environments/test_docker.py`, `tests/unit/environments/test_ec2.py`, `tests/unit/environments/test_gke.py`, `tests/unit/environments/test_hyperbrowser.py`, `tests/unit/environments/test_islo.py`, `tests/unit/environments/test_kernel.py`, `tests/unit/environments/test_modal.py`, `tests/unit/environments/test_openshift.py`, `tests/unit/environments/test_runta.py`, `tests/unit/environments/test_sail.py`, `tests/unit/environments/test_tensorlake.py`, `tests/unit/environments/cwsandbox/test_cwsandbox_compose.py`, `tests/unit/environments/sbx/test_compose.py`, `tests/unit/environments/sbx/test_local.py`, `tests/unit/test_langsmith_environment.py` | Assert argv contains names rather than values and the separate environment transport preserves values. |

## Collect hook budget

Original Harbor gives every `[[verifier.collect]]` hook its own timeout,
`timeout_sec`, which defaults to 60 seconds. The hooks run one after another,
and a hook that reaches its limit is killed and logged as a warning. Its
evidence is then missing, so the verifier sees an empty or absent receipt. A
probe that drives real services in a sidecar takes as long as those services
take. When several trials share a host, the same probe can go from about 20 s to
over a minute, so a limit the task never chose cuts it off. Raising the limit in
each task only adds more independent deadlines, one per hook, on top of the
verifier's own timeout.

Here an unset hook `timeout_sec` means the hooks of one collection share a
single budget: the verifier timeout that the trial resolves for that step, after
overrides, the maximum and the multiplier. Each hook gets whatever is left of
that budget. A hook's own `timeout_sec` can still shorten its share, but never
extend it. Once the budget is spent, any remaining hooks are skipped with a
warning. Changing the default is the only way to get one backstop. The deadline
lives in the trial, which is the only place that knows the verifier timeout and
runs the hooks. A task can set a value per hook, but it cannot make the hooks
share one.

Tradeoff: a task that relied on the 60-second default now waits up to the
verifier timeout for a stuck hook, and collection plus verification can take up
to twice the verifier timeout. A hook set to 60 seconds explicitly behaves as
before.

The upstream files changed for this behavior are:

| Files | Change |
|---|---|
| `src/harbor/models/task/config.py` | `VerifierCollectConfig.timeout_sec` defaults to `None`. |
| `src/harbor/trial/trial.py` | `_collect_artifacts_phased` sets one deadline from the step's verifier timeout, and `_run_collect_hooks` gives each hook what remains. |
| `docs-mintlify/jobs/artifact-collection.mdx` | Describes the shared budget. |
| `tests/unit/test_trial_verifier_artifact_transfer.py`, `tests/unit/models/test_artifact_validation.py`, `tests/unit/test_single_step_trial.py` | Cover the shared budget, an explicit shorter share and a spent budget, and update the old 60-second expectations. |

## New files

| File | What |
|---|---|
| `src/harbor/environments/desktop_exec.py`, `tests/unit/environments/test_desktop_exec.py` | Private staged exec scripts shared by the desktop environments, keeping environment values and sudo credentials out of shell arguments, with regressions for shell startup and cleanup. |
| `src/harbor/trial/seats.py` | `SeatsTrial`, a `SingleStepTrial` with one agent per seat. It calls Harbor's trial methods where they take what a seat needs, and mirrors, per seat, the ones that hard-code the single agent (`_init_agent`, `_prepare`, `_setup_agent`, `_run_agent_phase`, `_download_role_logs`, `_sync_agent_output`). |
| `src/harbor/environments/seat.py` | `SeatEnvironment`: one Compose service of the trial's project as a `BaseEnvironment`, over the per-service operations. Ordinary execs pass no `-u`, so Compose's `user:` applies in full; `default_user` holds that uid for agents' ownership fixes only. `kill_exec_processes` kills, as root, every process in the service that an exec started (its parent is outside the container), with its descendants, for an agent the trial has stopped; the service's own processes under PID 1 keep running. |
| `docs-mintlify/tasks/multi-agent.mdx` | The task format, participants, how a run starts and ends, logs, the verifier, and the limitations. |
| `examples/tasks/hello-seats/`, `examples/configs/features/seats-trial.yaml`, `examples/configs/features/seats-job.yaml` | A runnable two-agent example task and the trial and job configs that run it. |
| `tests/unit/models/test_task_config_seats.py`, `tests/unit/models/test_trial_participants.py`, `tests/unit/environments/test_seat.py`, `tests/unit/environments/test_dind_service_upload.py`, `tests/unit/trial/test_seats.py` | Unit tests; none needs Docker. |
| `tests/runtime/test_hello_seats_docker.py` | Runs hello-seats with the oracle in both seats on real Docker. |
