from __future__ import annotations

import pytest

from reasoning_ledger.cloud_client import CloudClientContractError
from reasoning_ledger.desktop_auth import DesktopAuthenticationError, DesktopAuthenticationStageError
from reasoning_ledger.desktop_auth_browser import DesktopCallbackFailure
from reasoning_ledger.desktop_cloud_http import DesktopCloudHttpError
from reasoning_ledger.desktop_cognito_tokens import CognitoDesktopTokenError
from reasoning_ledger.desktop_live_runtime import _callback_failure, _log_callback_failure


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (CognitoDesktopTokenError("private details", diagnostic_code="token_endpoint_timeout"), "token_endpoint_timeout"),
        (DesktopCloudHttpError("private details"), "hosted_api"),
        (CloudClientContractError("private details"), "hosted_contract"),
        (DesktopAuthenticationStageError("post_auth_boundary"), "post_auth_boundary"),
        (DesktopAuthenticationStageError("desktop_cloud_state"), "desktop_cloud_state"),
        (DesktopAuthenticationStageError("credential_store"), "credential_store"),
        (DesktopAuthenticationError("Desktop OS credential write failed"), "credential_store"),
        (DesktopAuthenticationError("private details"), "desktop_auth"),
        (RuntimeError("private details"), "internal"),
    ],
)
def test_callback_failure_reports_only_a_fixed_public_code(failure: Exception, code: str) -> None:
    diagnostic = _callback_failure(failure)
    assert isinstance(diagnostic, DesktopCallbackFailure)
    assert diagnostic.code == code
    assert "private details" not in str(diagnostic)


def test_callback_failure_log_retains_type_but_not_exception_content(caplog) -> None:
    error = RuntimeError("private code and token material")
    diagnostic = _callback_failure(error)

    _log_callback_failure(error, diagnostic)

    assert "category=internal" in caplog.text
    assert "exception_type=builtins.RuntimeError" in caplog.text
    assert "private code and token material" not in caplog.text
