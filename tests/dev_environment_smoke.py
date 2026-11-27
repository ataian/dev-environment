#!/usr/bin/env python3
"""Exercise an installed launcher with a recording stub or a real synthetic fixture.

The fixture uses the plan's install paths and dev.json schema. Its generated
Compose file supplies static binds and resolves DEV_TOOLS_IMAGE (immutable
image ID) and DEV_TOOLS_USER (numeric UID:GID) from the launcher environment.
The plan leaves the names of these two interpolation variables open.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BASH = Path(shutil.which("bash") or "/bin/bash").resolve()
PROBES = {"context", "info", "version", "buildx", "inventory"}
BUILD_FAILURE = "synthetic daemon failure"


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def option(argv, *names, default=None):
    for index, arg in enumerate(argv):
        if arg in names:
            return argv[index + 1]
        name, separator, value = arg.partition("=")
        if separator and name in names:
            return value
    return default


def image_key(state, ref):
    return state / hashlib.sha256(ref.encode()).hexdigest()


def docker_stub():
    """Record process-boundary arguments and implement only the fixture's CLI."""
    argv = sys.argv[1:]
    args = argv[2:] if argv[:2] == ["--context", "default"] else argv
    state = Path(os.environ["STUB_STATE"])

    def record(kind, **evidence):
        line = json.dumps(dict(kind=kind, argv=argv, **evidence)) + "\n"
        with (state / "calls.jsonl").open("ab", buffering=0) as log:
            log.write(line.encode())

    if args[:2] == ["context", "show"]:
        record("context")
        print("default")
    elif args[:2] == ["context", "inspect"]:
        record("context")
        endpoint = os.environ.get("STUB_ENDPOINT", "unix:///var/run/docker.sock")
        print(endpoint if option(args, "--format", "-f") else json.dumps([
            {"Name": "default", "Endpoints": {"docker": {"Host": endpoint}}},
        ]))
    elif args[0] in {"info", "version", "--version"}:
        record("info" if args[0] == "info" else "version")
        if args[0] == "info" and "STUB_FAIL_INFO" in os.environ:
            return 1
        print("fixture-docker" if args[0] == "info" else "Docker version 27.0.0")
    elif args[:2] == ["buildx", "inspect"]:
        record("buildx")
        name = os.environ.get("STUB_BUILDER_NAME", "default")
        driver = os.environ.get("STUB_BUILDER_DRIVER", "docker")
        endpoint = os.environ.get("STUB_BUILDER_ENDPOINT", "default")
        print(f"""Name:   {name}
Driver: {driver}

Nodes:
Name:               default
Endpoint:           {endpoint}
Status:             running
BuildKit version:   v0.27.1
Platforms:          linux/amd64
Devices:
 Name:                  example.com/accelerator=0
 Automatically allowed: false
GC Policy rule#0:
 All:           false
 Filters:       type==source.local
 Keep Duration: 48h0m0s
GC Policy rule#1:
 All:            true
 Max Used Space: 20GiB""")
    elif args[:2] == ["image", "ls"]:
        record("inventory")
        if "STUB_FAIL_INVENTORY" in os.environ:
            return 1
        ref = option(args, "--filter").removeprefix("reference=")
        if image_key(state, ref).is_file():
            print(ref)
    elif args[:2] == ["image", "inspect"] or args[0] == "inspect":
        operands = iter(args[2:] if args[0] == "image" else args[1:])
        refs = []
        for arg in operands:
            if arg in {"--format", "-f"}:
                next(operands)
            elif not arg.startswith("-"):
                refs.append(arg)
        ref, = refs
        record("inspect", ref=ref)
        if "STUB_FAIL_INSPECT" in os.environ:
            print("Error response from daemon: unexpected inspection failure", file=sys.stderr)
            return 1
        key = image_key(state, ref)
        if not key.is_file():
            print(f"No such image: {ref}", file=sys.stderr)
            return 1
        image = key.read_text()
        print(image if option(args, "--format", "-f") else json.dumps([{"Id": image}]))
    elif args[0] == "build" or args[:2] == ["image", "build"]:
        require(option(args, "--builder") == "default", "build did not pin the verified builder")
        context = Path(args[-1]).resolve()
        require(context.is_relative_to(state.parent), f"build context outside fixture: {context}")
        dockerfile = Path(option(args, "--file", "-f", default=context / "Dockerfile"))
        tag = option(args, "--tag", "-t")
        ignore = Path(f"{dockerfile}.dockerignore")
        if not ignore.is_file():
            ignore = context / ".dockerignore"
        # The fixture uses a Dockerfile with no COPY and a deny-all context rule.
        deny_all = ignore.is_file() and ignore.read_text().split() == ["**"]
        inputs = [] if deny_all else sorted(
            str(path.relative_to(context)) for path in context.rglob("*") if path.is_file()
        )
        record("build", tag=tag, context=str(context), inputs=inputs,
               dockerfile=dockerfile.read_text())
        if "STUB_SLOW_BUILD" in os.environ:
            (state / "building").touch()
            time.sleep(1)
        if "STUB_FAIL_BUILD" in os.environ:
            print(BUILD_FAILURE, file=sys.stderr)
            return 41
        image = "sha256:" + hashlib.sha256(os.urandom(16)).hexdigest()
        image_key(state, image).write_text(image)
        image_key(state, tag).write_text(image)
        iidfile = option(args, "--iidfile")
        if iidfile:
            Path(iidfile).write_text(image + "\n")
        record("built", tag=tag, image=image)
        print(image)
    elif args[0] == "tag" or args[:2] == ["image", "tag"]:
        source, target = args[-2:]
        record("tag", source=source, target=target)
        image_key(state, target).write_text(image_key(state, source).read_text())
    elif args[0] == "compose":
        require(argv[:2] == ["--context", "default"], "Compose did not pin local context")
        if args[1:] in (["version"], ["version", "--short"]):
            record("version")
            print("2.30.0")
            return 0
        compose_path = Path(option(args, "-f", "--file"))
        verb_at = args.index(str(compose_path)) + 1
        # The planned fixture uses global options before the Compose verb.
        while args[verb_at] in {"-p", "--project-name", "--env-file"}:
            verb_at += 2
        verb = args[verb_at]
        compose = read_json(compose_path)
        tools = compose["services"]["tools"]
        for field in ("image", "user"):
            match = re.fullmatch(r"\$\{(\w+):\?[^}]+}", tools[field])
            tools[field] = os.environ.get(match[1], "") if match else tools[field]
        record(verb, compose_argv=args, tools=tools, tail=args[verb_at + 1:])
        if verb == "config":
            print(json.dumps(compose))
        elif verb == "run":
            if not image_key(state, tools["image"]).is_file():
                return 125
            if active := os.environ.get("STUB_RETAG"):
                # Simulate the mutable active tag changing after a step acquires its image.
                image_key(state, active).write_text(os.environ["STUB_REPLACEMENT"])
            return int(os.environ.get("STUB_NATIVE_EXIT", "0"))
        elif verb != "up":
            return 125
    else:
        record("unexpected")
        print(f"Unexpected Docker call: {argv!r}", file=sys.stderr)
        return 125
    return 0


