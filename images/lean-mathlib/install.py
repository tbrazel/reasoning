"""Build-only pinned mathlib setup, cached objects and offline import probe.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import os
import subprocess
from pathlib import Path

root = Path("/opt/mathlib")
lock = json.loads(Path("/usr/share/reasoning/lean-mathlib-inputs.json").read_bytes())
snapshot = "https://snapshot.ubuntu.com/ubuntu/" + lock["ubuntu_snapshot"]
sources = Path("/tmp/reasoning-build.sources.list")
sources.write_text("".join("deb " + snapshot + " " + suite + " main\n" for suite in ("jammy", "jammy-updates", "jammy-security")))
# Only this signed, immutable archive is consulted; never the base image's live mirrors.
apt = [
    "apt-get",
    "-o",
    "Dir::Etc::sourcelist=" + str(sources),
    "-o",
    "Dir::Etc::sourceparts=-",
    "-o",
    "Acquire::Check-Valid-Until=false",
    "-o",
    "APT::Update::Error-Mode=any",
]
subprocess.run([*apt, "update"], check=True)
subprocess.run(
    [*apt, "install", "-y", "--no-install-recommends", *[name + "=" + version for name, version in sorted(lock["build_packages"].items())]],
    check=True,
)
subprocess.run([*apt, "clean"], check=True)
sources.unlink()
assert (root / "lean-toolchain").read_text().strip() == "leanprover/lean4:v" + lock["lean_version"]
manifest = json.loads((root / "lake-manifest.json").read_bytes())
assert {item["name"]: item["rev"] for item in manifest["packages"]} == lock["dependencies"]
# The archived source gets the exact commit identity required by the mathlib cache.
subprocess.run(["git", "init"], cwd=root, check=True)
subprocess.run(["git", "remote", "add", "origin", "https://github.com/leanprover-community/mathlib4.git"], cwd=root, check=True)
subprocess.run(["git", "fetch", "--depth", "1", "origin", lock["mathlib_revision"]], cwd=root, check=True)
subprocess.run(["git", "reset", "--mixed", "FETCH_HEAD"], cwd=root, check=True)
subprocess.run(["/opt/lean/bin/lake", "exe", "cache", "get"], cwd=root, check=True)
for item in manifest["packages"]:
    package = root / ".lake/packages" / item["name"]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=package, text=True).strip()
    assert revision == item["rev"], "mathlib dependency drift"
path = subprocess.check_output(["/opt/lean/bin/lake", "env", "printenv", "LEAN_PATH"], cwd=root, text=True).strip()
parts = [str((root / item).resolve()) if not item.startswith("/") else item for item in path.split(":")]
Path("/usr/share/reasoning/lean-path.txt").write_text(":".join(parts))
subprocess.run(
    ["/opt/lean/bin/lean", "--stdin"],
    input="import Mathlib\nexample : (2 : ℕ) + 3 = 5 := by norm_num\n",
    text=True,
    env={**os.environ, "LEAN_PATH": ":".join(parts)},
    cwd=root,
    check=True,
)
