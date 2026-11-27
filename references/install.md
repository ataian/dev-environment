# Install dev-environment

## Discover and confirm

1. Establish the operator's project root before discovery or writes. Resolve it to a canonical absolute directory. Find actual nested Git roots and module directories within that scope. Inspect their guides, manifests, lockfiles, CI, Makefiles and existing development helpers. Derive native build, check, test, selector syntax, working directories and prerequisites from those sources.
2. Confirm ambiguous checkout, module and service choices before writing. The runner requires source mounts, `<project>/dev/` and the cache to be disjoint. If the supplied root is also the source checkout or the proposed paths overlap, stop and ask for a suitable enclosing root or layout. Do not change checkout boundaries or infer paths from a fixed directory depth.
3. Resolve optional decisions explicitly: native `test-selected` when supported, `start` for selected development services and approved dependencies, and any project-specific unit, `profile` or E2E workflow the operator needs. Use `services: []` when none are selected. Mark unselected `profile` and E2E unsupported. Confirm Docker probes and Hermes profile grants separately before making those changes.
4. Check host and project prerequisites for the selected scope using the [toolchain contract](toolchain.md). For each selected custom helper, trace all called scripts, data, images and service dependencies. Bundle adapted host resources under `dev/helpers/` and container resources at `/opt/project-dev`. Use the configured checkout and acquired tools image for any isolated runtime helper. Keep Java and similar tools in the image, document remaining host tools such as `jq` or `timeout`, and preserve applicable isolation and scoped cleanup checks. Obtain separate approval for any required socket or privileged workflow. Omit MTB-specific helpers and inputs when unused.

## Render, check and publish

1. Prepare the candidate before replacing any existing file. Stage updates away from their destinations. Render `dev.json`, `Dockerfile`, `Dockerfile.dockerignore` and `compose.yaml` from observed requirements. Prepare byte- and mode-preserving copies of `scripts/dev.sh` for `dev/dev.sh` and `scripts/{dev-runner.py,run-in-tools.sh}` for `dev/helpers/`. Distribute only the skill, its references and shipped scripts. Keep package tests and planning files outside the install.
2. Prepare a short `dev/AGENTS.md`. Record each selected checkout and native working directory, relevant prerequisites and exact supported invocations. Render the canonical absolute launcher path, verb and any concrete selector as single POSIX-shell command lines (for example with Python `shlex.join`), usable from the nested checkout. Stop if a command cannot be rendered safely on one line. Use Markdown backtick delimiters that preserve the exact commands. List `help`, `build`, `check`, `test`, `update` and only configured optional verbs. Label pending probes and unsupported workflows.
3. Preflight every destination, including parent directories, the cache and each checkout-root `AGENTS.md` using the pointer rule below. Refuse symlinks, unexpected file types, foreign files at target paths and modified prior installer files. An identical verified file is a no-op. Replace a changed installer file only when a trusted prior copy or digest proves the current bytes and the operator approves the replacement. Preserve unrelated files and cache contents. If a destination conflicts or a required prerequisite fails, stop before publication and report it. Recheck destination identity before writing. Publish the checked install files and create the project-local writable cache outside source and install mounts if absent. Use `cmp` on all three installed scripts and verify `help`. Before the first runtime probe, validate Compose with the approved local context and Docker/Compose overrides unset:

   ```bash
   python3 - "$install" <<'PY'
   import os
   import sys

   docker_overrides = {
       "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
       "DOCKER_TLS", "DOCKER_TLS_VERIFY", "BUILDX_BUILDER", "BUILDKIT_HOST",
   }
   env = {
       key: value for key, value in os.environ.items()
       if key not in docker_overrides and not key.startswith("COMPOSE_")
   }
   env["DEV_TOOLS_IMAGE"] = "dev-tools:config-only"
   env["DEV_TOOLS_USER"] = f"{os.getuid()}:{os.getgid()}"
   install = sys.argv[1]
   argv = [
       "docker", "--context", "default", "compose", "--env-file", "/dev/null",
       "-f", f"{install}/compose.yaml", "config", "--quiet",
   ]
   os.execvpe(argv[0], argv, env)
   PY
   ```

   Set `install` to the canonical `dev/` path. If current authorization covers rendering only, report Compose validation and runtime probes as pending.
4. In each selected checkout's root `AGENTS.md`, use one installer-owned block. Compute the Markdown link from that checkout root to the actual `dev/AGENTS.md` and verify it resolves there. For `sample-project/sources/amber-engine/`:

   ```markdown
   <!-- dev-environment: begin -->
   For containerized builds, checks and tests, read [the dev guide](../../dev/AGENTS.md).
   <!-- dev-environment: end -->
   ```

   Add the block if absent. Leave identical text untouched on reinstall. If the link changes, replace only a single block whose bytes match the previous verified installer block. Stop on malformed or duplicate markers, an unrecognized edit within the block, or a competing pointer. Preserve all bytes outside the block, including unrelated instructions. Recheck the original file before replacing it and verify exactly one resolving pointer after writing.
5. From the actual nested checkout, verify the exact documented check command with an authorized minimal native probe. Record exit status and update the guide's verification status. If validation or a probe is unavailable or fails, record the blocker and keep the command's runtime status pending or failed. Keep the full application suite outside the install gate.

## Hermes grants

1. Identify the operator-authorized target profile, its actual config path and installed Hermes version. Verify its native approval mechanism. Get operator approval for the profile and each exact rendered launcher invocation before writing. Include `test-selected` only for an explicitly approved concrete selector whose full command the installed matcher can represent exactly. Treat `update` and `start` as separate decisions when available.
2. Hermes 0.23.1 trims command text and entries before literal comparison. Entries containing `*`, `?` or `[` also use case-sensitive glob matching, even inside shell quotes. Quoting, relative paths and alternate renderings affect matches. Native shell-operator guards, hardline rules and operator denies still apply. Use the same safely quoted absolute invocation in the guide, terminal call and grant. Stop the grant step if the exact rendering is unsafe, uses glob metacharacters, or the version or approval is unavailable. Do not add wildcard selectors, generic Docker or shell grants, or disable approval gates.
3. Merge only the authorized exact strings into the current `command_allowlist`, preserving unrelated entries and settings. Stop on malformed config or a concurrent change that cannot be reconciled safely. Read back the effective profile list and verify the exact additions and preserved entries. In an isolated profile with normal guards enabled, check the exact documented command and a nearby ungranted rendering. Record the native matched rule and approval provenance when the command is flagged. An ordinary command may already pass without a finding and expose no approval provenance. Record that outcome separately from an observed allowlist decision. If authority, readback or behavior cannot be verified, report the grant step as incomplete.