def bind(source, target, *, read_only=False):
    return {"type": "bind", "source": str(source), "target": target,
            "read_only": read_only, "bind": {"create_host_path": False}}


class Fixture:
    def __init__(self, root, name="sample project"):
        self.project = root / name
        self.install = self.project / "dev"
        self.launcher = self.install / "dev.sh"
        self.helpers = self.install / "helpers"
        self.app = self.project / "sources" / "amber engine"
        self.other = self.project / "third party" / "quartz"
        self.cache = self.project / ".local" / "dev-cache"
        self.caller = root / "unrelated caller"
        self.state = root / "docker-state"
        bin_dir = root / "bin"
        for path in (self.helpers, self.app / "module one", self.other / "module two",
                     self.cache, self.caller, self.state, bin_dir, root / "home"):
            path.mkdir(parents=True, exist_ok=True)
        self.env = {
            "PATH": str(bin_dir), "HOME": str(root / "home"), "LANG": "C",
            "TMPDIR": str(root), "STUB_STATE": str(self.state),
        }
        # Deliberately omit host Docker, Java and Node from the launcher PATH.
        for name in ("bash", "sh", "python3", "dirname", "realpath", "readlink",
                     "id", "mkdir", "sha256sum", "flock", "mktemp", "rm"):
            command = shutil.which(name)
            if command and not (bin_dir / name).exists():
                (bin_dir / name).symlink_to(command)
        stub = bin_dir / "docker"
        stub.write_text(f"#!{sys.executable}\nfrom runpy import run_path\n"
                        f"raise SystemExit(run_path({str(Path(__file__).resolve())!r})"
                        "['docker_stub']())\n")
        stub.chmod(0o755)
        self.dockerfile = self.install / "Dockerfile"
        self.dockerfile.write_text("FROM scratch\nLABEL purpose=fixture\n")
        (self.install / "Dockerfile.dockerignore").write_text("**\n")
        (self.caller / "Dockerfile").write_text("FROM scratch\nLABEL purpose=decoy\n")
        self.protected = {
            self.app / "Makefile": "build:\n\ttrue\n",
            self.app / "package-lock.json": '{"fixture": true}\n',
            self.cache / "marker": "existing cache\n",
            self.project / ".local" / "service-data": "existing service state\n",
            self.install / ".env": "DEV_TOOLS_IMAGE=unrelated-image\n",
            self.install / "secret.txt": "synthetic secret\n",
        }
        for path, contents in self.protected.items():
            path.write_text(contents)
        self.app_step = {"checkout": "app", "workdir": "module one"}
        self.other_step = {"checkout": "other", "workdir": "module two"}
        self.config = {
            "checkouts": {"app": {"host_path": str(self.app), "mount": "/workspace"}},
            "commands": {
                "build": [dict(self.app_step, argv=["make", "build"])],
                "check": [dict(self.app_step, argv=["make", "check"])],
                "test": [dict(self.app_step, argv=["python3", "-m", "unittest"])],
                "test-selected": [dict(self.app_step, argv=["mvn", "-Dtest={selector}", "test"])],
            },
            "cache_dir": str(self.cache),
            "services": [],
        }
        self.compose = self.install / "compose.yaml"
        self.config_path = self.install / "dev.json"
        self.configure()

    def configure(self):
        write_json(self.config_path, self.config)
        self.volumes = [bind(item["host_path"], item["mount"])
                        for item in self.config["checkouts"].values()] + [
            bind(self.helpers, "/opt/project-dev", read_only=True),
            bind(self.cache, "/opt/project-cache"),
        ]
        # JSON is valid YAML and keeps the recording stub dependency-free.
        write_json(self.compose, {"services": {
            "tools": {"image": "${DEV_TOOLS_IMAGE:?Run dev.sh}",
                      "user": "${DEV_TOOLS_USER:?Run dev.sh}",
                      "environment": {"HOME": "/opt/project-cache"}, "entrypoint": [],
                      "depends_on": ["unrelated"], "volumes": self.volumes},
            "db": {"image": "fixture-db", "depends_on": ["db-init"]},
            "db-init": {"image": "fixture-init"},
            "unrelated": {"image": "fixture-unrelated"},
        }})

    def install_package(self):
        for source, target in (("dev.sh", self.launcher),
                               ("dev-runner.py", self.helpers / "dev-runner.py"),
                               ("run-in-tools.sh", self.helpers / "run-in-tools.sh")):
            path = ROOT / "scripts" / source
            require(path.is_file(), f"missing installed component scripts/{source}")
            shutil.copy2(path, target)

    def invoke(self, *args, **environment):
        return subprocess.run([BASH, self.launcher, *args], cwd=self.caller,
                              env=self.env | environment, timeout=15, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def calls(self, *kinds):
        log = self.state / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [call for call in calls if call["kind"] in kinds] if kinds else calls

    def clear_calls(self):
        (self.state / "calls.jsonl").unlink(missing_ok=True)

    def compose_prefix(self, project):
        return ["compose", "--env-file", "/dev/null", "-p", project, "-f", str(self.compose)]


def require(condition, problem):
    if not condition:
        raise AssertionError(problem)


def equal(actual, expected, description):
    require(actual == expected, f"{description}: expected {expected!r}, got {actual!r}")


def status(result, expected, description):
    output = "\n".join(text.strip() for text in (result.stdout, result.stderr) if text)
    equal(result.returncode, expected, f"{description} ({output})")


def operations(fixture):
    return [call["kind"] for call in fixture.calls() if call["kind"] not in PROBES | {"config"}]


def verify_run(fixture, call, project, workdir, argv, image):
    equal(call["compose_argv"], fixture.compose_prefix(project) + [
        "run", "--rm", "--no-deps", "--pull", "never", "-T", "-w", workdir,
        "tools", "/bin/sh", "/opt/project-dev/run-in-tools.sh", *argv,
    ], "Compose run argv")
    tools = call["tools"]
    equal(tools["image"], image, "immutable image ID passed to Compose")
    equal(tools["user"], f"{os.getuid()}:{os.getgid()}", "numeric container user")
    equal(tools["volumes"], fixture.volumes, "scoped long binds")
    equal(tools["environment"]["HOME"], "/opt/project-cache", "writable container home")
    for key in ("privileged", "ports", "volumes_from"):
        require(not tools.get(key), f"unsafe tools option: {key}")


def verify_build(fixture, call, *, refresh):
    equal(call["dockerfile"], fixture.dockerfile.read_text(), "installed Dockerfile")
    require(set(call["inputs"]) <= {"Dockerfile", "Dockerfile.dockerignore", ".dockerignore"},
            f"non-build inputs sent to Docker: {call['inputs']!r}")
    require(call["tag"], "Docker build has no tag")
    for flag in ("--pull", "--no-cache"):
        equal(flag in call["argv"], refresh, f"Docker build flag {flag}")


def first_run(fixture):
    status(fixture.invoke("build"), 0, "first build from unrelated cwd")
    kinds = operations(fixture)
    require(kinds[:3] == ["inspect", "build", "built"] and kinds[-1:] == ["run"]
            and set(kinds[3:-1]) <= {"inspect"}, f"unexpected cold dispatch: {kinds!r}")
    build, = fixture.calls("build")
    built, = fixture.calls("built")
    run, = fixture.calls("run")
    verify_build(fixture, build, refresh=False)
    equal(fixture.calls("inspect")[0]["ref"], build["tag"], "first inspected active tag")
    project = option(run["argv"], "-p")
    require(project and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project),
            f"invalid Compose project identity: {project!r}")
    verify_run(fixture, run, project, "/workspace/module one", ["make", "build"], built["image"])
    return project, build["tag"], built["image"]


