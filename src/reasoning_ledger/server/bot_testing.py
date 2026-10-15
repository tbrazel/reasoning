"""Owner-configured, offline development-box test profiles shared by CLI and bots.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from ..deployment.host import InstallError
from ..deployment.private import protected_temporary_directory, read_document, write_document
from .bot_client import BotClientError, bot_panel
from .bot_developer import REPOSITORY
from .bot_gateway import bounded_execute, workspace_open

PROFILES = {
    "smoke": ["tests/test_public_server.py"],
    "bots": ["tests/test_public_bot_runtime.py", "tests/test_public_bot_developer.py"],
    "knowledge": ["tests/test_public_ledger_human_checks.py", "tests/test_public_source_statements.py"],
    "frontend": None,
}
SOURCE_DIRECTORIES = {"src", "tests", "web", "desktop", "docs", "LICENSES"}
SOURCE_ROOT_FILES = {"pyproject.toml", "uv.lock", "README.md", "AGENTS.md", "LICENSE", "NOTICE", "REUSE.toml", "Makefile"}
MAX_FILE, MAX_SOURCE = 4 * 1024 * 1024, 128 * 1024 * 1024


def included(name):
    relative = Path(name)
    return (not relative.is_absolute() and ".." not in relative.parts and bool(relative.parts)
            and (relative.parts[0] in SOURCE_DIRECTORIES or name in SOURCE_ROOT_FILES)
            and not any(part.startswith(".env") or part in {"node_modules", ".venv", ".git", "__pycache__"}
                        for part in relative.parts))


def policy(path):
    document = read_document(Path(path))
    fields = {"format", "repository", "source_root", "profiles"}
    if (set(document) not in (fields, fields | {"node_executable"}) or type(document["format"]) is not int
            or document["format"] != 1 or not isinstance(document["repository"], str)
            or not REPOSITORY.fullmatch(document["repository"]) or not isinstance(document["profiles"], list)
            or not document["profiles"] or any(item not in PROFILES for item in document["profiles"])
            or len(set(document["profiles"])) != len(document["profiles"]) or not isinstance(document["source_root"], str)):
        raise BotClientError("invalid local developer-test policy; configure it from the trusted development checkout")
    node = document.get("node_executable")
    if node is not None and (not isinstance(node, str) or not Path(node).is_absolute() or Path(node).name != "node"):
        raise BotClientError("developer frontend tests require an exact locally configured Node executable")
    root = Path(document["source_root"])
    if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
            or not (root / "src/reasoning_ledger/server/api.py").is_file()):
        raise BotClientError("developer tests require the configured local Reasoning checkout")
    return {**document, "source_root": str(root.resolve(strict=True))}


def _git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], env={"PATH": os.defpath}, timeout=15)


def snapshot(root, destination):
    """Copy bounded tracked source only: never .git, ignored exports, credentials or host home."""
    names = _git(root, "ls-files", "-z").decode().split("\0")
    if len(names) > 10000:
        raise BotClientError("developer checkout exceeds the source snapshot file allowance")
    checksum, total, count = hashlib.sha256(), 0, 0
    for name in sorted(filter(None, names)):
        relative = Path(name)
        if not included(name):
            continue
        source = root / relative
        if not source.exists():  # A locally deleted tracked file is absent from the tested snapshot.
            continue
        if any((root / Path(*relative.parts[:index])).is_symlink() for index in range(1, len(relative.parts) + 1)):
            raise BotClientError("developer source snapshots do not follow symlinks")
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > MAX_FILE:
            raise BotClientError("developer source snapshot contains an oversized or nonregular file")
        body = source.read_bytes()
        total += len(body)
        if len(body) > MAX_FILE or total > MAX_SOURCE:
            raise BotClientError("developer source snapshot exceeds its byte allowance")
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(body)
        checksum.update(name.encode() + b"\0" + len(body).to_bytes(8, "big") + body)
        count += 1
    revision = _git(root, "rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
        raise BotClientError("invalid checkout revision")
    return {"source_revision": revision, "snapshot_sha256": checksum.hexdigest(), "files": count,
            "dirty": bool(_git(root, "status", "--porcelain", "--untracked-files=no").strip())}


def sandbox(root, source, profile, *, node_executable=None):
    executable = shutil.which("bwrap")
    if sys.platform != "linux" or not executable:
        raise BotClientError("developer test isolation requires Linux bubblewrap; no host test was started")
    args = [executable, "--die-with-parent", "--unshare-all", "--new-session", "--clearenv",
            "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "PYTHONPATH", str(root / "src"), "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for name in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/ld.so.cache"):
        if Path(name).exists():
            args += ["--ro-bind", name, name]
    # Mount toolchains before replacing the checkout with a frozen source-only snapshot.
    if profile == "frontend":
        node = node_executable or shutil.which("node")
        dependencies = root / "web/node_modules"
        if not node or not dependencies.is_dir():
            raise BotClientError("install frontend dependencies and select the repository's Node version first")
        node = str(Path(node).resolve(strict=True))
        (source / "web/node_modules").symlink_to("/dependencies/node_modules", target_is_directory=True)
        args += ["--ro-bind", node, node, "--ro-bind", str(dependencies.resolve()), "/dependencies/node_modules"]
        for cache in (".vite", ".vite-temp"):
            (dependencies / cache).mkdir(exist_ok=True)
            args += ["--tmpfs", "/dependencies/node_modules/" + cache]
        command = [node, "--test", *[str(Path("tests") / file.name) for file in sorted((source / "web/tests").glob("*.test.mjs"))]]
        if len(command) <= 2:
            raise BotClientError("no frontend unit tests in the configured checkout")
    else:
        venv = root / ".venv"
        if not (venv / "bin/python").is_file():
            raise BotClientError("run make dev-setup in the configured checkout before testing")
        args += ["--ro-bind", str(venv.resolve()), "/python"]
        interpreter = (venv / "bin/python").resolve(strict=True)
        if not interpreter.is_relative_to(Path("/usr")):
            prefix = interpreter.parent.parent
            if interpreter.parent.name != "bin" or not (prefix / "lib").is_dir():
                raise BotClientError("Python must come from a dedicated toolchain prefix, not a home-directory executable")
            args += ["--ro-bind", str(prefix), str(prefix)]
            launcher = venv / "bin/python"
            if launcher.is_symlink():
                alias = Path(os.path.normpath(str(launcher.parent / launcher.readlink()))).parent.parent
                if alias != prefix and alias.resolve(strict=True) == prefix:
                    args += ["--ro-bind", str(prefix), str(alias)]
        command = ["/python/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *PROFILES[profile]]
    working = root / "web" if profile == "frontend" else root
    return args + ["--ro-bind", str(source), str(root), "--chdir", str(working), "--", *command]


def prepare(document, profile, directory, *, regression=None):
    if profile not in document["profiles"]:
        raise BotClientError("test profile is not enabled in the local owner policy")
    root = Path(document["source_root"])
    source = directory / "source"
    source.mkdir(mode=0o700)
    identity = snapshot(root, source)
    if regression is not None:
        if profile == "frontend" or len(regression) > 64000:
            raise BotClientError("additional Python regression tests need a Python profile and at most 64000 bytes")
        filename = "tests/test_developer_regression.py"
        if (source / filename).exists():
            raise BotClientError("the reserved regression filename already exists in tracked source")
        (source / filename).write_bytes(regression)
        identity["regression_sha256"] = hashlib.sha256(regression).hexdigest()
        identity["snapshot_sha256"] = hashlib.sha256(bytes.fromhex(identity["snapshot_sha256"]) + regression).hexdigest()
    arguments = sandbox(root, source, profile, node_executable=document.get("node_executable"))
    if regression is not None:
        arguments.append(filename)
    return {"repository": document["repository"], "profile": profile, **identity}, arguments


def execute(identity, arguments, receipt):
    write_document(receipt, {"format": 1, **identity, "status": "outcome_unknown", "started_at": int(time.time())}, new=True)
    # Even worst-case JSON escaping fits the private document's 256 KiB allowance.
    result = bounded_execute(arguments, timeout=600, maximum=32 * 1024)
    status = "passed" if result["exit_code"] == 0 and not result["timed_out"] and not result["truncated"] else "failed"
    retained = {"format": 1, **identity, **result, "status": status, "finished_at": int(time.time())}
    write_document(receipt, retained)
    # Test output is untrusted and can contain fixtures or secrets. It stays private on the dev box.
    failed_tests = re.findall(r"^FAILED (tests/[A-Za-z0-9_/]+\.py::[A-Za-z0-9_:]+)(?:\[| -|$)",
                             result["output"], flags=re.MULTILINE)[:20]
    return {**identity, "status": status, "exit_code": result["exit_code"], "timed_out": result["timed_out"],
            "truncated": result["truncated"], "failed_tests": failed_tests, "output_retained_locally": True}


def register(gateway, registry, path, context):
    from ..agents.tools import ToolDefinition

    if not path or not context.get("developer", {}).get("enabled") or not gateway.local_tools:
        return
    document = policy(path)
    if document["repository"] not in context["developer"]["repositories"]:
        return
    def authorize():
        grant = gateway.request("/developer")
        if not grant.get("enabled") or document["repository"] not in grant.get("repositories", []):
            raise BotClientError("developer repository permission changed")

    def source_read(path, offset=0):
        authorize()
        root = Path(document["source_root"])
        if not included(path) or path not in _git(root, "ls-files", "-z").decode().split("\0"):
            raise BotClientError("choose an exact tracked development source file, not private state")
        with workspace_open(root, path, maximum=MAX_FILE) as descriptor, os.fdopen(descriptor, "rb", closefd=False) as file:
            body = file.read(MAX_FILE + 1)
        try:
            text = body.decode()
        except UnicodeError:
            raise BotClientError("development source reading accepts UTF-8 text only") from None
        return {"path": path, "source_revision": _git(root, "rev-parse", "HEAD").decode().strip(),
                "sha256": hashlib.sha256(body).hexdigest(), "offset": offset,
                "text": text[offset:offset + 12000], "more": len(text) > offset + 12000}

    registry.register(ToolDefinition("development_source_read", "Read a bounded exact tracked source file in the "
        "owner-configured development checkout. Private state, ignored exports and symlinks are not exposed.",
        {"type": "object", "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 300},
         "offset": {"type": "integer", "minimum": 0, "maximum": MAX_FILE}}, "required": ["path"],
         "additionalProperties": False}, source_read, risk="read"))

    def run(profile, test_path=None):
        import secrets

        # Reauthorize the current grant; tool availability cached by the CLI cannot grant execution.
        authorize()
        regression = None
        if test_path is not None:
            if not test_path.endswith(".py"):
                raise BotClientError("the additional regression must be a Python test in this topic's workspace")
            with workspace_open(gateway.workspace, test_path, maximum=64000) as descriptor:
                regression = os.read(descriptor, 64001)
        with protected_temporary_directory("reasoning-dev-test-") as directory:
            identity, arguments = prepare(document, profile, directory, regression=regression)
            command = ["reasoning-dev-test", document["repository"], profile, identity["source_revision"], identity["snapshot_sha256"]]
            approval = gateway.request("/executions", command=command)
            while approval["status"] == "pending":
                gateway.request("/progress", phase="waiting_approval", activity="waiting_approval")
                time.sleep(2)
                approval = gateway.request(f"/executions/{approval['id']}/status")
            if approval["status"] != "approved":
                return {"refused": "The owner did not approve this exact source snapshot and test profile."}
            authorize()
            consumed = gateway.request(f"/executions/{approval['id']}/consume")
            if json.loads(consumed["command_json"]) != command:
                raise BotClientError("test consent differs from the frozen profile and source snapshot")
            receipt = Path(gateway.receipt_directory) / ("developer-test-" + secrets.token_hex(16) + ".json")
            gateway.request("/progress", phase="using_tools", activity="running_compute")
            return {**execute(identity, arguments, receipt), "approval_id": approval["id"]}
    registry.register(ToolDefinition("development_test", "Run an owner-approved offline test profile on the development box, "
        "against a frozen tracked-source snapshot. Results report commit, snapshot hash and outcome; raw output stays private.",
        {"type": "object", "properties": {"profile": {"enum": document["profiles"]},
         "test_path": {"type": "string", "minLength": 1, "maxLength": 300}}, "required": ["profile"],
         "additionalProperties": False}, run, risk="write", idempotent=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--repository", required=True)
    configure.add_argument("--source-root", type=Path, default=Path.cwd())
    run = commands.add_parser("test")
    run.add_argument("--profile", choices=tuple(PROFILES), default="smoke")
    run.add_argument("--yes", action="store_true", help="explicit local consent for unattended offline testing")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    try:
        args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = args.state_dir / "developer.json"
        if args.command == "configure":
            if not REPOSITORY.fullmatch(args.repository):
                raise BotClientError("provide an exact GitHub owner/repository")
            root = args.source_root.resolve(strict=True)
            if not (root / "src/reasoning_ledger/server/api.py").is_file():
                raise BotClientError("configure from the trusted local Reasoning checkout")
            write_document(path, {"format": 1, "repository": args.repository,
                                  "source_root": str(root), "profiles": list(PROFILES),
                                  "node_executable": shutil.which("node")}, new=True)
            bot_panel("REASONING · DEVELOPER BOT", [("Repository", args.repository), ("Source", root),
                ("Policy", path), ("Next", "Enable Developer tools and local execution in My bots; restart make bot-run")])
            return 0
        document = policy(path)
        if args.command == "list":
            print(json.dumps({"repository": document["repository"], "profiles": document["profiles"]}))
            return 0
        with protected_temporary_directory("reasoning-dev-test-") as directory:
            identity, arguments = prepare(document, args.profile, directory)
            bot_panel("REASONING · DEVELOPMENT TEST", [(key.replace("_", " "), value) for key, value in identity.items()])
            if not args.yes and input("Run this isolated offline test profile? [y/N] ").strip().lower() != "y":
                return 0
            import secrets

            receipt = args.state_dir / ("developer-test-" + secrets.token_hex(16) + ".json")
            result = execute(identity, arguments, receipt)
            bot_panel("REASONING · TEST RESULT", [("Status", result["status"]), ("Exit", result["exit_code"]),
                                                   ("Private output", receipt)])
            return 0 if result["status"] == "passed" else 1
    except (OSError, ValueError, subprocess.SubprocessError, BotClientError, InstallError) as error:
        message = str(error) if isinstance(error, BotClientError) else "local developer test setup or receipt is invalid"
        parser.exit(2, f"reasoning-dev-test: {message}\n")


if __name__ == "__main__":
    raise SystemExit(main())
