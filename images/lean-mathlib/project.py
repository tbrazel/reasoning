"""Offline Lean files and bounded multi-file projects in the worker's scratch directory.

The JSON report is a job artifact, not a trusted server proof attestation.
Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FILE = re.compile(r"Proofs/(?:[A-Z][A-Za-z0-9_]*/)*[A-Z][A-Za-z0-9_]*\.lean\Z")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def project(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "files", "entry", "target", "expected_type"}
        or type(value["format"]) is not int
        or value["format"] != 1
    ):
        raise ValueError("project requires format, files, entry, target and expected_type")
    files = value["files"]
    if (
        not isinstance(files, dict)
        or not 1 <= len(files) <= 32
        or any(
            not isinstance(name, str) or not FILE.fullmatch(name) or not isinstance(source, str) or not source.strip()
            for name, source in files.items()
        )
        or sum(len(source.encode()) for source in files.values()) > 64000
    ):
        raise ValueError("provide at most 32 bounded Proofs/*.lean files")
    if value["entry"] not in files or not isinstance(value["target"], str) or not NAME.fullmatch(value["target"]):
        raise ValueError("entry must exist and target must be a Lean declaration name")
    expected = value["expected_type"]
    if (
        not isinstance(expected, str)
        or not 1 <= len(expected) <= 2000
        or any(mark in expected for mark in ("\n", "\r", "\x00", "--", "/-", "-/", "#", ";"))
    ):
        raise ValueError("expected_type must be a bounded single Lean type expression")
    dependencies = {}
    for name, source in files.items():
        imports = [word for line in source.splitlines() if line.startswith("import ") for word in line[7:].split("--")[0].split()]
        dependencies[name] = [word.replace(".", "/") + ".lean" for word in imports if word.startswith("Proofs.")]
        if any(dependency not in files for dependency in dependencies[name]):
            raise ValueError("project imports an absent Proofs module")
    ordered, pending = [], set(files)
    while pending:
        ready = sorted(name for name in pending if not set(dependencies[name]) & pending)
        if not ready:
            raise ValueError("cyclic local Lean imports")
        ordered.extend(ready)
        pending.difference_update(ready)
    return files, ordered


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["-DwarningAsError=true"]:
        args = args[1:]
    if len(args) != 1 or args[0].startswith("-"):
        return subprocess.call(["/opt/lean/bin/lean", *args])
    source_path = Path(args[0])
    raw = source_path.read_bytes()
    if len(raw) > 64000:
        raise ValueError("Lean source exceeds its allowance")
    base = Path("/work/project")
    base.mkdir(mode=0o700, exist_ok=False)
    path = Path("/usr/share/reasoning/lean-path.txt").read_text().strip()
    env = {**os.environ, "LEAN_PATH": str(base) + ":" + path}
    if not raw.lstrip().startswith(b"{"):
        source = base / "Submission.lean"
        source.write_bytes(raw)
        return subprocess.call(["/opt/lean/bin/lean", "-DwarningAsError=true", "--root=" + str(base), str(source)], cwd=base, env=env)
    value = json.loads(raw)
    files, ordered = project(value)
    for name in ordered:
        target = base / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(files[name])
        result = subprocess.call(
            ["/opt/lean/bin/lean", "-DwarningAsError=true", "--root=" + str(base), "-o", str(target.with_suffix(".olean")), str(target)],
            cwd=base,
            env=env,
        )
        if result:
            return result
    entry_module = value["entry"][:-5].replace("/", ".")
    guard = base / "ReasoningTypeGuard.lean"
    guard.write_text(
        f"import {entry_module}\nset_option warningAsError true\n"
        f"theorem reasoning_target_type : ({value['expected_type']}) := {value['target']}\n"
    )
    result = subprocess.call(
        ["/opt/lean/bin/lean", "--root=" + str(base), "-o", str(guard.with_suffix(".olean")), str(guard)], cwd=base, env=env
    )
    if result:
        return result
    audit = subprocess.run(
        ["/opt/lean/bin/lean", "--run", "/opt/reasoning/Audit.lean", "ReasoningTypeGuard", "reasoning_target_type"],
        cwd=base,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if len(audit.stdout.encode()) + len(audit.stderr.encode()) > 65536:
        raise ValueError("axiom audit output exceeded its allowance")
    report = json.loads(audit.stdout)
    lock = json.loads(Path("/usr/share/reasoning/lean-mathlib-inputs.json").read_bytes())
    report.update(
        format=1,
        target=value["target"],
        expected_type=value["expected_type"],
        program_sha256=hashlib.sha256(raw).hexdigest(),
        lean_version=lock["lean_version"],
        mathlib_revision=lock["mathlib_revision"],
        provenance="untrusted-job-artifact-not-server-proof-attestation",
    )
    out = Path("/work/out")
    out.mkdir(mode=0o700, exist_ok=True)
    (out / "lean-report.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    for name, text in files.items():
        (out / name.replace("/", "_")).write_text(text)
    print(json.dumps(report, sort_keys=True))
    return audit.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Lean project refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
