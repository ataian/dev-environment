# Toolchain contract

Follow [installation](install.md) for discovery, ownership, copies, developer guidance and Hermes grants. The launcher uses its physical directory from any caller cwd.

- Host: Linux, Bash, Python 3.9+, Docker Engine, Buildx, Compose v2. Use context `default` on `unix:///var/run/docker.sock` with matching host and daemon bind paths. Unset Docker endpoint, TLS, client-config and builder overrides. Cold builds and refresh require the local `default` Docker-driver builder. The daemon must preserve the caller’s numeric UID:GID on bind mounts.
- Tool image: provide `/bin/sh` and project-native dependencies. Put Java, Node, and similar toolchains in the image when required. Keep sources, secrets and caches outside the build context. The launcher snapshots only Dockerfile bytes and generates a deny-all ignore rule in a private temporary context.
- Tools service: use exact checkout binds from `dev.json`, the read-only helper bind, and the writable cache bind and `HOME`. Use long binds with `create_host_path: false`. Do not add a Compose build, inherited entrypoint, Docker socket, privileged mode, or published ports. The tool container is not an agent sandbox.
- Services: `services` lists explicitly requested development services. Approve each transitive dependency through the generated Compose `depends_on` graph. Keep default project-scoped resource names and avoid fixed `container_name`. `start` targets only listed service roots and their approved dependencies. Use image-backed services because `start` disables builds. Ordinary commands start no dependencies.

## `dev.json`

| Field | Required value |
|---|---|
| `checkouts` | Nonempty map from logical names to `host_path` and `mount`. Host paths must be existing, canonical absolute directories, separate from `dev/` and cache. Single checkout mounts at `/workspace`. Multiple checkouts use separate, nonoverlapping absolute container paths outside the helper and cache mounts. |
| `commands` | Map from verbs to nonempty ordered step lists. Include `build`, `check`, and `test`. Reserve `help`, `update`, and `start` for the launcher. |
| Step | `checkout` names a declared checkout. `workdir` is relative to that checkout and MUST resolve to an existing directory inside it. `argv` is a string array with a nonempty executable. |
| `cache_dir` | Existing, writable, canonical absolute directory inside the project and separate from `dev/` and checkout mounts. |
| `services` | List of distinct development service names. An empty list disables `start`. |

Use actual native commands. Express prerequisites as ordered steps. The launcher stops at the first failure and returns its exit status. Define `test-selected` only when the native tool supports selection. Use exactly one `{selector}` token across its steps, either as an entire argument or at the end of a literal argument such as `-Dtest={selector}`. Supply one nonempty selector. The launcher substitutes it once and passes the resulting argument unchanged.

Example for `/fixture/sample-project/dev/`:

```json
{
  "checkouts": {
    "app": {"host_path": "/fixture/sample-project/sources/amber-engine", "mount": "/workspace"}
  },
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

Generate the Dockerfile from the project’s tool requirements. This example supports the Python fixture:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends make && rm -rf /var/lib/apt/lists/*
ENTRYPOINT []
```

Use `**` as the entire `Dockerfile.dockerignore` pattern list. Use discovered absolute paths when generating `compose.yaml`:

```yaml
services:
  tools:
    image: "${DEV_TOOLS_IMAGE:?Run dev.sh}"
    user: "${DEV_TOOLS_USER:?Run dev.sh}"
    entrypoint: []
    environment:
      HOME: /opt/project-cache
    volumes:
      - type: bind
        source: /fixture/sample-project/sources/amber-engine
        target: /workspace
        bind:
          create_host_path: false
      - type: bind
        source: /fixture/sample-project/dev/helpers
        target: /opt/project-dev
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: /fixture/sample-project/.local/dev-cache
        target: /opt/project-cache
        bind:
          create_host_path: false
```

## Image lifecycle

- Compose identity: `dev-<first 24 hex digits of SHA-256(canonical install path)>`. Active tag: `<identity>-tools:<SHA-256(Dockerfile bytes)>`.
- Ordinary commands inspect the active image and build only after confirming absence. The launcher acquires one immutable image ID for all steps and uses disposable `run --rm --no-deps --pull never` containers.
- `update` serializes with cold builds for the same install. It builds a unique candidate with `--pull --no-cache` and promotes the inspected candidate ID only after success. Failed builds leave the prior tag and project state untouched. Candidate tags from successful builds remain local along with earlier images.

## Pinned MTB provenance

Source: `/home/mariko/projects/mtb/de.uke.iam.mtb` at `e599bfac9d1216a8ee93f4bfc6aa5ddd9395b7d9`. Adapted patterns: `docker/dev/dev.sh` for path anchoring, Dockerfile-hash reuse and Compose argv dispatch, `docker/dev/compose.yaml` for UID:GID and source/cache binds, `docker/dev/Dockerfile.dockerignore` for deny-all context, and `docker/isolated/docker-local.sh` for local target checks.

| Traced workflow | Installation decision |
|---|---|
| `docker/dev/unit.sh` | Exclude the Maven Surefire log classifier and detached wait/cleanup path. Plain native commands return their own status. |
| `docker/dev/profile.sh` → `profile-prepare.sh`, `profile-inspect.jq`, `profile.mjs`, `profile.properties` | Include profile resources, images, scoped socket/cleanup and host `jq`/`timeout` only when a selected profile workflow requires them and the prerequisites are approved. |
| `docker/isolated/runtime.sh` → `docker-local.sh`, `inputs.sha256`, isolated Compose/Cypress definitions, jq audits and templates | For selected E2E, adapt helper paths to declared roots and the acquired tools image. Qualify required inputs, runtime dependencies, isolation and cleanup for that project. |

## Synthetic acceptance fixture

`--contract` uses a recording CLI. Real modes take an existing standalone install under `sample-project/dev/` with the example checkout layout and native selector mapping. Include the checkout pointer and the exact, shell-quoted absolute check invocation in backticks in the sibling guide. The smoke test observes the install.

- `--docker --install <absolute dev/>` expects a fresh install identity, an existing `test_probe.Probe.test_pass`, and a GNU Make `check` recipe that fails when `.dev-smoke-check-fail` exists at the checkout root (`make check` exits 2). The harness creates and removes its own untracked probe and verifies output ownership, cache reuse, selector dispatch and disposable exit.
- `--update --install <absolute dev/>` expects a populated active image and existing `dev-smoke-marker` in the cache. For service state evidence, place the synthetic marker at `<project>/.local/service-data`. The harness records real Docker argv and submits an invalid Dockerfile in a private candidate context for the failure case. It checks that the active ID, cache marker, Makefile and tracked fixture inputs remain usable or unchanged as applicable. Successful rebuilds may reproduce the same image ID.
