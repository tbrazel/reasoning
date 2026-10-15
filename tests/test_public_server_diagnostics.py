"""Real child-process diagnostics classify SSH failures without printing secrets."""

import sys

import pytest

from reasoning_ledger.deployment import host, server_diagnostics


@pytest.mark.parametrize("diagnostic,expected", [
    (b"Permission denied (publickey).", "SSH authentication failed"),
    (b"sign_and_send_pubkey: signing failed: agent refused operation", "SSH authentication failed"),
    (b"Host key verification failed.", "SSH host-key verification failed"),
    (b"REMOTE HOST IDENTIFICATION HAS CHANGED!", "Do not disable host-key checking"),
    (b"WARNING: UNPROTECTED PRIVATE KEY FILE!", "mode 0600"),
    (b"Load key: invalid format", "operator private key"),
    (b"connect: Connection timed out", "could not reach"),
    (b"connect: Connection refused", "could not reach"),
    (b"Could not resolve hostname", "could not reach"),
    (b"python3: not found", "system Python 3"),
    (b"unrecognized SSH failure", "exit 255"),
])
def test_ssh_failure_guidance_is_allowlisted_not_raw_upstream_text(diagnostic, expected):
    error = server_diagnostics._failure(
        ["ssh", "-i", "/secure/operator key"], diagnostic + b"\nSECRET sk-or-fixture-private-header", returncode=255
    )
    assert expected in str(error)
    assert "SECRET" not in str(error) and "sk-or-fixture" not in str(error)
    if "authentication" in expected:
        assert "ssh-add -t 1h '/secure/operator key'" in str(error)
        assert "BatchMode=yes" in str(error)


def test_early_stdin_close_drains_actual_ssh_reason_and_reaps_child():
    program = "import os, sys; os.close(0); sys.stderr.write('Permission denied (publickey). SECRET'); sys.exit(255)"
    with pytest.raises(host.InstallError, match="SSH authentication failed") as error:
        server_diagnostics._run([sys.executable, "-c", program], b"# verified input\n" * 100000, timeout=3)
    assert "SECRET" not in str(error.value)


def test_large_stderr_is_drained_bounded_and_does_not_pollute_binary_stdout():
    script = b"import sys\nsys.stderr.write('SECRET ' * 50000)\nsys.stdout.buffer.write(b'\\x00\\x01{}')\n"
    result = server_diagnostics._run([sys.executable, "-I", "-S", "-B", "-"], script, timeout=3)
    assert result.stdout == b"\x00\x01{}" and result.stderr is None


def test_remote_permission_exception_is_not_mislabeled_as_ssh_authentication():
    error = server_diagnostics._failure(["ssh"], b"PermissionError: [Errno 13] Permission denied: SECRET", returncode=1)
    assert "Remote verification failed (exit 1)" in str(error)
    assert "SECRET" not in str(error)


def test_early_success_exit_still_refuses_incomplete_verification_input():
    program = "import os, sys; os.close(0); sys.stdout.write('{}'); sys.exit(0)"
    with pytest.raises(host.InstallError, match="complete input"):
        server_diagnostics._run([sys.executable, "-c", program], b"# verified input\n" * 100000, timeout=3)