def reject(fixture, args, description, **environment):
    fixture.clear_calls()
    result = fixture.invoke(*args, **environment)
    require(result.returncode != 0, f"accepted {description}: {result.stdout}")
    equal(operations(fixture), [], f"Docker side effects for {description}")


def check_dispatch(fixture, project, active, image):
    fixture.clear_calls()
    status(fixture.invoke("help"), 0, "help")
    equal(fixture.calls(), [], "Docker calls for help")
    for args in (("bogus",), ("start",), ("check", "extra"), ("update", "extra"),
                 ("test-selected",), ("test-selected", "one", "two"), ("test-selected", "")):
        reject(fixture, args, repr(args))

    for selector in ("Class#method", "Class#method; $(touch should-not-exist) *"):
        fixture.clear_calls()
        status(fixture.invoke("test-selected", selector), 0, "native selector")
        equal(operations(fixture), ["inspect", "run"], "image reuse on selected test")
        inspect, = fixture.calls("inspect")
        equal(inspect["ref"], active, "cached active tag")
        run, = fixture.calls("run")
        verify_run(fixture, run, project, "/workspace/module one",
                   ["mvn", f"-Dtest={selector}", "test"], image)
    require(not (fixture.caller / "should-not-exist").exists(), "selector evaluated as shell text")

    fixture.config["commands"]["test-selected"][0]["argv"] = ["python3", "-m", "unittest", "{selector}"]
    fixture.config["checkouts"]["other"] = {"host_path": str(fixture.other), "mount": "/quartz"}
    spaced = ["printf", "%s", "a b", "$(touch should-not-exist)", "one;two", "", "*"]
    fixture.config["commands"]["check"].append(dict(fixture.other_step, argv=spaced))
    fixture.configure()
    fixture.clear_calls()
    selector = "test_probe.Probe.test_pass"
    status(fixture.invoke("test-selected", selector), 0, "whole-argument selector")
    run, = fixture.calls("run")
    verify_run(fixture, run, project, "/workspace/module one", ["python3", "-m", "unittest", selector], image)

    fixture.clear_calls()
    status(fixture.invoke("test"), 0, "native test")
    run, = fixture.calls("run")
    verify_run(fixture, run, project, "/workspace/module one", ["python3", "-m", "unittest"], image)

    replacement = "sha256:" + "f" * 64
    image_key(fixture.state, replacement).write_text(replacement)
    fixture.clear_calls()
    status(fixture.invoke("check", STUB_RETAG=active, STUB_REPLACEMENT=replacement), 0, "ordered check")
    equal(operations(fixture), ["inspect", "run", "run"], "ordered dispatch with one acquisition")
    first, second = fixture.calls("run")
    verify_run(fixture, first, project, "/workspace/module one", ["make", "check"], image)
    verify_run(fixture, second, project, "/quartz/module two", spaced, image)
    image_key(fixture.state, active).write_text(image)

    fixture.clear_calls()
    status(fixture.invoke("check", STUB_NATIVE_EXIT="23"), 23, "native failure")
    equal(operations(fixture), ["inspect", "run"], "native failure stops subsequent steps")
    run, = fixture.calls("run")
    verify_run(fixture, run, project, "/workspace/module one", ["make", "check"], image)

    fixture.config["services"] = ["db"]
    fixture.configure()
    fixture.clear_calls()
    status(fixture.invoke("start"), 0, "configured service start")
    up, = fixture.calls("up")
    equal(up["compose_argv"][:len(fixture.compose_prefix(project))], fixture.compose_prefix(project),
          "service start Compose scope")
    equal([arg for arg in up["tail"] if not arg.startswith("-")], ["db"], "requested services")
    require(set(operations(fixture)) <= {"inspect", "up"}, "unexpected service startup side effects")
    compose = read_json(fixture.compose)
    compose["services"]["db-init"]["depends_on"] = ["tools"]
    write_json(fixture.compose, compose)
    reject(fixture, ("start",), "service dependency on tools")
    fixture.configure()
    fixture.clear_calls()
    status(fixture.invoke("check"), 0, "ordinary check with services configured")
    equal(operations(fixture), ["inspect", "run", "run"], "ordinary check starts no services")


