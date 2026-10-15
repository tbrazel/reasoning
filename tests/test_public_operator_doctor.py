"""Operator verification uses retained release/trust and always closes SSH."""

from __future__ import annotations

import json

import pytest
from test_public_operator_handoff import deployment_state

from reasoning_ledger.deployment import operator_doctor
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.release_state import archived_release


@pytest.mark.parametrize("outcome", ["pass", "failed_check", "transport_error", "opening_pending", "closing_pending"])
def test_operator_verification_uses_exact_retained_inputs_and_closes_ssh(tmp_path, outcome):
    state, document, request, binding, token, identity = deployment_state(tmp_path)
    calls = []

    def command(arguments):
        calls.append(arguments)
        assert arguments[0] == "do-ssh-access"
        assert arguments[arguments.index("--token-file") + 1] == str(token)
        phase = "closed" if "--close" in arguments else "open"
        if outcome == "opening_pending" and phase == "open" or outcome == "closing_pending" and phase == "closed":
            phase = "pending"
        print(json.dumps({"phase": phase}))
        return 0

    def diagnose(archive, origin, address, private, known_hosts, *, confirmed_release):
        assert archive == archived_release(state, binding["release_id"])
        assert origin == binding["public_url"] and address == "139.59.1.2"
        assert private == identity and confirmed_release == binding["release_id"]
        assert known_hosts.read_text().startswith("139.59.1.2 ssh-ed25519 ")
        if outcome == "transport_error":
            raise InstallError("diagnostic transport fixture")
        return {"all_checks_passed": outcome != "failed_check", "checks": {"https_health": "fail" if outcome == "failed_check" else "pass"}}

    if outcome == "pass":
        result = operator_doctor.verify_operator(state, command, detect_public_ipv4=lambda: "203.0.113.20/32", diagnose=diagnose)
        assert result["phase"] == "operator_verified" and result["ssh_closed"] is True
    else:
        with pytest.raises(InstallError):
            operator_doctor.verify_operator(state, command, detect_public_ipv4=lambda: "203.0.113.20/32", diagnose=diagnose)
    assert len(calls) == 2 and "--open-cidr" in calls[0] and "--close" in calls[-1]


def test_cli_operator_doctor_uses_selected_state(tmp_path, monkeypatch, capsys):
    from reasoning_ledger.deployment.cli import main

    calls = []

    def verify(directory, command):
        calls.append(directory)
        return {"phase": "operator_verified", "ssh_closed": True}

    monkeypatch.setattr(operator_doctor, "verify_operator", verify)
    assert main(["operator-doctor", "--state-dir", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert json.loads(capsys.readouterr().out)["ssh_closed"] is True
