"""Explicitly configured optional Desktop provider and browser wiring."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from .cloud_client import CloudClientContractError
from .desktop_auth import (
    DesktopAuthenticationController,
    DesktopAuthenticationError,
    DesktopAuthenticationStageError,
    DesktopOidcConfiguration,
)
from .desktop_auth_browser import DesktopCallbackFailure, SecureDesktopAuthEntropy, SystemBrowserAuthorizationLauncher
from .desktop_cloud import DesktopCloudController
from .desktop_cloud_http import DesktopCloudHttpBoundaryFactory, DesktopCloudHttpConfiguration, DesktopCloudHttpError
from .desktop_cognito_authority import DesktopCognitoAuthority, DesktopCognitoScope
from .desktop_cognito_tokens import CognitoDesktopTokenClient, CognitoDesktopTokenError
from .desktop_live_profile import load_desktop_live_profile
from .desktop_os_credential_store import DesktopOsCredentialStore

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesktopLiveRuntime:
    auth: DesktopAuthenticationController
    browser: SystemBrowserAuthorizationLauncher
    tokens: CognitoDesktopTokenClient

    def close(self) -> None:
        self.browser.close()
        self.tokens.close()


def _callback_failure(error: Exception) -> DesktopCallbackFailure:
    """Expose only a fixed failure category, never an exception or token."""

    if isinstance(error, CognitoDesktopTokenError):
        return DesktopCallbackFailure(error.diagnostic_code)
    if isinstance(error, DesktopCloudHttpError):
        if str(error) == "Desktop Cloud service returned HTTP 401":
            return DesktopCallbackFailure("hosted_unauthorized")
        if str(error) == "Desktop Cloud service returned HTTP 403":
            return DesktopCallbackFailure("hosted_forbidden")
        return DesktopCallbackFailure("hosted_api")
    if isinstance(error, CloudClientContractError):
        return DesktopCallbackFailure("hosted_contract")
    if isinstance(error, DesktopAuthenticationStageError):
        return DesktopCallbackFailure(error.diagnostic_code)
    if isinstance(error, DesktopAuthenticationError):
        code = "credential_store" if str(error).startswith("Desktop OS credential") else "desktop_auth"
        return DesktopCallbackFailure(code)
    return DesktopCallbackFailure("internal")


def _log_callback_failure(error: Exception, failure: DesktopCallbackFailure) -> None:
    """Write a useful local diagnostic without retaining exception content."""

    _LOG.error(
        "Desktop OAuth callback failed: category=%s exception_type=%s.%s",
        failure.code,
        type(error).__module__,
        type(error).__qualname__,
    )


def build_desktop_live_runtime(cloud: DesktopCloudController) -> DesktopLiveRuntime:
    profile = load_desktop_live_profile()
    endpoint = DesktopCloudHttpConfiguration(profile.api_origin)
    tokens = CognitoDesktopTokenClient(
        user_pool_id=profile.user_pool_id,
        client_id=profile.desktop_client_id,
        login_origin=profile.login_origin,
    )
    try:
        store = DesktopOsCredentialStore()
        authority = DesktopCognitoAuthority(
            tokens,
            endpoint,
            DesktopCognitoScope(
                account_id=profile.account_id,
                user_id=profile.user_id,
                tenant_id=profile.tenant_id,
                entitlement_version=profile.entitlement_version,
            ),
            store,
        )
        configuration = DesktopOidcConfiguration(
            issuer=tokens.issuer,
            client_id=tokens.client_id,
            audience=tokens.client_id,
            redirect_uri=profile.redirect_uri,
            tenant_id=profile.tenant_id,
        )
        holder: dict[str, DesktopAuthenticationController] = {}

        def callback(value) -> None:
            try:
                holder["controller"].accept_callback(value)
            except Exception as error:
                failure = _callback_failure(error)
                # The exception message and traceback can retain request or
                # credential material.  Record only the fixed public category
                # and Python exception type in Desktop's local backend log.
                _log_callback_failure(error, failure)
                holder["controller"].record_callback_failure(failure.code)
                raise failure from None

        browser = SystemBrowserAuthorizationLauncher(profile.login_origin + "/oauth2/authorize", callback)
        controller = DesktopAuthenticationController(
            configuration,
            authority=authority,
            browser=browser,
            credential_store=store,
            provider_factory=DesktopCloudHttpBoundaryFactory(endpoint, authority),
            cloud=cloud,
            entropy=SecureDesktopAuthEntropy(),
            clock=lambda: datetime.now(UTC),
        )
        holder["controller"] = controller
        return DesktopLiveRuntime(controller, browser, tokens)
    except Exception:
        tokens.close()
        raise


__all__ = ["DesktopLiveRuntime", "build_desktop_live_runtime"]