def check_validation(fixture):
    good = fixture.config
    (fixture.app / "escape").symlink_to(fixture.other, target_is_directory=True)
    (fixture.project / "alias").symlink_to(fixture.app, target_is_directory=True)
    for description, keys, value in (
        ("relative checkout", ("checkouts", "app", "host_path"), "sources/amber engine"),
        ("noncanonical checkout", ("checkouts", "app", "host_path"), str(fixture.project / "alias")),
        ("missing checkout mount", ("checkouts", "app", "host_path"), str(fixture.project / "absent")),
        ("relative container mount", ("checkouts", "app", "mount"), "workspace"),
        ("root container mount", ("checkouts", "app", "mount"), "/"),
        ("noncanonical container mount", ("checkouts", "app", "mount"), "/x/../workspace"),
        ("duplicate container mount", ("checkouts", "other", "mount"), "/workspace"),
        ("reserved helper mount", ("checkouts", "other", "mount"), "/opt/project-dev"),
        ("reserved cache mount", ("checkouts", "other", "mount"), "/opt/project-cache"),
        ("nested checkout mount", ("checkouts", "other", "mount"), "/workspace/vendor"),
        ("cache in checkout", ("cache_dir",), str(fixture.app / "module one")),
        ("cache outside project", ("cache_dir",), str(fixture.caller)),
        ("invalid service allowlist", ("services",), "db"),
        ("empty step list", ("commands", "check"), []),
        ("shell command", ("commands", "check"), "make check"),
        ("unknown checkout in later step", ("commands", "check", 1, "checkout"), "missing"),
        ("absolute workdir", ("commands", "check", 1, "workdir"), str(fixture.other)),
        ("missing workdir", ("commands", "check", 1, "workdir"), "absent"),
        ("escaping workdir", ("commands", "check", 1, "workdir"), ".."),
        ("symlink escape", ("commands", "check", 0, "workdir"), "escape"),
        ("empty command argv", ("commands", "check", 1, "argv"), []),
        ("empty executable", ("commands", "check", 1, "argv"), ["", "check"]),
        ("non-string argv", ("commands", "check", 1, "argv"), ["make", 1]),
        ("NUL in argv", ("commands", "check", 1, "argv"), ["make", "bad\0arg"]),
    ):
        config = deepcopy(good)
        parent = config
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
        write_json(fixture.config_path, config)
        reject(fixture, ("check",), description)

    for argv in (["mvn"], ["mvn", "{selector}", "-Dtest={selector}"],
                 ["mvn", "{selector}/suffix"], ["mvn", "{selector}{selector}"]):
        config = deepcopy(good)
        config["commands"]["test-selected"][0]["argv"] = argv
        write_json(fixture.config_path, config)
        reject(fixture, ("test-selected", "Class#method"), f"invalid selector mapping {argv!r}")

    fixture.config_path.write_text("{broken JSON")
    reject(fixture, ("check",), "malformed dev.json")
    fixture.config_path.unlink()
    reject(fixture, ("check",), "missing dev.json")
    config = deepcopy(good)
    del config["checkouts"]["app"]["mount"]
    write_json(fixture.config_path, config)
    reject(fixture, ("check",), "missing checkout mount mapping")
    fixture.configure()
    fixture.compose.rename(fixture.install / "compose.backup")
    reject(fixture, ("check",), "missing compose.yaml")
    (fixture.install / "compose.backup").rename(fixture.compose)
    compose = read_json(fixture.compose)
    compose["services"]["tools"]["volumes"][0]["source"] = str(fixture.caller)
    write_json(fixture.compose, compose)
    reject(fixture, ("check",), "Compose checkout bind disagrees with dev.json")
    fixture.configure()
    reject(fixture, ("check",), "remote Docker host override", DOCKER_HOST="tcp://fixture.invalid:2375")
    reject(fixture, ("check",), "remote Docker context endpoint", STUB_ENDPOINT="tcp://fixture.invalid:2375")


