# dev-environment Implementation Plan

> **For workers:** Review draft. Execute only after Max approves this plan. Anjin implements via Codex; Fujiko reviews the exact candidate through the native Kanban review transition. This plan does not authorize live installation, profile changes, publication, or deployment.

**Goal:** Give developers and coding agents one installable Hermes skill that configures containerized native build, check, and test commands for their working checkout.

**Architecture:** The skill discovers the project and writes project-specific configuration. An unchanged launcher and bundled helpers run that configuration via Docker Compose. The tool image and writable cache persist across disposable command containers; image refresh is a separate operation.

**Tech stack:** Hermes skill Markdown, Bash, Python 3 standard library on the host for JSON/argv handling, Docker Engine and Compose v2. Project toolchains, including Java or Node when needed, run in the tool image.

**Spec:** [SPEC_v3.md](../../docs/SPEC_v3.md), SHA-256 `c31ae80339b298a7da8b14181b193e12e15286239ea6477e26c9051c047f253c`. Max confirmed it as the implementation baseline. [Fujiko’s spec review](../../../docs/REVIEW_v3_Skill-Specs-und-Handoff_v2.md) approved this exact version; E6 is covered in Task 2. This is the first standalone release, proposed package version `0.1.0`; document version `v1`.

## Scope and source

- Max: “ich möchte dass du dir in /home/mariko/projects/demo_and_dev/dev-environment/docs/SPEC_v3.md durchliest. es geht nun um die implementierung dieses skills.” Deliver the standalone package in that project directory, not an installation into MTB or an edit to its working tree.
- Use tracked MTB reference files at `/home/mariko/projects/mtb/de.uke.iam.mtb`, commit `e599bfac9d1216a8ee93f4bfc6aa5ddd9395b7d9`. Read them via `git -C /home/mariko/projects/mtb/de.uke.iam.mtb show <revision>:<path>` with the revision above. Record provenance for reused content. The reviewed source is a reference, not a target or runtime dependency.
- `docker/dev/dev.sh:35–37,129–169,186–227` owns path resolution, image selection, Compose dispatch and optional helper calls. `docker/dev/compose.yaml` defines the source/cache mounts and numeric user. `Dockerfile.dockerignore` excludes source from the build context.
- Trace `docker/dev/unit.sh`, `profile.sh` and their called resources, plus `docker/isolated/docker-local.sh` and `runtime.sh` where selected. The unit wrapper currently interprets Maven logs; the profile workflow uses `profile*.{sh,jq,mjs,properties}`, staged fixtures, specific images and controlled socket access. `runtime.sh` derives its checkout and tool image from the old layout, stages pinned inputs and invokes `javac`/`jfr`. Do not carry those assumptions over to ordinary commands.
- The old Maker-owned skill is a discovery/documentation reference only. No dependency on Maker, `disposable-demo`, another installed skill, a sibling `isolated/` tree, or `/workspace/docker/dev`. No file, rule, or role migration in Maker. No full application suite, new framework suite, or exhaustive suite run as an installation gate.
- Builds use a trusted, local Docker target with matching bind-mount paths. Ordinary tools run without the Docker socket, privileged mode, or published ports. The tool container is not an agent sandbox. Use only synthetic data and project-scoped resources for verification. Any image installation on a real host or change to a real profile requires the applicable separate authorization; request it only if an isolated fixture cannot cover the required check.

## Package and install interfaces

| Path relative to project | Responsibility |
|---|---|
| `SKILL.md` | Hermes discovery and installation entrypoint; ask for the project root first. |
| `references/install.md` | Project discovery, rendering, optional prerequisites, grants and readback. |
| `references/toolchain.md` | `dev.json` contract, Dockerfile/Compose examples, MTB helper adaptation and native command mapping. |
| `scripts/dev.sh`, `scripts/dev-runner.py` | Stable launcher and runtime. Copy as `dev/dev.sh` and `dev/helpers/dev-runner.py`; no byte substitutions. |
| `scripts/run-in-tools.sh` | In-container helper copied to `dev/helpers/run-in-tools.sh`; executes the given argv. |
| `tests/dev_environment_smoke.py` | Package verification modes (`--contract`, `--docker`, `--update`); never part of a target install. |

