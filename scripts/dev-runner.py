#!/usr/bin/env python3
"""Dispatch a trusted dev installation to its local Docker Engine."""

import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4


DOCKER = ["docker", "--context", "default"]
SOCKET = "unix:///var/run/docker.sock"
HELPERS = PurePosixPath("/opt/project-dev")
CACHE = PurePosixPath("/opt/project-cache")
SELECTOR = "{selector}"
BUILTINS = {"help", "start", "update"}
BASE_COMMANDS = {"build", "check", "test"}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def valid_string(value):
    return isinstance(value, str) and "\0" not in value and not re.search(r"[\ud800-\udfff]", value)


def valid_name(value):
    return valid_string(value) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value)


def directory(value, label):
    require(valid_string(value), f"{label} must be a canonical absolute directory")
    path = Path(value)
    require(path.is_absolute() and str(path.resolve(strict=True)) == value and path.is_dir(),
            f"{label} must be a canonical absolute directory: {value}")
    return path


def overlaps(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def installed_file(install, relative):
    path = install / relative
    require(path.is_file() and path.resolve() == path, f"expected regular installed file: {path}")
    return path


def read_config(install):
    config = json.loads(installed_file(install, "dev.json").read_text(encoding="utf-8"))
    require(isinstance(config, dict), "dev.json must be an object")
    checkouts = config.get("checkouts")
    require(isinstance(checkouts, dict) and checkouts, "checkouts must be a nonempty object")
    helpers = directory(str(install / "helpers"), "helpers")
    cache = directory(config.get("cache_dir"), "cache_dir")
    require(os.access(cache, os.W_OK | os.X_OK), f"cache_dir is not writable: {cache}")
    require(cache != install.parent and cache.is_relative_to(install.parent)
            and not overlaps(cache, install), "cache_dir must be project-local and outside dev/")
    mounts = {HELPERS: (helpers, True), CACHE: (cache, False)}
    checkout_paths = {}
    for name, item in checkouts.items():
        require(valid_name(name) and isinstance(item, dict), f"invalid checkout: {name!r}")
        host = directory(item.get("host_path"), f"checkout {name} host_path")
        value = item.get("mount")
        require(valid_string(value) and value.startswith("/") and not value.startswith("//"),
                f"checkout {name} mount must be absolute")
        mount = PurePosixPath(value)
        require(mount != PurePosixPath("/") and str(mount) == value and ".." not in mount.parts,
                f"checkout {name} mount must be canonical: {value}")
        require(not any(overlaps(mount, target) for target in mounts),
                f"overlapping container mount: {value}")
        require(not overlaps(host, install) and not overlaps(host, cache),
                f"overlapping host mount: {host}")
        mounts[mount] = (host, False)
        checkout_paths[name] = (host, mount)
    if len(checkouts) == 1:
        require(PurePosixPath("/workspace") in mounts, "a single checkout must mount at /workspace")

    services = config.get("services")
    require(isinstance(services, list) and all(valid_name(name) and name != "tools" for name in services)
            and len(set(services)) == len(services), "services must list distinct development service names")
    commands = config.get("commands")
    require(isinstance(commands, dict) and BASE_COMMANDS <= commands.keys(),
            "commands must define build, check and test")
    dispatch = {}
    for verb, steps in commands.items():
        require(valid_name(verb) and verb not in BUILTINS and isinstance(steps, list) and steps,
                f"invalid command mapping: {verb!r}")
        dispatch[verb] = []
        selector_count = 0
        for index, step in enumerate(steps, 1):
            label = f"commands.{verb} step {index}"
            require(isinstance(step, dict), f"{label} must be an object")
            name, relative, argv = (step.get(key) for key in ("checkout", "workdir", "argv"))
            require(isinstance(name, str) and name in checkout_paths, f"{label}: unknown checkout")
            host, mount = checkout_paths[name]
            require(valid_string(relative) and relative and not Path(relative).is_absolute(),
                    f"{label}: workdir must be relative")
            workdir = (host / relative).resolve(strict=True)
            require(workdir.is_dir() and workdir.is_relative_to(host),
                    f"{label}: workdir must be a directory inside {host}")
            require(isinstance(argv, list) and argv and all(valid_string(arg) for arg in argv) and argv[0],
                    f"{label}: argv must be a string array with a nonempty executable")
            for arg in argv:
                count = arg.count(SELECTOR)
                require(not count or count == 1 and arg.endswith(SELECTOR),
                        f"{label}: {SELECTOR} must occur once at the end of an argument")
                selector_count += count
            dispatch[verb].append((str(mount / workdir.relative_to(host)), argv))
        require(selector_count == (verb == "test-selected"),
                f"{verb}: expected {int(verb == 'test-selected')} {SELECTOR} token(s)")
    return dispatch, services, mounts


def output(argv, **kwargs):
    return subprocess.check_output(argv, text=True, **kwargs).strip()


def preflight():
    overrides = {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
                 "DOCKER_TLS", "DOCKER_TLS_VERIFY", "BUILDX_BUILDER", "BUILDKIT_HOST"}
    require(not (present := sorted(overrides & os.environ.keys())),
            f"unset Docker target overrides: {', '.join(present)}")
    require(output(["docker", "context", "show"]) == "default", "expected Docker context default")
    endpoint = output(DOCKER + ["context", "inspect", "default", "--format", "{{.Endpoints.docker.Host}}"])
    require(endpoint == SOCKET, f"expected Docker endpoint {SOCKET}, got {endpoint!r}")
    require(output(DOCKER + ["info", "--format", "{{.Name}}"]), "Docker daemon reported an empty name")


def validate_compose(compose, env, mounts, start):
    config = json.loads(output(compose + ["config", "--format", "json"], env=env))
    services = config["services"]
    tools = services.get("tools", {})
    require(tools.get("image") == env["DEV_TOOLS_IMAGE"] and tools.get("user") == env["DEV_TOOLS_USER"],
            "Compose tools must use DEV_TOOLS_IMAGE and DEV_TOOLS_USER")
    require(tools.get("environment", {}).get("HOME") == str(CACHE), f"Compose tools HOME must be {CACHE}")
    unsafe = ("build", "container_name", "entrypoint", "privileged", "ports", "volumes_from", "use_api_socket",
              "devices", "device_cgroup_rules", "cap_add", "network_mode", "ipc", "pid", "links")
    require(not any(tools.get(key) for key in unsafe), "Compose tools contains unsupported runtime options")
    volumes = tools.get("volumes", [])
    require(len(volumes) == len(mounts), "Compose tools must bind exactly the configured directories")
    expected = {str(target): (str(source), read_only) for target, (source, read_only) in mounts.items()}
    for volume in volumes:
        target = volume.get("target")
        require(volume.get("type") == "bind" and volume.get("bind", {}).get("create_host_path", False) is False
                and expected.pop(target, None) == (volume.get("source"), volume.get("read_only", False)),
                f"Compose tools bind differs from dev.json or enables host-path creation: {target}")
    if start:
        pending = list(start)
        seen = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            require(name != "tools" and name in services, f"invalid development service dependency: {name}")
            service = services[name]
            require(not service.get("container_name"), f"{name}: use the Compose project-scoped container name")
            deps = service.get("depends_on", {})
            for field in ("network_mode", "ipc", "pid"):
                mode = service.get(field, "")
                require(not mode.startswith("container:"), f"{name}: {field} references an external container")
                if mode.startswith("service:"):
                    target = mode.removeprefix("service:")
                    require(target in deps, f"{name}: declare {target} in depends_on")
            for field in ("links", "volumes_from"):
                for entry in service.get(field, []):
                    require(entry.split(":")[0] in deps, f"{name}: declare {entry} in depends_on")
            pending.extend(deps)
            seen.add(name)


def inspect_image(ref, *, missing_ok=False):
    args = DOCKER + ["image", "inspect", "--format", "{{.Id}}", ref]
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode == 0:
        image = result.stdout.strip()
        require(IMAGE_ID.fullmatch(image), f"invalid Docker image ID for {ref}: {image!r}")
        return image
    missing = {f"No such image: {ref}", f"Error response from daemon: No such image: {ref}",
               f"Error: No such image: {ref}"}
    if missing_ok and result.returncode == 1 and result.stderr.strip() in missing:
        inventory = output(DOCKER + ["image", "ls", "--filter", f"reference={ref}",
                                     "--format", "{{.Repository}}:{{.Tag}}"])
        if not inventory:
            return None
    raise Invalid(f"image inspection failed for {ref}: {result.stderr.strip()}")


def build_image(dockerfile, tag, refresh, lock):
    builder = output(DOCKER + ["buildx", "inspect", "default"])
    sections = re.split(r"^Nodes:[ \t]*$", builder, flags=re.M)
    # Buildx indents nested metadata, including device names.
    fields = re.compile(r"^(Name|Driver|Endpoint):[ \t]*(.*?)[ \t]*$", re.M)
    require(len(sections) == 2
            and fields.findall(sections[0]) == [("Name", "default"), ("Driver", "docker")]
            and fields.findall(sections[1]) in (
                [("Name", "default"), ("Endpoint", "default")],
                [("Name", "default"), ("Endpoint", SOCKET)],
            ),
            "expected the default local docker-driver builder")
    with TemporaryDirectory(prefix="dev-tools-build-") as temp:
        context = Path(temp)
        (context / "Dockerfile").write_bytes(dockerfile)
        (context / "Dockerfile.dockerignore").write_text("**\n")
        args = DOCKER + ["build", "--builder", "default", "--tag", tag]
        if refresh:
            args += ["--pull", "--no-cache"]
        subprocess.run(args + [str(context)], check=True, pass_fds=(lock,))
    return inspect_image(tag)


def acquire_image(install, project, refresh):
    # Lock the canonical directory inode, independent of checkout and cache configuration.
    lock = os.open(install, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        dockerfile = installed_file(install, "Dockerfile").read_bytes()
        active = f"{project}-tools:{hashlib.sha256(dockerfile).hexdigest()}"
        image = inspect_image(active, missing_ok=True)
        if refresh:
            candidate = f"{project}-tools:candidate-{uuid4().hex}"
            image = build_image(dockerfile, candidate, True, lock)
            subprocess.run(DOCKER + ["image", "tag", image, active], check=True, pass_fds=(lock,))
        elif image is None:
            image = build_image(dockerfile, active, False, lock)
        return image
    finally:
        os.close(lock)


def main(install, args):
    dispatch, services, mounts = read_config(install)
    verbs = ["help", *dispatch, "update"]
    if services:
        verbs.append("start")
    verb, *operands = args or ["help"]
    require(verb in verbs, f"unsupported command: {verb}")
    require(len(operands) == (verb == "test-selected") and all(operands),
            f"{verb} expects {'one nonempty selector' if verb == 'test-selected' else 'no arguments'}")
    if verb == "help":
        print("Usage: dev.sh {" + "|".join("test-selected SELECTOR" if item == "test-selected" else item
                                           for item in verbs) + "}")
        return 0

    for path in ("Dockerfile", "compose.yaml", "helpers/run-in-tools.sh"):
        installed_file(install, path)
    preflight()
    project = "dev-" + hashlib.sha256(os.fsencode(install)).hexdigest()[:24]
    compose = DOCKER + ["compose", "--env-file", "/dev/null", "-p", project, "-f", str(install / "compose.yaml")]
    env = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    # Compose config interpolates tools even when only a development service is requested.
    env.update(DEV_TOOLS_IMAGE=f"{project}-tools:config-only", DEV_TOOLS_USER=f"{os.getuid()}:{os.getgid()}")
    validate_compose(compose, env, mounts, services if verb == "start" else [])
    if verb == "start":
        return subprocess.call(compose + ["up", "-d", "--no-build", "--", *services], env=env)
    image = acquire_image(install, project, verb == "update")
    if verb == "update":
        print(f"Active tools image: {image}")
        return 0
    env["DEV_TOOLS_IMAGE"] = image
    for workdir, argv in dispatch[verb]:
        if operands:
            argv = [arg.replace(SELECTOR, operands[0]) for arg in argv]
            require(argv[0], "selector produced an empty executable")
        status = subprocess.call(compose + ["run", "--rm", "--no-deps", "--pull", "never", "-T",
                                            "-w", workdir, "tools", "/bin/sh",
                                            str(HELPERS / "run-in-tools.sh"), *argv], env=env)
        if status:
            return status if status > 0 else 128 - status
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(Path(sys.argv[1]).resolve(strict=True), sys.argv[2:]))
    except subprocess.CalledProcessError as error:
        print(f"dev.sh: {error}", file=sys.stderr)
        sys.exit(error.returncode if error.returncode > 0 else 128 - error.returncode)
    except (ValueError, OSError, KeyError, RuntimeError, TypeError, AttributeError) as error:
        print(f"dev.sh: {error}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