def check_refresh(fixture, project, active, image):
    fixture.clear_calls()
    before = {path: path.read_bytes() for path in fixture.protected}
    status(fixture.invoke("update"), 0, "successful refresh")
    equal([kind for kind in operations(fixture) if kind != "inspect"], ["build", "built", "tag"],
          "refresh builds before promotion and starts no containers")
    build, = fixture.calls("build")
    built, = fixture.calls("built")
    tag, = fixture.calls("tag")
    verify_build(fixture, build, refresh=True)
    require(build["tag"] != active, "refresh built directly under the active tag")
    equal(tag["target"], active, "promotion to existing active tag without Dockerfile change")
    require(tag["source"] in {built["tag"], built["image"]}, "promotion did not use candidate image")
    equal(image_key(fixture.state, active).read_text(), built["image"], "promoted image ID")
    replacement = built["image"]
    require(replacement != image, "fixture did not distinguish the two image IDs")

    fixture.clear_calls()
    status(fixture.invoke("build"), 0, "ordinary use of refreshed image")
    equal(operations(fixture), ["inspect", "run"], "refreshed image reuse")
    run, = fixture.calls("run")
    verify_run(fixture, run, project, "/workspace/module one", ["make", "build"], replacement)

    fixture.clear_calls()
    failed = fixture.invoke("update", STUB_FAIL_BUILD="1")
    require(failed.returncode != 0 and re.search(r"\b(build|pull|rebuild)\b", failed.stdout, re.I),
            f"failed update did not report its stage: exit {failed.returncode}, {failed.stdout!r}")
    equal([kind for kind in operations(fixture) if kind != "inspect"], ["build"], "failed update side effects")
    failed_build, = fixture.calls("build")
    verify_build(fixture, failed_build, refresh=True)
    require(failed_build["tag"] not in {active, build["tag"]}, "candidate tag was reused")
    equal(image_key(fixture.state, active).read_text(), replacement, "active tag after failed update")
    equal({path: path.read_bytes() for path in before}, before, "persistent files after both update attempts")

    fixture.clear_calls()
    status(fixture.invoke("build"), 0, "ordinary run after failed update")
    equal(operations(fixture), ["inspect", "run"], "reuse without rebuilding after failed update")
    run, = fixture.calls("run")
    verify_run(fixture, run, project, "/workspace/module one", ["make", "build"], replacement)


