"""Default compute onboarding can be skipped and safely resumed.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""
import pytest

from reasoning_ledger.deployment import setup
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import read_document, write_document


@pytest.mark.parametrize("answer,expected", [("yes", "modal"), ("no", "none"), ("skip", "none")])
def test_default_compute_choice_retains_yes_or_skip(tmp_path, monkeypatch, answer, expected):
    tmp_path.chmod(0o700)
    calls = []
    monkeypatch.setattr(setup, "ask", lambda prompt, default: calls.append(default) or answer)
    assert setup.select_compute_backend(tmp_path, "auto") == expected
    assert read_document(tmp_path / "compute-choice.json") == {"backend": expected}
    assert setup.select_compute_backend(tmp_path, "auto") == expected
    assert calls == ["yes"]


def test_completed_setup_resumes_original_compute_choice_without_prompt(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    write_document(tmp_path / "setup-completion.json", {"compute_backend": "none"})
    monkeypatch.setattr(setup, "ask", lambda *_: pytest.fail("must not prompt on completed setup"))
    assert setup.select_compute_backend(tmp_path, "auto") == "none"


def test_explicit_compute_skip_does_not_prompt_or_write_a_new_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "ask", lambda *_: pytest.fail("explicit choice"))
    assert setup.select_compute_backend(tmp_path, "none") == "none"
    assert not (tmp_path / "compute-choice.json").exists()


def test_bad_retained_choice_fails_closed(tmp_path):
    tmp_path.chmod(0o700)
    write_document(tmp_path / "compute-choice.json", {"backend": "invalid"})
    with pytest.raises(InstallError):
        setup.select_compute_backend(tmp_path, "auto")


def test_can_skip_an_unstarted_default_selection_without_poisoning_auto_resume(tmp_path):
    tmp_path.chmod(0o700)
    write_document(tmp_path / "compute-choice.json", {"backend": "modal"})
    assert setup.select_compute_backend(tmp_path, "none") == "none"
    assert setup.select_compute_backend(tmp_path, "auto") == "none"


def test_retained_modal_input_cannot_be_silently_omitted(tmp_path):
    tmp_path.chmod(0o700)
    write_document(tmp_path / "compute-choice.json", {"backend": "modal"})
    write_document(tmp_path / "modal-input.json", {"saved": True})
    with pytest.raises(InstallError, match="original compute selection"):
        setup.select_compute_backend(tmp_path, "none")
