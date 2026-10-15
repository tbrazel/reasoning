"""The full source gate prepares its own frontend and protected test state.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_make_check_prepares_private_state_and_frontend_before_python_routes():
    makefile = (ROOT / "Makefile").read_text()
    wrapper = makefile.split("\ncheck:\n", 1)[1].split("\n\n", 1)[0]
    assert "umask 022;" in wrapper
    gate = makefile.split("\n_node_check:", 1)[1].split("\n_node_dev-setup:", 1)[0]
    assert gate.index("--prefix web ci") < gate.index("--prefix web run build") < gate.index("$(PYTEST) -q")