def overlapping_build(fixture, second_verb):
    fixture.clear_calls()
    building = fixture.state / "building"
    building.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(fixture.invoke, "build", STUB_SLOW_BUILD="1")
        deadline = time.monotonic() + 10
        while not building.exists() and not first.done() and time.monotonic() < deadline:
            time.sleep(0.01)
        require(building.exists(), "first overlapping build never reached Docker")
        concurrent = executor.submit(fixture.invoke, second_verb)
        status(first.result(), 0, "first concurrent run")
        status(concurrent.result(), 0, f"concurrent {second_verb}")


def check_image_isolation(fixture, first_project, first_active):
    fixture.clear_calls()
    second = Fixture(fixture.project.parent, "second install")
    second.install_package()
    project, active, _ = first_run(second)
    require(project != first_project, "two installs shared a Compose project ID")
    require(active != first_active, "two installs with identical Dockerfiles shared an active tag")

    second.dockerfile.write_text(second.dockerfile.read_text() + "LABEL revision=2\n")
    second.clear_calls()
    project_after, active_after, _ = first_run(second)
    equal(project_after, project, "Compose project with a new Dockerfile hash")
    require(active_after not in {active, first_active}, "Dockerfile change reused an existing active tag")

    second.dockerfile.write_text(second.dockerfile.read_text() + "LABEL revision=3\n")
    second.clear_calls()
    failed = second.invoke("build", STUB_FAIL_BUILD="1")
    require(failed.returncode != 0, "cold image build failure returned success")
    equal(operations(second), ["inspect", "build"], "cold build failure must prevent launch")

    overlapping_build(second, "build")
    equal(len(second.calls("build")), 1, "concurrent cache misses must share one build")
    built, = second.calls("built")
    equal([call["tools"]["image"] for call in second.calls("run")], [built["image"]] * 2,
          "concurrent runs used the same built image")

    second.dockerfile.write_text(second.dockerfile.read_text() + "LABEL revision=4\n")
    overlapping_build(second, "update")
    stages = second.calls("build", "built", "tag")
    equal([call["kind"] for call in stages], ["build", "built", "build", "built", "tag"],
          "cache miss and refresh must share a build lock")
    equal(stages[-1]["target"], stages[0]["tag"], "concurrent promotion targets the current active tag")


def check_container_helper(fixture):
    argv = ["a b", "Class#method", "$(touch should-not-exist)", "", "*"]
    result = subprocess.run([
        "/bin/sh", fixture.helpers / "run-in-tools.sh", sys.executable, "-c",
        "import json, sys; print(json.dumps(sys.argv[1:])); sys.exit(23)", *argv,
    ], cwd=fixture.caller, env=fixture.env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=5)
    status(result, 23, "container helper native exit")
    equal(json.loads(result.stdout), argv, "container helper exact argv")


def contract():
    with TemporaryDirectory(prefix="dev environment smoke ") as directory:
        fixture = Fixture(Path(directory).resolve())
        probe = subprocess.run(["docker", "context", "show"], cwd=fixture.caller, env=fixture.env,
                               capture_output=True, text=True, timeout=5)
        require(probe.returncode == 0 and probe.stdout.strip() == "default" and fixture.calls("context"),
                f"recording Docker fixture failed: {probe.stderr}")
        fixture.clear_calls()
        require((ROOT / "scripts" / "dev.sh").is_file(),
                "launcher absent: scripts/dev.sh (recording Docker fixture ready)")
        fixture.install_package()
        reject(fixture, ("build",), "daemon failure", STUB_FAIL_INFO="1")
        fixture.clear_calls()
        failed = fixture.invoke("build", STUB_FAIL_INVENTORY="1")
        require(failed.returncode != 0, "cache miss accepted with failed image inventory")
        equal(operations(fixture), ["inspect"], "failed inventory must prevent builds")
        for field, value in (
            ("NAME", "other"),
            ("DRIVER", "remote"),
            ("ENDPOINT", "tcp://fixture.invalid:2375"),
        ):
            fixture.clear_calls()
            failed = fixture.invoke("build", **{f"STUB_BUILDER_{field}": value})
            description = f"builder {field.lower()} {value!r}"
            require(failed.returncode != 0, f"cache miss accepted with {description}")
            equal(len(fixture.calls("buildx")), 1, f"inspect reached for {description}")
            equal(operations(fixture), ["inspect"], f"{description} must prevent builds")
        fixture.clear_calls()
        project, active, image = first_run(fixture)
        for verb in ("build", "update"):
            fixture.clear_calls()
            failed = fixture.invoke(verb, STUB_FAIL_INSPECT="1")
            require(failed.returncode != 0, f"{verb}: image inspection error returned success")
            equal(operations(fixture), ["inspect"], f"{verb}: image inspection error must prevent builds")
        check_container_helper(fixture)
        check_dispatch(fixture, project, active, image)
        check_validation(fixture)
        check_refresh(fixture, project, active, image)
        check_image_isolation(fixture, project, active)


