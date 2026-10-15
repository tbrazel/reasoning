import sys

import pytest

from reasoning_ledger.codex_client import (
    CodexAppServerClient,
    CodexClientError,
    local_codex_runtime_error,
)

EXPECTED_RUNTIME_ERROR = (
    "Codex could not start because a required local runtime library is missing. "
    "Repair or reinstall Codex and its Node.js/Homebrew runtime, then fully quit "
    "and reopen Reasoning."
)


def test_local_codex_runtime_error_hides_dyld_paths(tmp_path):
    stderr_path = tmp_path / "codex.log"
    stderr_path.write_text(
        "dyld[83422]: Library not loaded: /opt/homebrew/opt/llhttp/lib/"
        "libllhttp.9.3.dylib\nReason: tried: /private/secret/path\n",
        encoding="utf-8",
    )

    detail = local_codex_runtime_error(stderr_path)

    assert detail == EXPECTED_RUNTIME_ERROR
    assert "/opt/homebrew" not in detail
    assert "/private/secret" not in detail


def test_initialize_reports_safe_local_runtime_diagnosis(tmp_path):
    stderr_path = tmp_path / "codex.log"
    script = tmp_path / "broken_codex.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('dyld[1]: Library not loaded: /private/missing.dylib\\n')\n",
        encoding="utf-8",
    )
    client = CodexAppServerClient(
        [sys.executable, str(script)],
        on_notification=lambda _message: None,
        on_server_request=lambda _message: None,
        stderr_path=stderr_path,
    )

    with pytest.raises(CodexClientError, match="required local runtime library") as caught:
        client.start(timeout=2)

    assert "/private/missing.dylib" not in str(caught.value)


def test_local_codex_runtime_error_ignores_previous_log_entries(tmp_path):
    stderr_path = tmp_path / "codex.log"
    previous = "dyld[1]: Library not loaded: /private/old.dylib\n"
    stderr_path.write_text(previous + "a different current failure\n", encoding="utf-8")

    assert local_codex_runtime_error(stderr_path, start_offset=len(previous)) is None
