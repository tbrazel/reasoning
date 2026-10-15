"""A narrowly authenticated GitHub publisher separate from the no-network CAS sandbox.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import re
import shutil
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from ..agents.tools import ToolDefinition
from ..deployment.private import read_document, write_document
from .bot_client import BotClientError
from .bot_developer import REPOSITORY, USER


def github_executable(workspace):
    candidate = shutil.which("gh")
    if not candidate:
        return None
    path = Path(candidate).resolve(strict=True)
    if path.is_relative_to(Path(workspace).resolve(strict=True)):
        raise BotClientError("GitHub CLI must be installed outside the bot-controlled workspace")
    return str(path)


def publish_issue(repository, title, body, *, executable=None, directory=None):
    if not REPOSITORY.fullmatch(repository):
        raise BotClientError("invalid exact GitHub repository")
    executable = executable or shutil.which("gh")
    if not executable:
        raise BotClientError("install GitHub CLI and sign in locally before enabling developer bots")
    # Fixed API host/endpoint and stdin JSON: no shell, no arbitrary command, no gh configuration supplied by chat.
    result = subprocess.run([executable, "api", "--hostname", "github.com", "--method", "POST",
                             f"repos/{repository}/issues", "--input", "-", "--jq", ".html_url"],
                            input=json.dumps({"title": title, "body": body}), text=True, capture_output=True,
                            timeout=60, check=False, cwd=directory)
    url = result.stdout.strip()
    if result.returncode or len(result.stdout) > 4096 or not re.fullmatch(
            r"https://github\.com/" + re.escape(repository) + r"/issues/[1-9][0-9]*", url, flags=re.IGNORECASE):
        raise BotClientError("GitHub issue outcome is unknown; inspect GitHub and the retained receipt before retrying")
    return url


def register(gateway, registry, directory, policy):
    if not policy.get("enabled"):
        return
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(ToolDefinition("server_diagnostics", "Read sanitized topic-scoped server counts and states, never logs or secrets.",
                                    schema, lambda: gateway.request("/diagnostics"), risk="read"))
    if directory is None:
        return
    directory = Path(directory).resolve(strict=True)
    executable = github_executable(gateway.workspace)
    properties = {"repository": {"enum": policy["repositories"]}, "title": {"type": "string", "minLength": 1, "maxLength": 200},
                  "body": {"type": "string", "minLength": 1, "maxLength": 16000},
                  "client_id": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "^[A-Za-z0-9_-]+$"}}
    def create(repository, title, body, client_id):
        if executable is None:
            raise BotClientError("install GitHub CLI and sign in locally before requesting issue publication")
        approval = gateway.request("/issues", repository=repository, title=title, body=body, client_id=client_id)
        if not isinstance(approval.get("id"), str) or not USER.fullmatch(approval["id"]):
            raise BotClientError("invalid server issue approval identity")
        path = directory / ("github-issue-" + approval["id"] + ".json")
        if path.exists():
            retained = read_document(path)
            if retained.get("status") == "created":
                gateway.request(f"/issues/{approval['id']}/receipt", issue_url=retained["issue_url"])
                return {"issue_url": retained["issue_url"]}
            raise BotClientError("this issue write has an unknown outcome; never republish automatically")
        while approval["status"] == "pending":
            gateway.request("/progress", phase="waiting_approval", activity="waiting_approval")
            time.sleep(2)
            approval = gateway.request(f"/issues/{approval['id']}/status")
        if approval["status"] == "created":
            return {"issue_url": approval["issue_url"]}
        if approval["status"] != "approved":
            return {"refused": "The owner did not approve this exact GitHub issue, or its outcome is unknown."}
        receipt = gateway.request(f"/issues/{approval['id']}/consume")
        if any(receipt[key] != value for key, value in {"repository": repository, "title": title, "body": body}.items()):
            raise BotClientError("issue consent differs from the exact frozen issue preview")
        write_document(path, {"status": "outcome_unknown", "repository": repository,
                              "document_sha256": receipt["document_sha256"]}, new=True)
        try:
            url = publish_issue(receipt["repository"], receipt["title"], receipt["body"], executable=executable, directory=directory)
        except Exception:
            with suppress(BotClientError):
                gateway.request(f"/issues/{approval['id']}/receipt", unknown=True)
            raise BotClientError("GitHub issue outcome is unknown; inspect GitHub before making another request") from None
        write_document(path, {"status": "created", "repository": repository,
                              "document_sha256": receipt["document_sha256"], "issue_url": url})
        gateway.request(f"/issues/{approval['id']}/receipt", issue_url=url)
        return {"issue_url": url}
    registry.register(ToolDefinition("github_issue_create", "Draft an issue in an allowed repository. The bot owner must approve "
                                    "the exact repository/title/body in People before one authenticated GitHub write.",
                                    {"type": "object", "properties": properties, "required": list(properties),
                                     "additionalProperties": False},
                                    create, risk="write", idempotent=False))
