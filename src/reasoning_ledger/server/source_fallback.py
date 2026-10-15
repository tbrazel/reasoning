"""Bounded primary-source fallback when official arXiv HTML is unavailable.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from dataclasses import replace

from ..agents.scientific import ScientificToolError
from .research_network import SourceError


def register(registry):
    original = registry.get("arxiv_read")

    def read(arxiv_id, queries, context_characters=1800):
        try:
            return original.handler(arxiv_id=arxiv_id, queries=queries, context_characters=context_characters)
        except (ScientificToolError, SourceError, OSError, TimeoutError):
            listing = registry.get("arxiv_source_list").handler(arxiv_id=arxiv_id)
            files = [item for item in listing["data"]["files"] if item["path"].endswith(".tex") and item.get("text_readable")]
            files.sort(key=lambda item: (-item.get("bytes", 0), item["path"]))
            if not files:
                raise ScientificToolError("arXiv HTML unavailable and no readable TeX source is present") from None
            result = registry.get("arxiv_source_read").handler(arxiv_id=arxiv_id, path=files[0]["path"],
                queries=queries, context_characters=context_characters)
            result["fallback"] = {"requested": "official_html", "used": "primary_tex", "path": files[0]["path"],
                                  "coverage": "Selected source member only; use source_list/read for other files."}
            return result

    # Register into the owning registry, without changing public tool schemas.
    registry._tools[original.name] = replace(original, handler=read)
