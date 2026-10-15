"""Collection-only checks keep optional campaigns out of deployment verification."""

import os
import subprocess
import sys
from pathlib import Path


def collect(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_default_excludes_optional_campaigns_but_keeps_compute_security():
    result = collect()
    assert result.returncode == 0
    assert "tests/test_science_evidence.py::" not in result.stdout
    assert "tests/test_local_science_tools.py::" not in result.stdout
    assert "tests/test_public_compute_docker.py::" in result.stdout
    assert "tests/test_public_input_security.py::" in result.stdout


def test_science_is_explicitly_available_and_explicit_path_is_not_an_implicit_opt_in():
    enabled = collect("--science", "tests/test_science_evidence.py")
    assert enabled.returncode == 0 and "tests/test_science_evidence.py::" in enabled.stdout
    disabled = collect("tests/test_science_evidence.py")
    assert disabled.returncode == 5 and "deselected" in disabled.stdout


def test_deployment_scope_contains_live_launch_contract_and_excludes_fleet_and_research():
    result = collect("--deployment")
    assert result.returncode == 0
    assert "tests/test_public_server_installation.py::" in result.stdout
    assert "tests/test_public_dns.py::" in result.stdout
    assert "tests/test_public_owner_setup.py::" in result.stdout
    assert "tests/test_public_workflow_runs.py::" in result.stdout
    assert "tests/test_public_compute_fleet.py::" not in result.stdout
    assert "tests/test_science_" not in result.stdout


def test_conflicting_scopes_fail_before_collection():
    result = collect("--science", "--deployment")
    assert result.returncode == 4 and "run them separately" in result.stderr


def test_postgres_fixture_shards_omit_only_sqlite_parameters_and_require_explicit_test_dsn():
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "--skip-sqlite-params", "tests/test_public_work_context.py"]
    environment = {**os.environ, "REASONING_TEST_POSTGRES_DSN": "fixture-collection-only"}
    result = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "[postgresql]" in result.stdout and "[sqlite]" not in result.stdout and "deselected" in result.stdout
    assert "test_strategies_have_no_implicit_modifier" in result.stdout, "non-parametric checks remain selected"
    environment.pop("REASONING_TEST_POSTGRES_DSN")
    absent = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=30, check=False
    )
    assert absent.returncode == 4 and "requires a disposable" in absent.stderr
