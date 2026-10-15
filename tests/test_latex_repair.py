"""Tests for the two-stage guarded LaTeX repair."""

import textwrap

import pytest

from reasoning_ledger import latex_check, repair

pytestmark = pytest.mark.skipif(
    not latex_check.available(), reason="Node + KaTeX checker unavailable"
)


def _fake(tmp_path, body: str) -> str:
    script = tmp_path / "fake-repair"
    script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    script.chmod(0o755)
    return str(script)


class TestPureLogic:
    def test_unbalanced_detection(self):
        assert not repair.has_unbalanced_delimiters(r"ok \(x\) and \[y\]")
        assert repair.has_unbalanced_delimiters(r"missing end \(x is set y")
        assert repair.has_unbalanced_delimiters(r"$a$ and $b")

    def test_strip_invariant_ignores_delimiter_moves(self):
        a = r"Let $x$ be a set $y$."
        moved = r"Let $x$ be a set $y.$"
        changed = r"Let $x$ be a group $y$."
        assert repair._strip_delims(a) == repair._strip_delims(moved)
        assert repair._strip_delims(a) != repair._strip_delims(changed)


class TestStageA:
    def test_span_content_fix_is_spliced(self, tmp_path, monkeypatch):
        # \RR fails; the model returns \mathbb{R}, which renders
        monkeypatch.setenv(
            "REASONING_REPAIR_COMMAND",
            _fake(
                tmp_path,
                """
                import sys, json
                sys.stdin.read()
                print(json.dumps({"fixed": [r"\\mathbb{R}"]}))
                """,
            ),
        )
        out = repair.repair_text(r"The ring \(\RR\) is nice.")
        assert out == r"The ring \(\mathbb{R}\) is nice."
        assert latex_check.check_text(out) == []

    def test_prose_is_never_altered(self, tmp_path, monkeypatch):
        # even a misbehaving model can only affect the span; the prose
        # around it is spliced verbatim by the harness
        monkeypatch.setenv(
            "REASONING_REPAIR_COMMAND",
            _fake(
                tmp_path,
                """
                import sys, json
                sys.stdin.read()
                print(json.dumps({"fixed": [r"\\mathbb{Z}"]}))
                """,
            ),
        )
        out = repair.repair_text(r"Consider \(\ZZ\) modules over a base.")
        assert out.startswith("Consider ") and out.endswith(" modules over a base.")

    def test_a_still_broken_fix_is_discarded(self, tmp_path, monkeypatch):
        # the model returns something that still fails KaTeX -> keep original,
        # repair_text returns None (no improvement)
        monkeypatch.setenv(
            "REASONING_REPAIR_COMMAND",
            _fake(
                tmp_path,
                """
                import sys, json
                sys.stdin.read()
                print(json.dumps({"fixed": [r"\\stillbad"]}))
                """,
            ),
        )
        assert repair.repair_text(r"The ring \(\RR\).") is None


class TestStageB:
    def test_delimiter_repair_passes_guard(self, tmp_path, monkeypatch):
        # a missing closing $ swallowed prose into math; the model re-places
        # the delimiter without touching content
        broken = r"Let $x be the identity element."
        fixed = r"Let $x$ be the identity element."
        monkeypatch.setenv(
            "REASONING_REPAIR_COMMAND",
            _fake(
                tmp_path,
                f"""
                import sys
                sys.stdin.read()
                print({fixed!r}, end="")
                """,
            ),
        )
        out = repair.repair_text(broken)
        assert out == fixed
        assert latex_check.check_text(out) == []

    def test_content_change_is_rejected_by_guard(self, tmp_path, monkeypatch):
        # the model reworded prose while "fixing" delimiters -> guard rejects,
        # original is kept (repair_text returns None since nothing improved)
        broken = r"Let $x be the identity element."
        cheating = r"Let $x$ be the neutral element."  # identity -> neutral
        monkeypatch.setenv(
            "REASONING_REPAIR_COMMAND",
            _fake(
                tmp_path,
                f"""
                import sys
                sys.stdin.read()
                print({cheating!r}, end="")
                """,
            ),
        )
        assert repair.repair_text(broken) is None


class TestNoOp:
    def test_clean_text_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "REASONING_REPAIR_COMMAND",
            _fake(tmp_path, "import sys; sys.stdin.read(); print('unused')"),
        )
        assert repair.repair_text(r"All good: \(x^2 + 1\).") is None
