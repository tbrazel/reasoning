"""Explicit, model-aware reasoning effort validation and adapter settings.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from .store import StoreError

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def checked(value):
    if value is not None and value not in EFFORTS:
        raise StoreError(400, "select an advertised reasoning effort or provider default")
    return value


def openrouter_efforts(model):
    reasoning = model.get("reasoning")
    if not isinstance(reasoning, dict) or "supported_efforts" not in reasoning:
        return []
    levels = reasoning["supported_efforts"]
    if levels is None:
        levels = EFFORTS
    if not isinstance(levels, (list, tuple)):
        return []
    return [level for level in EFFORTS if level in levels and not (level == "none" and reasoning.get("mandatory"))]


def cli_efforts(engine, model):
    # Only expose levels from the installed offline Codex catalog. This never
    # refreshes account metadata, invokes inference or changes user configuration.
    if engine == "codex":
        catalog = codex_catalog()
        if model == "default" and catalog:
            return [level for level in ("low", "medium", "high", "xhigh", "max")
                    if all(level in levels for levels in catalog.values())]
        return catalog.get(model, [])
    if engine == "claude" and (model == "default" or any(name in model for name in ("opus", "sonnet", "fable"))):
        return ["low", "medium", "high"]
    return []


def codex_catalog():
    from functools import lru_cache

    # Kept on the function so repeated runner heartbeats share one bounded probe.
    if not hasattr(codex_catalog, "cached"):
        codex_catalog.cached = lru_cache(maxsize=1)(_codex_catalog)
    return codex_catalog.cached()


def _codex_catalog():
    import json
    import subprocess

    try:
        result = subprocess.run(["codex", "debug", "models", "--bundled"], capture_output=True, timeout=10)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            return {}
        models = json.loads(result.stdout).get("models", [])
        if not isinstance(models, list):
            return {}
        return {model["slug"]: [level for level in ("low", "medium", "high", "xhigh", "max")
                if any(isinstance(item, dict) and item.get("effort") == level
                       for item in model.get("supported_reasoning_levels", []))]
                for model in models if isinstance(model, dict) and isinstance(model.get("slug"), str)}
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return {}
