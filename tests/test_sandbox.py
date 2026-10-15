"""Tests for the bwrap filesystem-isolation helper."""

import subprocess
from pathlib import Path

import pytest

from reasoning_ledger import sandbox


class TestWrapConstruction:
    def test_off_returns_command_unchanged(self, monkeypatch):
        monkeypatch.setenv("REASONING_SANDBOX", "off")
        cmd = ["codex", "app-server"]
        assert sandbox.wrap(cmd, rw=[Path("/x")], ro=[Path("/y")]) == cmd

    def test_wrap_binds_the_given_paths(self, monkeypatch, tmp_path):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        rw = tmp_path / "rw"
        rw.mkdir()
        ro = tmp_path / "ro"
        ro.mkdir()
        wrapped = sandbox.wrap(["true"], rw=[rw], ro=[ro], chdir=rw)
        assert wrapped[0] == "bwrap"
        assert "--bind-try" in wrapped and str(rw) in wrapped
        assert "--ro-bind-try" in wrapped and str(ro) in wrapped
        assert "--die-with-parent" in wrapped
        # the ephemeral home hides real dotfiles
        assert "--tmpfs" in wrapped and str(Path.home()) in wrapped

    def test_missing_paths_are_skipped(self, monkeypatch, tmp_path):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        ghost = tmp_path / "does-not-exist"
        wrapped = sandbox.wrap(["true"], rw=[ghost])
        assert str(ghost) not in wrapped

    def test_network_can_be_unshared(self, monkeypatch):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        assert "--unshare-net" in sandbox.wrap(["true"], network=False)
        assert "--unshare-net" not in sandbox.wrap(["true"])

    def test_env_lists_add_extra_binds(self, monkeypatch, tmp_path):
        """REASONING_SANDBOX_RO/RW let a nonstandard host (e.g. NixOS)
        widen the whitelist without code changes."""
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        store = tmp_path / "store"
        store.mkdir()
        (store / "marker").write_text("toolchain\n")
        state = tmp_path / "state"
        state.mkdir()
        ghost = tmp_path / "ghost"
        monkeypatch.setenv("REASONING_SANDBOX_RO", f"{store}:{ghost}")
        monkeypatch.setenv("REASONING_SANDBOX_RW", str(state))

        wrapped = sandbox.wrap(["/bin/sh", "-c", "exit 0"])
        i = wrapped.index(str(store))
        assert wrapped[i - 1] == "--ro-bind-try"
        j = wrapped.index(str(state))
        assert wrapped[j - 1] == "--bind-try"
        assert str(ghost) not in wrapped  # missing entries skipped

        script = (
            f"cat {store}/marker; "
            f"touch {store}/w 2>/dev/null && echo ro-writable; "
            f"touch {state}/w && echo rw-writable"
        )
        out = subprocess.run(
            sandbox.wrap(["/bin/sh", "-c", script]),
            capture_output=True, text=True, timeout=30,
        ).stdout
        assert "toolchain" in out
        assert "ro-writable" not in out
        assert "rw-writable" in out


class TestCodexHome:
    def test_codex_home_is_workbench_owned(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "wb"))
        source, inside = sandbox.codex_home_bind()
        assert source == tmp_path / "wb" / "codex"
        assert source.is_dir()  # created, so the bind never silently skips
        assert inside == Path.home() / ".codex"

    def test_codex_home_env_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
        assert sandbox.codex_home_bind() == (tmp_path / "ch", tmp_path / "ch")

    def test_wrap_mounts_pairs_at_their_inside_path(self, monkeypatch, tmp_path):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "auth.json").write_text("{}\n")
        inside = Path.home() / ".codex"

        wrapped = sandbox.wrap(["/bin/sh", "-c", "ls ~/.codex"], rw=[(outside, inside)])
        i = wrapped.index(str(outside))
        assert wrapped[i - 1] == "--bind-try" and wrapped[i + 1] == str(inside)
        out = subprocess.run(wrapped, capture_output=True, text=True, timeout=30)
        assert "auth.json" in out.stdout  # outside content, inside path

    def test_codex_env_points_home_when_unsandboxed(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "wb"))
        monkeypatch.setenv("REASONING_SANDBOX", "off")
        env = sandbox.codex_env()
        assert env is not None
        assert env["CODEX_HOME"] == str(tmp_path / "wb" / "codex")

    def test_codex_env_inherits_when_the_namespace_binds(self, monkeypatch):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        assert sandbox.codex_env() is None


