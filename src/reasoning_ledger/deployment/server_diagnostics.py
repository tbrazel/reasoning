"""Verified-SSH transport for read-only server diagnosis and upgrade planning."""

from __future__ import annotations

import base64
import json
import os
import selectors
import shlex
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from . import host, server_operations


def _failure(args, stderr, *, input_refused=False, returncode=None):
    """Classify bounded SSH diagnostics without exposing raw remote output."""
    detail = stderr.lower()
    identity = None
    if "-i" in args:
        position = args.index("-i") + 1
        if position < len(args):
            identity = args[position]
            if any(ord(character) < 32 or ord(character) == 127 for character in str(identity)):
                identity = None
    if any(text in detail for text in (b"host key verification failed", b"remote host identification has changed")):
        return host.InstallError(
            "SSH host-key verification failed. Preserve the pinned known-hosts file; "
            "verify the deployment identity and server key with an authorized operator. Do not disable host-key checking."
        )
    if any(text in detail for text in (b"unprotected private key", b"bad permissions", b"invalid format", b"error in libcrypto")):
        return host.InstallError(
            "SSH could not use the operator private key. Check its format, ownership, and mode 0600; "
            "keep the existing key and operator state intact."
        )
    if any(text in detail for text in (
        b"permission denied (", b"permission denied, please try again", b"incorrect passphrase",
        b"signing failed", b"agent refused operation",
    )):
        command = "ssh-add -t 1h " + shlex.quote(str(identity)) if identity is not None else "ssh-add -t 1h /path/to/operator-key"
        return host.InstallError(
            "SSH authentication failed before a verified receipt was received. Remote commands use BatchMode=yes and cannot "
            "prompt for the private-key passphrase. In this same terminal, start an SSH agent if needed "
            '(eval "$(ssh-agent -s)"), then run: ' + command + ". Enter the passphrase locally and retry. "
            "If the key is already loaded, check that this operator is authorized on the server."
        )
    if any(text in detail for text in (
        b"connection timed out", b"connection refused", b"no route to host", b"could not resolve hostname", b"connection reset",
    )):
        return host.InstallError(
            "SSH could not reach the deployment server. Check this computer's network/public IP, the server address, "
            "and temporary SSH firewall access; retry with the same operator state."
        )
    if b"python3: not found" in detail or b"python3: command not found" in detail:
        return host.InstallError("Remote verification could not start system Python 3. Check the server's system Python installation.")
    if returncode == 255:
        return host.InstallError(
            "SSH transport failed before a verified receipt was received (exit 255). Check the SSH agent/key, "
            "operator authorization, network, and pinned host trust; retry with the same operator state."
        )
    if input_refused:
        return host.InstallError(
            "The verification session closed before receiving its complete input; no verified receipt was accepted. "
            "Check SSH authentication and system Python on the server, then retry with the same operator state."
        )
    return host.InstallError(
        f"Remote verification failed (exit {returncode}); no verified receipt was accepted. "
        "Check the server's system Python and installation state; retry with the same operator state."
    )


