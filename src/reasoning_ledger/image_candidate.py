"""Validate the actual hosted run selected for exact-archive image publication.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import argparse
import re
from pathlib import Path

from .image_release import json_body


def validate_run(value, revision):
    if (not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision)
            or value.get("head_sha") != revision or value.get("status") != "completed"
            or value.get("conclusion") != "success" or value.get("event") != "workflow_dispatch"
            or value.get("path") != ".github/workflows/compute-images.yml"
            or type(value.get("id")) is not int or value["id"] <= 0
            or not isinstance(value.get("repository"), dict) or not isinstance(value.get("head_repository"), dict)
            or not value["repository"].get("id")
            or value["repository"]["id"] != value["head_repository"].get("id")):
        raise ValueError("candidate must come from the exact successful manual image workflow in this repository")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    try:
        if args.run.stat().st_size > 1024**2:
            raise ValueError("candidate run receipt exceeds its allowance")
        validate_run(json_body(args.run.read_bytes()), args.source_revision)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"image candidate refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
