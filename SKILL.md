---
name: dev-environment
description: Install standalone containerized build, check, and test commands for a project checkout.
version: 0.1.0
platforms: [linux]
---

# Dev environment

1. First establish the operator's project root. If missing, ask for its path and wait. Use that root before inspecting checkouts or writing files.
2. Follow [installation](references/install.md) for discovery, decisions, rendering, verification and optional Hermes grants. Use the [toolchain contract](references/toolchain.md) for generated files and command mappings.
3. Report the install path, exact launcher commands, unsupported workflows, verification results and any pending authorization or prerequisite.

Resolve package paths relative to this skill. Installation uses this package and the selected project's tooling. Host prerequisites and Docker access are in the toolchain contract.