def _run(args, script, *, timeout=45, maximum_output=server_operations.MAX_REPORT):
    """Bound both input and output while retaining a killable owned SSH session."""
    if not 0 < timeout <= 45:
        raise host.InstallError("invalid diagnosis transport deadline")
    if type(maximum_output) is not int or not 1 <= maximum_output <= host.MAX_BUNDLE + server_operations.MAX_REPORT + 4:
        raise host.InstallError("invalid diagnosis output allowance")
    deadline = time.monotonic() + timeout
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as child:
        assert child.stdin is not None and child.stdout is not None and child.stderr is not None
        body = bytearray()
        stderr = bytearray()
        remaining = memoryview(script)
        completed, input_refused = False, False
        try:
            os.set_blocking(child.stdin.fileno(), False)
            os.set_blocking(child.stdout.fileno(), False)
            os.set_blocking(child.stderr.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdin, selectors.EVENT_WRITE)
                selector.register(child.stdout, selectors.EVENT_READ)
                selector.register(child.stderr, selectors.EVENT_READ)
                while selector.get_map():
                    timeout = deadline - time.monotonic()
                    if timeout <= 0:
                        raise host.InstallError("server diagnosis timed out; no host changes were requested")
                    for key, _ in selector.select(min(1, timeout)):
                        if key.fileobj is child.stdin:
                            try:
                                written = os.write(child.stdin.fileno(), remaining[:65536])
                            except BrokenPipeError:
                                # Drain stdout/stderr before reporting the failure:
                                # SSH may have the actual cause waiting on stderr.
                                input_refused = True
                                selector.unregister(child.stdin)
                                child.stdin.close()
                                continue
                            remaining = remaining[written:]
                            if not remaining:
                                selector.unregister(child.stdin)
                                child.stdin.close()
                        elif key.fileobj is child.stdout:
                            block = os.read(child.stdout.fileno(), 65536)
                            body.extend(block)
                            if len(body) > maximum_output:
                                raise host.InstallError("server diagnosis exceeded its report allowance")
                            if not block:
                                selector.unregister(child.stdout)
                        else:
                            block = os.read(child.stderr.fileno(), 65536)
                            stderr.extend(block)
                            del stderr[:-16384]
                            if not block:
                                selector.unregister(child.stderr)
                child.wait(timeout=max(0.01, deadline - time.monotonic()))
                if child.returncode != 0 or input_refused:
                    raise _failure(args, bytes(stderr), input_refused=input_refused, returncode=child.returncode)
                completed = True
                return subprocess.CompletedProcess(args, child.returncode, stdout=bytes(body))
        finally:
            if not completed:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def remote_script(manifest, release, checksum, origin):
    binding = server_operations.release_binding(manifest, release, checksum, origin)
    sources = []
    for module in (host, server_operations):
        body = Path(module.__file__).read_bytes()
        if len(body) > 256 * 1024:
            raise host.InstallError("server diagnosis tooling exceeds its source allowance")
        sources.append(base64.b64encode(body).decode("ascii"))
    request = base64.b64encode(host.canonical({"manifest": manifest, "binding": binding})).decode("ascii")
    # These are trusted local tool sources, not code taken from the installation
    # being diagnosed. No uploaded file, staging directory, or import of the
    # target application's potentially broken environment is required.
    return f"""import base64, json, os, sys, types
if os.geteuid() != 0:
    raise SystemExit('server diagnosis requires root read access')
installer = types.ModuleType('installer')
sys.modules['installer'] = installer
exec(compile(base64.b64decode({sources[0]!r}), '<reasoning-installer>', 'exec'), installer.__dict__)
operations = types.ModuleType('reasoning_server_operations')
sys.modules[operations.__name__] = operations
exec(compile(base64.b64decode({sources[1]!r}), '<reasoning-server-operations>', 'exec'), operations.__dict__)
request = json.loads(base64.b64decode({request!r}))
binding = request['binding']
report = operations.ServerInspector().inspect(request['manifest'], binding['release_id'], binding['bundle_sha256'], binding['public_url'])
print(json.dumps(report, sort_keys=True))
""".encode("ascii")


def diagnose_remote(
    bundle, origin, hostname, identity, known_hosts, *, confirmed_release, command=None, target_bundle=None, confirmed_target=None
):
    from .cli import bundle_snapshot

    if (target_bundle is None) != (confirmed_target is None):
        raise host.InstallError("upgrade planning requires both a target bundle and its confirmed release ID")
    # Snapshot both bundles before networking. An operator replacing an input
    # pathname cannot change what is inspected or bound into the plan.
    with bundle_snapshot(bundle, confirmed_release) as source:
        manifest, release, checksum = host.inspect_bundle(source)
        if target_bundle is not None:
            with bundle_snapshot(target_bundle, confirmed_target) as target:
                next_manifest, next_release, next_checksum = host.inspect_bundle(target)
                report = _diagnose(manifest, release, checksum, origin, hostname, identity, known_hosts, command)
                return server_operations.upgrade_plan(report, manifest, next_manifest, next_release, next_checksum)
        return _diagnose(manifest, release, checksum, origin, hostname, identity, known_hosts, command)


def _diagnose(manifest, release, checksum, origin, hostname, identity, known_hosts, command):
    from .cli import ssh_arguments

    binding = server_operations.release_binding(manifest, release, checksum, origin)
    options = ssh_arguments(hostname, identity, known_hosts)
    args = ["ssh", "-T", *options, "root@" + hostname, shlex.join(["python3", "-I", "-S", "-B", "-"])]
    response = (command or _run)(args, remote_script(manifest, release, checksum, origin))
    try:
        if response.returncode != 0 or not isinstance(response.stdout, bytes) or len(response.stdout) > server_operations.MAX_REPORT:
            raise ValueError

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        value = json.loads(response.stdout, object_pairs_hook=unique)
    except (AttributeError, ValueError, TypeError) as exc:
        raise host.InstallError("server diagnosis did not return a bounded JSON report") from exc
    return server_operations.validate_report(value, binding, manifest["database_schema"])
