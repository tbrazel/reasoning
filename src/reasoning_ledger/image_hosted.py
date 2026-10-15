"""Hosted publication dispatch receipts and anonymous catalog re-verification.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import image_publication as publication
from .deployment.private import locked_directory, write_private
from .image_release import PROFILES, json_body
from .image_review import combine, read_json
from .server.compute_catalog import canonical

PUBLIC_FILES = {"compute-images.json", "publication.json", "source-review.json", "source-review.ed25519",
                "public-acceptance.json", "workflow-request.json"}


def gh(arguments, *, structured=True):
    result = subprocess.run(["gh", *arguments], capture_output=True, timeout=1800, check=False)
    if result.returncode or len(result.stdout) > 4 * 1024**2:
        raise ValueError("hosted publication GitHub request failed; provider/credential output suppressed")
    return json_body(result.stdout) if structured else None


def request_identity(environment):
    value = {"format": 1, "profile": environment["PROFILE"], "source_revision": environment["SOURCE_REVISION"],
             "candidate_run_id": int(environment["CANDIDATE_RUN_ID"]),
             "workflow_run_id": int(environment["GITHUB_RUN_ID"]), "tooling_revision": environment["GITHUB_SHA"],
             "repository": environment["GITHUB_REPOSITORY"], "ref": environment["GITHUB_REF"]}
    if (value["profile"] not in PROFILES or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["source_revision"])
            or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["tooling_revision"])
            or value["candidate_run_id"] <= 0 or value["workflow_run_id"] <= 0
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"])
            or not value["ref"].startswith("refs/heads/") or len(value["ref"]) > 256):
        raise ValueError("invalid hosted publication request identity")
    return value


def publications_input(body, revision):
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
        raise ValueError("select an exact original source revision")
    value = json_body(body.encode())
    if (not 1 <= len(value) <= len(PROFILES) or set(value) - set(PROFILES)
            or any(type(run) is not int or run <= 0 for run in value.values())):
        raise ValueError("publication runs must map supported profiles to exact positive Actions run IDs")
    return value


def validate_publication_run(run, repository):
    if (run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("event") != "workflow_dispatch" or run.get("path") != ".github/workflows/compute-publish.yml"
            or run.get("repository", {}).get("full_name", "").lower() != repository.lower()
            or not run.get("repository", {}).get("id")
            or run.get("repository", {}).get("id") != run.get("head_repository", {}).get("id")):
        raise ValueError("selected publication must be a successful owned publication workflow, not a candidate or fork")


def hosted_combine(revision, publications, trusted_key, output, *, github=gh, environment=None):
    environment = environment or os.environ
    repository = environment["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid hosted repository")
    selected = publications_input(canonical(publications), revision)
    with locked_directory(output) as directory:
        published = directory / "published"
        published.mkdir(mode=0o700, exist_ok=True)
        locations = []
        for profile, run_id in sorted(selected.items()):
            run = github(["api", f"repos/{repository}/actions/runs/{run_id}"])
            validate_publication_run(run, repository)
            if run.get("id") != run_id:
                raise ValueError("publication run identity changed during hosted verification")
            location = published / profile
            location.mkdir(mode=0o700, exist_ok=False)
            github(["run", "download", str(run_id), "--repo", repository,
                    "--name", f"published-{profile}-{revision}", "--dir", str(location)], structured=False)
            if {path.name for path in location.iterdir()} != PUBLIC_FILES:
                raise ValueError("publication artifact contains missing or unexpected/private files")
            for name in PUBLIC_FILES:
                publication.hash_file(location / name, 8 * 1024**2)
            requested = read_json(location / "workflow-request.json")
            fields = {"format", "profile", "source_revision", "candidate_run_id", "workflow_run_id",
                      "tooling_revision", "repository", "ref"}
            if (set(requested) != fields or requested.get("format") != 1 or requested.get("profile") != profile
                    or requested.get("source_revision") != revision
                    or requested.get("workflow_run_id") != run_id or requested.get("tooling_revision") != run.get("head_sha")
                    or requested.get("repository", "").lower() != repository.lower()
                    or requested.get("ref") != "refs/heads/" + run.get("head_branch", "")):
                raise ValueError("downloaded publication request differs from selected exact workflow run")
            audit, _ = publication.load_audit(location / "source-review.json", location / "source-review.ed25519", trusted_key)
            if audit["source_revision"] != revision or audit["profile"] != profile:
                raise ValueError("publication artifacts do not match selected signed original candidates")
            locations.append(location)
        # combine itself checks signed receipts, anonymous source bytes, exact
        # registry digests, fresh unauthenticated pulls, and real worker runs.
        combine(locations, trusted_key, directory / "compute-images.json")
        catalog_sha, _ = publication.hash_file(directory / "compute-images.json", 65536)
        receipt = {"format": 1, "source_revision": revision, "publication_runs": selected,
                   "catalog_sha256": catalog_sha, "anonymous_reverification": True,
                   "workflow_run_id": int(environment["GITHUB_RUN_ID"]),
                   "tooling_revision": environment["GITHUB_SHA"], "repository": repository,
                   "human_review_verified": False, "independent_license_audit_verified": False}
        write_private(directory / "combine.json", canonical(receipt).encode())
        shutil.copyfile(trusted_key, directory / "review-key.pem", follow_symlinks=False)
        (directory / "review-key.pem").chmod(0o600)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("request", "combine"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trusted-key", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "request":
            write_private(args.output, canonical(request_identity(os.environ)).encode())
        else:
            if args.trusted_key is None:
                raise ValueError("select the independently enrolled public trust key")
            hosted_combine(os.environ["SOURCE_REVISION"], publications_input(os.environ["PUBLICATION_RUNS"],
                            os.environ["SOURCE_REVISION"]), args.trusted_key, args.output)
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"hosted compute publication refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