Generated install: `<project>/dev/{dev.sh,dev.json,Dockerfile,Dockerfile.dockerignore,compose.yaml,AGENTS.md,helpers/}`. Keep mutable cache and tool state separate from the installed helpers and generated definitions. Add a relative pointer to the root `AGENTS.md` of each selected checkout. The standalone distribution includes `SKILL.md`, `references/`, and `scripts/`; do not copy planning or test files into target installations.

`dev.json` is data, not a shell program. Required keys: `checkouts` (map of logical names to canonical absolute host paths and absolute container mounts), `commands` (map of supported verbs to ordered steps), `cache_dir` (absolute project-local path outside checkout mounts), `services` (allowlist of development service names, possibly empty). Each command step has `checkout`, `workdir` (relative to that checkout), and `argv` (string array). A `test-selected` step may use one whole-argument `{selector}` or one literal prefix with the token at the end, e.g. `-Dtest={selector}`; substitute once and pass unchanged as one argv element. Never evaluate a selector or an argv array as shell text. Use ordered steps for project-native prerequisites, not shell concatenation. Baseline verbs: `help`, `build`, `check`, `test`, `update`; `test-selected` only if the native tool supports selection. Add `start` only when the install declares development services. Document other commands only when actually configured and verified.

Example mapping for the synthetic layout (the installer writes this machine's actual paths):

```json
{
  "checkouts": {"app": {"host_path": "/fixture/sample-project/sources/amber-engine", "mount": "/workspace"}},
  "commands": {
    "build": [{"checkout": "app", "workdir": ".", "argv": ["make", "build"]}],
    "check": [{"checkout": "app", "workdir": ".", "argv": ["make", "check"]}],
    "test": [{"checkout": "app", "workdir": ".", "argv": ["python3", "-m", "unittest"]}],
    "test-selected": [{"checkout": "app", "workdir": ".", "argv": ["python3", "-m", "unittest", "{selector}"]}]
  },
  "cache_dir": "/fixture/sample-project/.local/dev-cache",
  "services": []
}
```

`--contract` creates its own stub fixture. Exercise the empty-service case and a named service alongside an unrelated service: `start` must only request configured services; ordinary commands must request none. `--docker` and `--update` take `--install` with the absolute path to an existing synthetic `dev/` install. The latter modes fail if Docker is unavailable; they never substitute the stub.

## Review focus

1. Invocation from outside the project, including paths with spaces and nested checkouts: always select the configured root and working directory. Task 1.
2. Selector `Class#method` survives mapping as one argument; a failing native check is visible to the caller. Task 1.
3. A reinstall, missing prerequisite, or conflicting guide leaves unrelated checkout instructions and profile grants intact. Task 2.
4. Normal runs do not pull or rebuild a present image; a failed refresh preserves the previous runnable image and developer state. Task 1.
5. Codex starts in the checkout root and reaches the sibling guide through the root pointer. Profile allowlist readback alone does not establish command behavior. Task 2.

## Task 1: Run native tools from a separate installation directory

**Files:** `scripts/dev.sh`, `scripts/dev-runner.py`, `scripts/run-in-tools.sh`, `references/toolchain.md`, `tests/dev_environment_smoke.py`.

**Story:** As a developer, I can edit a checkout and run the correct native command without installing the project toolchain on the host or rebuilding it on every run.

- [ ] **Write the failing checks first.** In `--contract`, use a temporary install and a recording Docker stub to assert cwd-independent dispatch, a path with spaces, a second checkout with a distinct mount and workdir, and exact forwarding of `Class#method` as `-Dtest=Class#method`. Stub a native exit of `23` and require `23` from the launcher. Assert unsupported verbs, invalid config paths and missing mounts cause no Docker run. Record image inspection, build and tag arguments separately. In `--docker`, use a synthetic non-MTB nested checkout as specified in Task 2. In `--update`, exercise success and failed candidate build with separate fixtures or controlled failure injection; run the prior image again after failure.
- [ ] **Establish RED.** Run `python3 tests/dev_environment_smoke.py --contract`; it must fail at the absent launcher or the first missing behavior, not on fixture setup. Keep the original failure and exit status with the task evidence.
- [ ] **Adapt dispatch and mounts.** Resolve `<project>/dev` once from `${BASH_SOURCE[0]}` and pass it to the adjacent runner. The only host helper path is relative to that install. Use the local Docker preflight pattern from `docker-local.sh` without importing MTB-only commands. Validate the config, canonical checkout mounts, directory existence, selector count and command shapes before use. Reject a workdir that resolves outside the selected checkout. Compose uses an explicit file, project identity and `--env-file /dev/null`. Bind one checkout read/write at `/workspace`; with multiple checkouts, use distinct configured paths and select `-w` per step. Bind `dev/helpers/` read-only at `/opt/project-dev` and the cache read/write at `/opt/project-cache`; use the calling UID:GID and a writable cache home. Use long bind syntax with host-path creation disabled. Preserve default no-deps operation. A representative command is:

  ```text
  docker compose --env-file /dev/null -p <install-project-id> -f <install-compose> run --rm --no-deps --pull never -T -w /workspace tools /bin/sh /opt/project-dev/run-in-tools.sh python3 -m unittest test_probe.Probe.test_pass
  ```

  The helper body after its shebang is `set -eu` followed by `exec "$@"`. Propagate the native process result; sequence multiple steps explicitly. Use isolated path-qualified service names under the same Compose project. `start` may start only configured dev services and their declared, approved dependencies. No implicit service start during ordinary commands.
- [ ] **Implement image reuse and refresh.** Namespace the tools image by the canonical install identity and version its active tag by the `Dockerfile` hash. Keep the Docker build context minimal and exclude checkout files, secrets, dev state and caches. Ordinary runs inspect the tag and build only if absent. Serialize missing-image builds and `update` for the same install, and select a stable image ID for a command once acquired. `update` builds under a unique candidate tag using Docker build with `--pull --no-cache`; tag that built image as active only after success. If the pull/build fails, return nonzero, report the failed stage and leave the prior active image usable. Do not run service startup, prune, reset caches or edit any application manifest/lockfile in the update path.
- [ ] **Verify GREEN.** Run `bash -n scripts/dev.sh`, `sh -n scripts/run-in-tools.sh`, and `python3 tests/dev_environment_smoke.py --contract`; expect exit `0`. Docker-mode checks remain distinct from stub results. If a real app needs a helper beyond the shipped generic wrapper, qualify it with Task 2; no unconditional MTB suite or optional helper imports in this runner.

## Task 2: Install the package and prove the developer workflow

**Depends on:** Task 1. **Files:** `SKILL.md`, `references/install.md`, `references/toolchain.md`, `tests/dev_environment_smoke.py` (standalone install and acceptance scenario). Update `SKILL.md` version once to `0.1.0`.

**Story:** As an operator, I can point Hermes at a project root and hand a developer a working `dev/` install with exact commands that Codex can find from the checkout.

- [ ] **Establish the skill baseline.** Before writing `SKILL.md`, run a fresh-context install scenario without it: the project root has a differently named nested checkout, unrelated root `AGENTS.md` text, and optional E2E absent. Ask for standalone dev setup without supplying a root; then supply the root. Record what is missed without the new skill and use the same scenario to verify the new skill. A successful baseline on any criterion is not a failure; target the actual gaps. Test the installer workflow, not merely keyword presence.
- [ ] **Write the skill installation procedure.** Ask for the project root before discovery or writes. Inspect actual nested checkouts and module roots, guides, manifests, lockfiles, CI, existing helpers and commands. Resolve ambiguities about which checkout or dev service is wanted before writing. Generate the native mappings, Compose and Dockerfile from observed project requirements; install the finished launcher byte-for-byte, with every required helper beside it. Use minimal package-relative references rather than another installed skill. Preserve existing files if not owned by this installer; stop on conflicting local adaptations instead of overwriting them. Verify the installed script by `cmp` and validate Compose config before the first run.
- [ ] **Handle applicable optional commands.** Trace the selected helper’s called resources end to end. Adapt any MTB-style unit or profile helper that a target actually needs; place host resources under `dev/helpers/` and container resources at `/opt/project-dev`, including nested calls. Keep project-only images, data, host `jq`/`timeout`, profile behavior, and service prerequisites scoped to the selected command. If E2E is needed, bundle and adapt its isolated runtime helper and required inputs; use the declared checkout and selected tools image, not an MTB path or independently derived tag. Move Java tooling into an appropriate image and document any remaining host prerequisites. Preserve selected isolation and scoped cleanup checks; do not enable privileged or socket workflows by default. Resolve prerequisites before launch, list them in `dev/AGENTS.md`, and explicitly mark unselected `profile`/E2E workflows unsupported. A project without such requirements must not install or execute MTB-specific code.
- [ ] **Make discovery and grants verifiable.** Write one short `dev/AGENTS.md` with exact supported commands, selector examples, relevant prerequisites, and working directory. Append or update only the owned pointer in each checkout-root guide, e.g. `For containerized builds, checks and tests, read [the dev guide](../../dev/AGENTS.md).` Compute the link from the actual root, retain unrelated text, and apply idempotently. Discover the authorized target Hermes profile and confirm the native approval mechanism on the installed version. Where using `command_allowlist`, merge the project's exact launcher-and-verb invocations (or the native narrowly scoped selector pattern) into that profile’s list; do not replace it wholesale or add generic Docker/shell grants. Read back the list and test supported documented commands in an isolated fixture profile. A broad matching rule, mismatched rendering or a profile that cannot be verified is not a completed grant install. Respect current operator authority and native approval policy.
- [ ] **Exercise the actual install.** Use a synthetic tree `sample-project/sources/amber-engine/`, a normal Git root with a pre-existing `AGENTS.md`, and native Python checks selected from a `Makefile`, CI and `test_probe.py`. In an isolated Hermes context, make only the standalone package available, drive the skill to install into `sample-project/dev/`, and run the acceptance script against that actual install; the script must not generate or repair the skill’s output. `--docker` verifies first build, the packaged helper running at `/opt/project-dev`, a pre-existing test followed by a newly created untracked file visible without staging, a native `python3 -m unittest test_probe.Probe.test_pass` selector, a deliberate native check failure and then recovery, image-ID reuse, a cache marker surviving successive containers, disposable container exit, and matching output UID:GID via `stat -c '%u:%g'`. Record the install and another caller cwd. Run without a Maker or demo install, no project host Java/Node, and no installed checkout `docker/dev/` directory. Keep test cleanup limited to the fixture resources.
- [ ] **Verify Codex and refresh independently.** Start a fresh native Codex session from `sources/amber-engine` with no guide path in the prompt; ask it to read the project instructions and report the exact supported check invocation. Verify it follows the root pointer to the sibling guide. Run `python3 tests/dev_environment_smoke.py --update --install "$FIXTURE/dev"` separately, where `FIXTURE` is the absolute `sample-project` path recorded by the installer run. Verify from the Docker invocation evidence that it rebuilds and pulls bases even with the same Dockerfile hash; a successful rebuild may reproduce the same image ID. In a controlled synthetic failure, verify a nonzero update, unchanged active image ID, a subsequent ordinary run on that image, and unchanged cache marker, application manifests/lockfiles, and any fixture service data. If real Docker, Codex, or native grants cannot run under authorized conditions, report which proof is blocked; do not substitute a stub for that proof.
- [ ] **Hand off the exact candidate.** Report the verified package identity and fixture results, including the supported optional commands or their absence. Run `git diff --check` if a repository has been set up and verify the actual package file set, frontmatter name, relative links, and copied script/helper bytes. Fujiko independently checks the diff/artifact, failure paths and fixture evidence against the SPEC and E6. No required full application test suite.

## Execution handoff

- Workspace: `/home/mariko/projects/demo_and_dev/dev-environment/`. At planning time it is not a Git checkout. Decide how to establish a reviewable local baseline before dispatch; never initialize Git in a sibling, or infer a commit/push authorization from plan approval. Verify the baseline and exact candidate revision or artifact manifest before review.
- Dispatch Tasks 1 → 2 serially on the native Anjin/Codex → Fujiko pipeline after approval. After the worker verifies its candidate, use `kanban_request_review(summary, reviewer="fujiko")`. A return for changes goes to Anjin through the native review path; review approval applies only to the named candidate. No fallback coding agent. No live profile changes, Maker rollout, publication or production actions.
- Use `superpowers:test-driven-development` for code and `superpowers:writing-skills` for the skill text in the implementation context; retain the named executor and reviewer rather than substituting generic subagents. Keep results with the tasks. Distinguish static review, stub execution, real Docker execution, Codex discovery and grant verification.
