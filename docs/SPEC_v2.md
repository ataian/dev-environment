# dev-environment — specification v2

Revision: standalone toolchain contract. Separate observed MTB behavior from additions; downstream role assignment and workflow are not part of this package.

## MTB baseline

- `docker/dev/dev.sh:4–32,129–175`: `build` builds the application, not a long-lived environment. The script identifies the tools image by Dockerfile hash and builds it if missing. Ordinary build/check commands use `docker compose run --rm --no-deps --pull never`. `test-selected` accepts a class or `Class#method`; specialized suites use the supplied helpers.
- `docker/dev/compose.yaml:1–22`: the reference Compose file defines `tools`, runs it as the invoking UID/GID, and mounts the actual checkout and a separate cache directory. Source edits and untracked files are visible without staging or rebuilding. The current command list has no `update` or `start` verb.

## Install and operate

- Ship a valid Hermes `SKILL.md`, generalized MTB `dev.sh`, required helpers, Dockerfile/Compose references and targeted smoke checks. Copy `dev.sh` unchanged to `<project>/dev/`. No Maker or demo skill dependency.
- Ask for the project root. Inspect nested source checkouts, instructions, manifests, lockfiles, CI, Dockerfiles and existing commands. Generate the matching Dockerfile and `compose.yaml` beside the script. Keep the working checkout mounted, generated files owned by the invoking user, and caches reusable. Preserve local inputs and data.
- Run builds, checks and tests with the native project commands in the tool container, not with a second Java/Node toolchain on the host. Preserve the MTB command semantics and applicable helpers. A project-specific test profile is not a universal requirement.
- Requested additions: start configured development services when needed; `dev.sh update` refreshes the specified base images and rebuilds the tool image within project version constraints. Preserve application dependencies, lockfiles and persistent dev data. Ordinary source changes reuse the existing tool image.
- Provide exact short operating commands in `<project>/dev/AGENTS.md`, discoverable from the working checkout, including by Codex. For a supplied target profile, set and verify project-scoped script grants so routine operation needs no further permission prompt. Preserve unrelated permissions.

## Extraction and package acceptance

Start from MTB `docker/dev/`: `dev.sh`, Dockerfile, Compose, guide, unit/profile helpers and called resources. Reuse the technical instructions from the existing `dev-environment` skill. Keep environment-specific operations in this package; consumers decide when to invoke them.

Anjin implements via Codex; Fujiko reviews the skill package. Use an isolated project root with no sibling skill. Demonstrate an edit and an untracked file are immediately visible, ordinary commands reuse the tools image/cache, selected commands receive their native selectors and generated outputs have the expected owner. Check update and configured-service startup separately from the MTB baseline. Verify guide discovery, scoped command execution and preservation of dependency files and persistent state. Reuse existing project commands as smoke probes; no new application framework tests or mandatory full-suite run at installation.