def record_real_docker():
    """Record real argv and optionally supply a deliberately invalid candidate context."""
    args = sys.argv[1:]
    with open(os.environ["SMOKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": args, "image": os.environ.get("DEV_TOOLS_IMAGE")}) + "\n")
    command = args[2:] if args[:2] == ["--context", "default"] else args
    if command[:1] == ["build"] and os.environ.get("SMOKE_BROKEN_CONTEXT"):
        require("candidate-" in option(command, "--tag", default="") and "--no-cache" in command,
                "failure injection must target a refresh candidate")
        print("Submitting deliberately invalid Dockerfile to the real builder", file=sys.stderr)
        args[-1] = os.environ["SMOKE_BROKEN_CONTEXT"]
    os.execv(os.environ["SMOKE_REAL_DOCKER"], ["docker", *args])


def real_smoke(install, *, refresh):
    require(install.is_absolute() and install == install.resolve() and install.name == "dev",
            "--install must be the canonical absolute synthetic dev/ path")
    config = read_json(install / "dev.json")
    project = install.parent
    checkout = project / "sources" / "amber-engine"
    cache = Path(config["cache_dir"])
    require(project.name == "sample-project" and len(config["checkouts"]) == 1
            and next(iter(config["checkouts"].values())) == {"host_path": str(checkout), "mount": "/workspace"}
            and cache.resolve() == cache and cache.is_relative_to(project),
            "real modes require the synthetic sample-project layout in references/toolchain.md")
    require((checkout / "test_probe.py").is_file(), "missing native synthetic test_probe.py")
    guide = install / "AGENTS.md"
    link = f"[the dev guide]({os.path.relpath(guide, checkout)})"
    equal((checkout / "AGENTS.md").read_text().count(link), 1, "checkout guide pointer")
    check = shlex.join([str(install / "dev.sh"), "check"])
    require(f"`{check}`" in guide.read_text(), f"missing exact check invocation in {guide}: {check}")
    checkout_name, = config["checkouts"]
    equal(config["commands"]["test-selected"], [
        {"checkout": checkout_name, "workdir": ".",
         "argv": ["python3", "-m", "unittest", "{selector}"]},
    ], "synthetic native selector mapping")
    for source, target in (("dev.sh", "dev.sh"), ("dev-runner.py", "helpers/dev-runner.py"),
                           ("run-in-tools.sh", "helpers/run-in-tools.sh")):
        equal((install / target).read_bytes(), (ROOT / "scripts" / source).read_bytes(),
              f"installed package bytes for {target}")
    real_docker = shutil.which("docker")
    require(real_docker, "Docker CLI unavailable")
    identity = "dev-" + hashlib.sha256(os.fsencode(install)).hexdigest()[:24]
    active = f"{identity}-tools:{hashlib.sha256((install / 'Dockerfile').read_bytes()).hexdigest()}"

    with TemporaryDirectory(prefix="dev-environment real caller ") as directory:
        caller = Path(directory).resolve()
        wrapper = caller / "docker"
        wrapper.write_text(f"#!{sys.executable}\nfrom runpy import run_path\n"
                           f"run_path({str(Path(__file__).resolve())!r})['record_real_docker']()\n")
        wrapper.chmod(0o755)
        log = caller / "docker.jsonl"
        env = os.environ | {"PATH": str(caller) + os.pathsep + os.environ.get("PATH", ""),
                            "SMOKE_REAL_DOCKER": str(Path(real_docker).resolve()), "SMOKE_DOCKER_LOG": str(log),
                            "SMOKE_BROKEN_CONTEXT": ""}

        def launch(*args, **overrides):
            log.unlink(missing_ok=True)
            result = subprocess.run([str(install / "dev.sh"), *args], cwd=caller, env=env | overrides,
                                    capture_output=True, text=True, timeout=900)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

        def ordinary(*args):
            result, calls = launch(*args)
            status(result, 0, f"native {args[0]}")
            runs = [call for call in calls if "compose" in call["argv"] and "run" in call["argv"]]
            require(runs and all(re.fullmatch(r"sha256:[a-f0-9]{64}", call["image"] or "") for call in runs),
                    "ordinary command did not use an immutable image ID")
            require(not any(call["argv"][2:3] == ["build"] for call in calls), "ordinary command rebuilt")
            ids = {item["image"] for item in runs}
            require(len(ids) == 1, "native steps used different tool images")
            return ids.pop()

        def image_id():
            return subprocess.check_output(
                [real_docker, "--context", "default", "image", "inspect", "--format", "{{.Id}}", active],
                cwd=caller, env=env, text=True, timeout=15).strip()

        print(f"install={install} caller={caller}")
        if refresh:
            files = [checkout / "Makefile", cache / "dev-smoke-marker", install / "Dockerfile"]
            files += [checkout / name for name in ("pyproject.toml", "requirements.txt", "package.json",
                                                   "package-lock.json", "uv.lock", "poetry.lock")
                      if (checkout / name).is_file()]
            service_state = project / ".local" / "service-data"
            if service_state.is_file():
                files.append(service_state)
            before = {path: path.read_bytes() for path in files}
            selector = ("test-selected", "test_probe.Probe.test_pass")
            prior = ordinary(*selector)
            equal(image_id(), prior, "active image before refresh")
            result, calls = launch("update")
            builds = [call["argv"] for call in calls if call["argv"][2:3] == ["build"]]
            print(f"refresh build argv={json.dumps(builds)}")
            status(result, 0, "real refresh")
            build, = builds
            require({"--pull", "--no-cache"} <= set(build) and option(build, "--tag") != active,
                    "real update did not request a fresh candidate build")
            replacement = image_id()
            equal(ordinary(*selector), replacement, "native run on refreshed image")
            broken = caller / "broken"
            broken.mkdir()
            (broken / "Dockerfile").write_text("INVALID_INSTRUCTION fail-refresh-probe\n")
            failed, calls = launch("update", SMOKE_BROKEN_CONTEXT=str(broken))
            print(f"invalid candidate exit={failed.returncode}")
            require(failed.returncode != 0 and "Submitting deliberately invalid Dockerfile" in failed.stderr
                    and "unknown instruction" in failed.stderr.lower(),
                    f"real candidate failure was not observed: {failed.stderr}")
            require(not any(call["argv"][2:4] == ["image", "tag"] for call in calls),
                    "failed candidate reached promotion")
            equal(image_id(), replacement, "active image after failed refresh")
            equal(ordinary(*selector), replacement, "native run after failed refresh")
            equal({path: path.read_bytes() for path in before}, before, "state after real refresh attempts")
        else:
            result, calls = launch("build")
            builds = [call["argv"] for call in calls if call["argv"][2:3] == ["build"]]
            print(f"cold build argv={json.dumps(builds)}")
            status(result, 0, "real first build")
            equal(len(builds), 1, "fresh synthetic install must build once")
            image = image_id()
            equal(ordinary("test"), image, "existing native tests")
            equal(ordinary("test-selected", "test_probe.Probe.test_pass"), image, "existing native selector")

            token = "dev_smoke_" + uuid4().hex
            module = checkout / f"test_{token}.py"
            generated = checkout / token
            marker = cache / token
            failure = checkout / ".dev-smoke-check-fail"
            require(not failure.exists() and not failure.is_symlink(), "stale check-failure marker")
            with module.open("x") as probe:
                probe.write(f"""import unittest
from pathlib import Path

class Probe(unittest.TestCase):
    def test_write(self):
        self.assertTrue(Path('/opt/project-dev/run-in-tools.sh').is_file())
        Path({token!r}).write_text('fixture')
        (Path.home() / {token!r}).write_text('fixture')

    def test_reuse(self):
        self.assertEqual((Path.home() / {token!r}).read_text(), 'fixture')
""")
            failure_created = False
            try:
                equal(ordinary("test-selected", f"{module.stem}.Probe.test_write"), image, "untracked native test")
                equal(ordinary("test-selected", f"{module.stem}.Probe.test_reuse"), image, "persistent cache")
                owner = subprocess.check_output(["stat", "-c", "%u:%g", "--", str(generated)],
                                                text=True, timeout=5).strip()
                equal(owner, f"{os.getuid()}:{os.getgid()}", "native output ownership")
                with failure.open("x"):
                    failure_created = True
                failed, _ = launch("check")
                status(failed, 2, "deliberate native make check failure")
            finally:
                for path in (module, generated, marker):
                    path.unlink(missing_ok=True)
                if failure_created:
                    failure.unlink(missing_ok=True)
            equal(ordinary("check"), image, "native check recovery")
        remaining = subprocess.check_output(
            [real_docker, "--context", "default", "ps", "-a", "--quiet", "--filter",
             f"label=com.docker.compose.project={identity}", "--filter", "label=com.docker.compose.oneoff=True"],
            cwd=caller, env=env, text=True, timeout=15)
        require(not remaining.strip(), "disposable tool containers remain")
        print(f"active={active} image={image_id()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--contract", action="store_true", help="use only the Docker recording fixture")
    modes.add_argument("--docker", action="store_true", help="run the native workflow on a real synthetic install")
    modes.add_argument("--update", action="store_true", help="run real successful and failing candidate builds")
    parser.add_argument("--install", type=Path, help="canonical absolute path to the synthetic dev/ install")
    args = parser.parse_args()
    if args.contract == (args.install is not None):
        parser.error("--install is required for --docker or --update and forbidden with --contract")
    try:
        if args.contract:
            contract()
        else:
            real_smoke(args.install, refresh=args.update)
    except (AssertionError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: dev-environment {'contract' if args.contract else 'real update' if args.update else 'real Docker'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