class TestResolvTarget:
    def test_regular_file_needs_no_mount(self, tmp_path):
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 127.0.0.53\n")
        assert sandbox._resolv_target(resolv) is None

    def test_first_hop_of_a_relative_chain(self, tmp_path):
        # etc/resolv.conf -> ../run/A -> B: mount at the first hop (A);
        # bwrap resolves the deeper hop host-side when mounting there.
        (tmp_path / "etc").mkdir()
        run = tmp_path / "run"
        run.mkdir()
        (run / "B").write_text("nameserver 1.1.1.1\n")
        (run / "A").symlink_to(run / "B")
        resolv = tmp_path / "etc" / "resolv.conf"
        resolv.symlink_to(Path("..") / "run" / "A")
        assert sandbox._resolv_target(resolv) == run / "A"

    def test_target_inside_own_directory_needs_no_mount(self, tmp_path):
        real = tmp_path / "resolv.real"
        real.write_text("nameserver 1.1.1.1\n")
        resolv = tmp_path / "resolv.conf"
        resolv.symlink_to(Path("resolv.real"))
        assert sandbox._resolv_target(resolv) is None

    def test_dangling_symlink_is_still_returned(self, tmp_path):
        # -try binds skip a missing source, so a dangling resolv.conf
        # degrades to no DNS file rather than a failed namespace
        (tmp_path / "etc").mkdir()
        resolv = tmp_path / "etc" / "resolv.conf"
        resolv.symlink_to(tmp_path / "run" / "missing")
        assert sandbox._resolv_target(resolv) == tmp_path / "run" / "missing"


class TestExecutableMount:
    def test_mount_is_the_binary_directory(self):
        real = Path("/opt/vendor/releases/1.2.3/bin/codex")
        assert sandbox._executable_mount(real) == Path(
            "/opt/vendor/releases/1.2.3/bin"
        )

    def test_mount_is_the_npm_package_root(self):
        scoped = Path("/x/lib/node_modules/@openai/codex/bin/codex.js")
        assert sandbox._executable_mount(scoped) == Path(
            "/x/lib/node_modules/@openai/codex"
        )
        plain = Path("/x/node_modules/tool/bin/tool.js")
        assert sandbox._executable_mount(plain) == Path("/x/node_modules/tool")

    def test_wrap_runs_a_binary_from_an_unmounted_install(
        self, monkeypatch, tmp_path
    ):
        """A PATH symlink to a binary in an arbitrary install dir must
        work inside the namespace: wrap execs the realpath and mounts
        its directory (siblings included), with no profile bind
        covering either the symlink or the install."""
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        install = tmp_path / "install" / "bin"
        install.mkdir(parents=True)
        exe = install / "tool"
        exe.write_text('#!/bin/sh\ncat "$(dirname "$0")/sibling.txt"\n')
        exe.chmod(0o755)
        (install / "sibling.txt").write_text("ran with siblings\n")
        pathdir = tmp_path / "pathdir"
        pathdir.mkdir()
        link = pathdir / "tool"
        link.symlink_to(exe)

        wrapped = sandbox.wrap([str(link)])
        assert str(install) in wrapped  # the install dir is mounted...
        assert str(pathdir) not in wrapped  # ...the symlink's dir is not
        assert wrapped[-1] == str(exe)  # and the realpath is what runs
        result = subprocess.run(wrapped, capture_output=True, text=True, timeout=30)
        assert result.stdout == "ran with siblings\n"


class TestIsolation:
    """Actually run a shell inside the sandbox and check what it sees."""

    def _run(self, script, **wrap_kwargs):
        cmd = sandbox.wrap(["/bin/sh", "-c", script], **wrap_kwargs)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def test_whitelisted_paths_visible_others_hidden(self, monkeypatch, tmp_path):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        (allowed / "marker").write_text("visible\n")
        secret = tmp_path / "secret"
        secret.mkdir()
        (secret / "creds").write_text("TOP SECRET\n")

        script = (
            f"cat {allowed}/marker 2>&1; "
            f"echo ---; "
            f"cat {secret}/creds 2>&1"
        )
        out = self._run(script, rw=[allowed], chdir=allowed).stdout
        assert "visible" in out
        assert "TOP SECRET" not in out
        assert "No such file" in out

    def test_writes_confined_to_bound_paths(self, monkeypatch, tmp_path):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        result = self._run(
            f"touch {allowed}/w && echo wrote", rw=[allowed], chdir=allowed
        )
        assert "wrote" in result.stdout
        assert (allowed / "w").exists()

    def test_home_is_ephemeral(self, monkeypatch, tmp_path):
        if not sandbox.available():
            pytest.skip("bwrap unavailable")
        monkeypatch.setenv("REASONING_SANDBOX", "bwrap")
        # a real dotfile in the actual home must not be readable
        out = self._run("ls -a ~ | tr '\\n' ' '").stdout
        assert ".ssh" not in out
        assert ".codex" not in out  # not bound for a bare wrap
