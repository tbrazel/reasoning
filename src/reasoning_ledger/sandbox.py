"""Filesystem isolation for codex processes via bubblewrap.

Codex agents run arbitrary shell commands, and codex's own sandbox
restricts *writes*, not reads — an agent shell can read anything the
workbench user can. This wraps codex in a bwrap namespace that shows it
a whitelist: the system toolchain, the executable's own install
directory (wherever PATH resolution found it), codex's home, and only
the paths a given codex process legitimately needs, and nothing else —
no other projects, no repo source, no credentials, no other homes.

Codex's home is workbench-owned: unless CODEX_HOME overrides it, the
state (auth, config, chat rollouts) lives at <workbench root>/codex
outside the namespace and is bound at ~/.codex inside, where codex
expects it. When no namespace is available to provide that bind,
codex_env() points the spawned codex at the same home via CODEX_HOME,
so the state location never depends on bwrap. The user's real ~/.codex
— their personal codex CLI state — is never exposed to workbench
agents.

Two profiles:

- The **app-server** must reach this project's ledger (its MCP child
  reads it) and the reasoning venv (to run that child), so those stay
  visible; the isolation is from everything *outside* the project. Codex is
  told to execute directly inside this namespace so it does not try to create
  a nested Linux sandbox; its apparent danger-full-access mode cannot escape
  the outer mounts.
- The **reviewer** (`codex exec`) needs none of that — only the system,
  codex's home, and its own temp scratch — so the ledger is genuinely
  invisible to it.

If bwrap is missing or a namespace cannot be created (user namespaces
disabled), `wrap` returns the command unchanged after warning once;
codex then runs as before. Set REASONING_SANDBOX=off to skip bwrap.

On hosts whose toolchain lives outside the standard root directories
(e.g. NixOS, where everything resolves into /nix/store), the
whitelist is extensible without code changes: REASONING_SANDBOX_RO and
REASONING_SANDBOX_RW are colon-separated path lists added read-only /
writable to every sandboxed process (and to the availability probe).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# Standard root dirs a toolchain expects. Symlinked ones (e.g. /bin ->
# usr/bin) are recreated as symlinks so lookups resolve inside the ns.
_SYSTEM_ROOTS = ("/usr", "/etc", "/opt", "/bin", "/sbin", "/lib", "/lib32", "/lib64")

# System state some toolchains keep outside those roots. TeX Live on
# Debian generates its format files (pdflatex.fmt etc.) into
# /var/lib/texmf and caches fonts under /var/cache/fonts — without them
# pdflatex dies with "can't find the format file". Read-only and
# -try-bound: absent on hosts without TeX, harmless everywhere else.
_SYSTEM_STATE = ("/var/lib/texmf", "/var/cache/fonts")

_probe_lock = threading.Lock()
_probe_result: bool | None = None


def _warn(message: str) -> None:
    print(f"[reasoning-ledger sandbox] {message}", file=sys.stderr, flush=True)


def enabled() -> bool:
    return os.environ.get("REASONING_SANDBOX", "bwrap").lower() != "off"


def _env_paths(var: str) -> list[Path]:
    """Extra bind paths from a colon-separated env list."""
    return [
        Path(entry).expanduser()
        for entry in os.environ.get(var, "").split(":")
        if entry
    ]


def available() -> bool:
    """Whether bwrap exists and can create a namespace here (cached)."""
    global _probe_result
    with _probe_lock:
        if _probe_result is not None:
            return _probe_result
        result = False
        true_bin = os.path.realpath(shutil.which("true") or "/usr/bin/true")
        if shutil.which("bwrap"):
            try:
                # Probe with the SAME mounts wrap() uses — the system roots,
                # the host's extra binds, and the probe binary's own install
                # dir (the dynamic loader and the binary must be present, or
                # exec fails with a misleading ENOENT) — plus every
                # namespace wrap() unshares.
                extra: list[str] = []
                for path in _env_paths("REASONING_SANDBOX_RO"):
                    extra += ["--ro-bind-try", str(path), str(path)]
                true_dir = os.path.dirname(true_bin)
                probe = subprocess.run(
                    [
                        "bwrap",
                        *_system_binds(),
                        *extra,
                        "--ro-bind-try", true_dir, true_dir,
                        "--proc", "/proc",
                        "--dev", "/dev",
                        "--unshare-user",
                        "--unshare-pid",
                        "--unshare-ipc",
                        "--unshare-uts",
                        true_bin,
                    ],
                    capture_output=True,
                    timeout=15,
                )
                result = probe.returncode == 0
            except (OSError, subprocess.SubprocessError):
                result = False
        if not result:
            _warn(
                "bwrap unavailable or user namespaces disabled — codex will "
                "run WITHOUT filesystem isolation. Set REASONING_SANDBOX=off "
                "to silence this."
            )
        _probe_result = result
        return result


def _resolv_target(resolv: Path = Path("/etc/resolv.conf")) -> Path | None:
    """Where to mount so a symlinked resolv.conf resolves in-namespace:
    its first symlink hop, when that leads outside its own directory.
    Resolver managers park the real file in different places
    (systemd-resolved and NetworkManager under /run, resolvconf under
    /run or /var/run), sometimes behind further symlinks — mounting at
    the first hop resolves any deeper hops on the host side, so one
    bind covers them all. None when no mount is needed (a regular file,
    or a target the directory's own bind already covers)."""
    if not resolv.is_symlink():
        return None
    target = Path(os.readlink(resolv))
    if not target.is_absolute():
        target = resolv.parent / target
    target = Path(os.path.normpath(target))
    if str(target).startswith(f"{resolv.parent}{os.sep}"):
        return None
    return target


def _system_binds() -> list[str]:
    # All generated binds use the -try variants: a source that is absent
    # on this host (or vanishes) skips the mount instead of failing the
    # whole namespace, so layout differences never break codex startup.
    args: list[str] = []
    for root in _SYSTEM_ROOTS:
        path = Path(root)
        if path.is_symlink():
            args += ["--symlink", os.readlink(path), root]
        elif path.exists():
            args += ["--ro-bind-try", root, root]
    for state in _SYSTEM_STATE:
        args += ["--ro-bind-try", state, state]
    # /etc is ro-bound whole, so a symlinked resolv.conf cannot be bound
    # over — expose its target instead so DNS keeps working. The
    # resolver it names stays reachable — net is not unshared.
    resolv_mount = _resolv_target()
    if resolv_mount is not None:
        args += ["--ro-bind-try", str(resolv_mount), str(resolv_mount)]
    return args


Bind = Path | tuple[Path, Path]


def wrap(
    command: list[str],
    *,
    rw: list[Bind] | None = None,
    ro: list[Bind] | None = None,
    chdir: Path | None = None,
    network: bool = True,
) -> list[str]:
    """Prefix `command` with a bwrap invocation exposing only the given
    paths (plus the system toolchain, the executable's own install
    directory, a private /tmp, and an ephemeral home). A bind is either
    a path mounted at itself or an (outside source, inside mount point)
    pair — how codex's workbench-owned home appears at ~/.codex.
    Returns `command` unchanged when sandboxing is off or bwrap is
    unusable. Missing sources are skipped so a not-yet-created dir never
    breaks startup."""
    if not enabled() or not available():
        return command

    home = str(Path.home())
    args: list[str] = [
        "bwrap",
        "--tmpfs", "/tmp",
        "--tmpfs", home,  # ephemeral, empty home: hides creds and dotfiles
    ]
    args += _system_binds()
    seen: set[str] = set()

    def bind(entries: list[Bind], flag: str) -> None:
        for entry in entries:
            source, dest = entry if isinstance(entry, tuple) else (entry, entry)
            source, dest = str(Path(source)), str(Path(dest))
            if dest not in seen and Path(source).exists():
                args.extend([flag, source, dest])
                seen.add(dest)

    bind(list(ro or []), "--ro-bind-try")
    bind(list(rw or []), "--bind-try")
    # Host-specific extra binds, mounted after the profile's own so they
    # win for anything nested inside them (nonstandard toolchain layouts
    # — see the module docstring).
    bind(_env_paths("REASONING_SANDBOX_RO"), "--ro-bind-try")
    bind(_env_paths("REASONING_SANDBOX_RW"), "--bind-try")
    # The executable itself must be visible inside the namespace: a PATH
    # entry is often a symlink into an install tree that no other mount
    # exposes (the real home hides behind the tmpfs). Exec the resolved
    # binary and expose its install directory read-only, wherever it
    # lives. Mounted last so no broader bind can hide it — in particular
    # the ~/.codex rebind, which mounts over the very tree the
    # standalone installer keeps the binary in; mounting after it nests
    # the install dir back into view at its real path.
    if os.path.isabs(command[0]):
        real = Path(os.path.realpath(command[0]))
        if real.exists():
            mount = str(_executable_mount(real))
            if mount not in seen:
                args += ["--ro-bind-try", mount, mount]
                seen.add(mount)
            command = [str(real), *command[1:]]
    if not network:
        args.append("--unshare-net")
    args += [
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
    ]
    if chdir is not None and Path(chdir).exists():
        args += ["--chdir", str(Path(chdir))]
    return [*args, *command]


def workbench_root() -> Path:
    """The workbench data root (projects, chats, codex state)."""
    root = os.environ.get("REASONING_WORKBENCH_ROOT")
    if root:
        return Path(root).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(data_home).expanduser() / "reasoning"


def codex_home_bind() -> tuple[Path, Path]:
    """(outside source, inside mount point) for codex's home. With
    CODEX_HOME set, codex uses that path on both sides. Otherwise the
    workbench owns codex's state: it lives at <workbench root>/codex
    outside and mounts at ~/.codex inside the namespace, where codex
    expects it — so the user's own ~/.codex (their personal codex CLI
    state) stays invisible to workbench agents, and everything codex
    writes (auth refreshes, chat rollouts) lands under the workbench
    root. The source dir is created here so a fresh install binds an
    empty home rather than silently skipping the mount."""
    env = os.environ.get("CODEX_HOME")
    if env:
        path = Path(env).expanduser()
        return path, path
    source = workbench_root() / "codex"
    source.mkdir(parents=True, exist_ok=True)
    return source, Path.home() / ".codex"


def codex_env() -> dict[str, str] | None:
    """Environment for spawning codex. None (inherit unchanged) when
    the bwrap namespace will mount the workbench-owned home at ~/.codex;
    otherwise a copy of the environment with CODEX_HOME pointing at the
    workbench-owned home directly — codex state lands in the same place
    whether or not sandboxing is available."""
    if enabled() and available():
        return None
    source, _ = codex_home_bind()
    env = dict(os.environ)
    env["CODEX_HOME"] = str(source)
    return env


def _executable_mount(real: Path) -> Path:
    """The directory to expose so the resolved executable runs inside
    the namespace: its own directory — siblings included, since codex
    spawns codex-code-mode-host from its install dir — or the whole npm
    package when it lives under node_modules (a JS launcher loads
    vendored platform binaries from sibling directories)."""
    parts = real.parts
    if "node_modules" in parts[:-1]:
        i = parts.index("node_modules")
        depth = i + (3 if parts[i + 1].startswith("@") else 2)
        if depth < len(parts):
            return Path(*parts[:depth])
    return real.parent
