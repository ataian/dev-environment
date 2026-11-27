# dev-environment coordination

- Approved plan: [.hermes/plans/PLAN_0.1.0_Dev-Environment_v2.md](.hermes/plans/PLAN_0.1.0_Dev-Environment_v2.md), SHA-256 `9a80776cfeca41c2a879ca66a42ea29f564cebd230920a6ed3078e687bd02555`. Approval: “gut, umsetzung bitte starten.”
- Board: `dev-environment`. Order: Task 1 → Task 2. Implementer: Anjin via Codex. Reviewer: Fujiko. Coordinator: Mariko, originating Desktop session.
- Baseline: new local repository, branch `dev-environment`, no commits. Plan and spec bytes are frozen. Use exact before/after artifact manifests and diffs for review; no commit, push, live installation, profile change or Hindsight write is authorized.
- Preflight: Task 1 defines launcher, configuration and contract tests; Task 2 consumes them in the standalone install and adds runtime acceptance. The shared files are `references/toolchain.md` and `tests/dev_environment_smoke.py`. Task 2 starts after Task 1 review approval.
