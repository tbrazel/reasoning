"""Hosted dispatch/evidence fixtures, never an actual registry publication.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import shutil
from pathlib import Path

import pytest
from test_public_compute_publication import reviewed as reviewed_fixture
from test_public_compute_review import complete

from reasoning_ledger import image_hosted as hosted
from reasoning_ledger import image_publication as publication
from reasoning_ledger.server.compute_catalog import canonical


def environment(revision):
    return {"PROFILE": "research-cpu", "SOURCE_REVISION": revision, "CANDIDATE_RUN_ID": "17", "GITHUB_RUN_ID": "42",
            "GITHUB_SHA": "a" * 40, "GITHUB_REPOSITORY": "research/bench", "GITHUB_REF": "refs/heads/publication-fixture"}


def test_hosted_request_is_exact_bounded_and_contains_no_environment_secrets():
    source = environment("b" * 40) | {"GH_TOKEN": "private-fixture-token", "SSH_AUTH_SOCK": "private-agent"}
    result = hosted.request_identity(source)
    assert "private" not in canonical(result) and result["candidate_run_id"] == 17 and result["workflow_run_id"] == 42
    for field, value in [("PROFILE", "other"), ("SOURCE_REVISION", "main"), ("GITHUB_RUN_ID", "-1")]:
        with pytest.raises(ValueError):
            hosted.request_identity(source | {field: value})


@pytest.fixture
def completed(tmp_path, monkeypatch):
    reviewed = reviewed_fixture.__wrapped__(tmp_path, monkeypatch)
    args, report = complete(reviewed, monkeypatch)
    env = environment(report["image"]["source_revision"])
    (args[-1] / "workflow-request.json").write_text(canonical(hosted.request_identity(env)))
    run = {"id": 42, "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
           "path": ".github/workflows/compute-publish.yml", "repository": {"id": 1, "full_name": "research/bench"},
           "head_repository": {"id": 1}, "head_sha": env["GITHUB_SHA"], "head_branch": "publication-fixture"}
    effects = []

    def github(arguments, *, structured=True):
        effects.append(arguments)
        if arguments[0] == "api":
            return run
        target = Path(arguments[arguments.index("--dir") + 1])
        for path in args[-1].iterdir():
            if path.name in hosted.PUBLIC_FILES:
                shutil.copyfile(path, target / path.name)

    monkeypatch.setattr(publication, "verify_public_image", lambda image, parent: effects.append("anonymous actual-execution fixture"))
    return args, report, env, run, github, effects


def test_hosted_combination_requires_fresh_checks_and_binds_exact_publication_runs(completed, tmp_path):
    args, report, env, _, github, effects = completed
    output = tmp_path / "combined"
    receipt = hosted.hosted_combine(report["image"]["source_revision"], {"research-cpu": 42}, args[7], output,
                                    github=github, environment=env)
    assert receipt["publication_runs"] == {"research-cpu": 42} and receipt["anonymous_reverification"] is True
    assert receipt["catalog_sha256"] == publication.hash_file(output / "compute-images.json")[0]
    assert effects[-1] == "anonymous actual-execution fixture"
    assert (output / "review-key.pem").read_bytes() == args[7].read_bytes()


@pytest.mark.parametrize("drift", ["fork", "failure", "workflow", "request", "signature", "private_extra", "run_id"])
def test_hosted_combination_refuses_changed_or_private_evidence(completed, tmp_path, drift):
    args, report, env, run, github, _ = completed
    if drift == "fork":
        run["head_repository"]["id"] = 2
    elif drift == "failure":
        run["conclusion"] = "failure"
    elif drift == "workflow":
        run["path"] = ".github/workflows/compute-images.yml"
    elif drift == "request":
        (args[-1] / "workflow-request.json").write_text(canonical(hosted.request_identity(env | {"GITHUB_RUN_ID": "43"})))
    elif drift == "signature":
        (args[-1] / "source-review.ed25519").write_bytes(b"x" * 64)
    elif drift == "run_id":
        run["id"] = 43
    if drift == "private_extra":
        original = github

        def github(arguments, *, structured=True):
            value = original(arguments, structured=structured)
            if not structured:
                target = Path(arguments[arguments.index("--dir") + 1])
                (target / "worker-token").write_text("must not be published")
            return value
    output = tmp_path / "refused"
    with pytest.raises(ValueError):
        hosted.hosted_combine(report["image"]["source_revision"], {"research-cpu": 42}, args[7], output,
                             github=github, environment=env)
    assert not (output / "compute-images.json").exists() and not (output / "combine.json").exists()


@pytest.mark.parametrize("value", [{}, {"unknown": 1}, {"research-cpu": True}, {"research-cpu": -1}, {"research-cpu": "17"}])
def test_hosted_selection_does_not_adopt_candidate_or_ambiguous_runs(value):
    with pytest.raises(ValueError):
        hosted.publications_input(canonical(value), "a" * 40)
