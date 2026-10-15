"""Probe installed runtimes and emit deterministic capabilities and SPDX inventory.

Runs with the image's Sage Python, using only the standard library. The inventory
does not assert a completed license or corresponding-source audit.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

CAPABILITIES_PATH = "/etc/reasoning/capabilities.json"
SBOM_PATH = "/usr/share/reasoning/sbom.spdx.json"
DEFAULTS = {
    "cpu": 1,
    "memory_bytes": 1073741824,
    "disk_bytes": 268435456,
    "pids": 128,
    "wall_time_seconds": 120,
    "artifact_bytes": 1048576,
}


def run(argv: list[str], text: str | None = None) -> str:
    result = subprocess.run(argv, input=text, text=True, capture_output=True, check=True, timeout=60)
    if len(result.stdout) + len(result.stderr) > 1024 * 1024:
        raise ValueError("runtime probe exceeded its output limit")
    return (result.stdout + result.stderr).strip()


def version(name: str, text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    if match is None:
        raise ValueError(f"cannot parse {name} version")
    return match.group(1)


def probe() -> dict[str, str]:
    pari = run(["gp", "-fq"], "print(version());quit\n")
    match = re.search(r"\[(\d+),\s*(\d+),\s*(\d+)\]", pari)
    if match is None:
        raise ValueError("cannot parse PARI version")
    return {
        "python": run(["python3", "-c", "import platform; print(platform.python_version())"]),
        "sage": version("Sage", run(["sage", "--version"]), r"SageMath version ([0-9.]+)"),
        "gap": version("GAP", run(["gap", "-q"], "Print(GAPInfo.Version);QUIT;\n"), r"(\d+\.\d+\.\d+)"),
        "pari": ".".join(match.groups()),
        "julia": version("Julia", run(["julia", "--version"]), r"julia version ([0-9.]+)"),
        "lean": version("Lean", run(["lean", "--version"]), r"Lean \(version ([0-9.]+),"),
        "noether": run(["python3", "-c", "import noether; print(noether.__version__)"]),
    }


def inventory() -> list[dict]:
    packages = []
    for line in run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"]).splitlines():
        name, release, source, source_version = line.split("\t")
        packages.append({"kind": "deb", "name": name, "version": release, "source": source, "source_version": source_version})
    for distribution in importlib.metadata.distributions():
        name, release = distribution.metadata.get("Name"), distribution.version
        if not name or not release:
            raise ValueError("installed Python distribution lacks identity")
        packages.append({"kind": "python", "name": name, "version": release})
        if name == "tertius-noether":
            packages.extend(noether_inventory(distribution))
    if not any(p["kind"] == "deb" for p in packages) or not any(p["kind"] == "python" for p in packages):
        raise ValueError("OS and Python package inventories are required")
    return sorted(packages, key=lambda p: json.dumps(p, sort_keys=True))


def noether_inventory(distribution) -> list[dict]:
    """Retain the wheel's installed native and Rust runtime dependency closure."""
    records = []
    for basename in ("native-libraries.cyclonedx.json", "noether-python.cyclonedx.json"):
        matches = [file for file in distribution.files or [] if str(file).endswith("/sboms/" + basename)]
        if len(matches) != 1:
            raise ValueError("Noether wheel lacks one required installed dependency inventory")
        document = json.loads(Path(distribution.locate_file(matches[0])).read_bytes())
        if document.get("bomFormat") != "CycloneDX" or not isinstance(document.get("components"), list):
            raise ValueError("Noether wheel dependency inventory is malformed")
        for component in document["components"]:
            if not component.get("name") or not component.get("version") or not component.get("bom-ref"):
                raise ValueError("Noether dependency inventory lacks component identity")
            record = {
                "kind": "noether-native" if basename.startswith("native") else "noether-rust",
                "name": component["name"], "version": component["version"],
                "identity": component["bom-ref"],
            }
            if "hashes" in component:
                record["hashes"] = component["hashes"]
            if "licenses" in component:
                record["licenses"] = component["licenses"]
            records.append(record)
        if basename.startswith("native") and {c["name"] for c in document["components"]} != {"FLINT", "GMP", "MPFR"}:
            raise ValueError("Noether native inventory does not cover FLINT, GMP and MPFR")
    return records


def verify_rust_notices(lock: dict, directory: Path | None = None) -> None:
    """Check exact release notices copied from the pinned compiler image."""
    runtime = lock["noether"]["rust_runtime"]
    directory = directory or Path(runtime["notice_directory"])
    if set(runtime["notices"]) != {"COPYRIGHT-library.html", "LICENSE-MIT", "LICENSE-APACHE"}:
        raise ValueError("Rust runtime notice inventory is incomplete")
    for name, expected in runtime["notices"].items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
            raise ValueError("Rust runtime notice is absent, linked or oversized")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Rust runtime notice SHA256 differs from its release lock")


def documents(lock: dict, observed: dict, packages: list[dict], revision: str, timestamp: str, mathlib=None) -> tuple[dict, dict]:
    if observed != lock["versions"]:
        raise ValueError("installed runtimes differ from locked versions")
    if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision):
        raise ValueError("source revision must be a complete Git object ID")
    if not timestamp.endswith("Z") or datetime.fromisoformat(timestamp.replace("Z", "+00:00")).utcoffset().total_seconds() != 0:
        raise ValueError("build timestamp must be UTC")
    runtimes = [{"name": name, "version": observed[name]} for name in ("python", "julia", "lean")]
    tools = [
        {"name": name, "version": observed[name], "entry_points": [entry], "operations": [operation]}
        for name, entry, operation in (
            ("python", "python3", "python-program-execution"),
            ("sage", "sage", "computer-algebra"),
            ("gap", "gap", "computational-group-theory"),
            ("pari", "gp", "number-theory"),
            ("julia", "julia", "base-stdlib-program-execution"),
            ("lean", "lean", "core-kernel-check"),
            ("noether", "/opt/reasoning/noether_worker.py", "declarative-verified-computation"),
        )
    ]
    capabilities = {
        "schema_id": "reasoning.executor.image-capabilities",
        "schema_version": "1.0.0-alpha.1",
        "manifest_scope": "installed",
        "image_name": "reasoning/research-cpu",
        "build_timestamp": timestamp,
        "source_revision": revision,
        "platform": {"os": "linux", "architecture": "amd64"},
        "runtimes": runtimes,
        "tools": tools,
        "compatibility": {"executor_contract": "1.0.0-alpha.1", "runner": "reasoning-compute-worker"},
        "paths": {"working": "/work", "input": "/work/inputs.json", "output": "/work/out", "cache": "/tmp", "temporary": "/tmp"},
        "resource_limits": {"defaults": DEFAULTS, "maximums": lock["maximum_resources"]},
        "network": {"supported_modes": ["denied"], "possible_destinations": []},
        "devices": [],
        "restrictions": [
            "Installed capability is not per-job permission; the worker must enforce limits and deny network/devices.",
            "Writable job paths are bounded worker tmpfs mounts, not image volumes.",
            "Julia includes Base and bundled standard libraries; additional registry packages are not installed.",
            "Lean includes the core toolchain; mathlib is not installed.",
            "Noether uses its pinned default CPU profile; MPI and optional GPL subprocess bridges are not installed.",
            "Noether requires x86-64-v3 CPU instructions; available capabilities do not grant per-job permission.",
        ],
        "sbom": {"format": "spdx-json", "path": SBOM_PATH},
    }
    if mathlib is not None:
        if mathlib["lean_version"] != observed["lean"] or not re.fullmatch(r"[a-f0-9]{40}", mathlib["mathlib_revision"]):
            raise ValueError("mathlib toolchain or revision differs from its lock")
        capabilities["image_name"] = "reasoning/lean-mathlib"
        capabilities["resource_limits"]["defaults"] = {**DEFAULTS, "memory_bytes": 4 * 1024**3}
        capabilities["tools"].append(
            {
                "name": "mathlib",
                "version": mathlib["mathlib_revision"],
                "entry_points": ["lean", "lake"],
                "operations": ["offline-import", "multi-file-project", "axiom-audit"],
            }
        )
        capabilities["restrictions"] = [line for line in capabilities["restrictions"] if "mathlib is not installed" not in line]
        capabilities["restrictions"].append("Lean project reports are untrusted job artifacts, not server proof attestations.")
        packages = [
            *packages,
            {"kind": "source", "name": "mathlib", "version": mathlib["mathlib_revision"]},
            *[{"kind": "source", "name": name, "version": version} for name, version in sorted(mathlib["dependencies"].items())],
        ]
    records = []
    noether = lock["noether"]
    packages = [
        *packages,
        {"kind": "source", "name": "noether-source", "version": noether["commit"]},
        {
            "kind": "compiler-runtime", "name": "rust-standard-library-runtime",
            "version": noether["rust_version"], "compiler_image": noether["rust_image"],
            "compiler_distribution": noether["rust_runtime"]["compiler_distribution"],
            "source_distribution": noether["rust_runtime"]["source_distribution"],
            "notice_directory": noether["rust_runtime"]["notice_directory"],
            "notice_sha256": noether["rust_runtime"]["notices"],
            "closure_verified": False,
            "review": (
                "Compiler-provided std/core/alloc and runtime dependencies are outside the Cargo wheel inventory; "
                "review static linkage and notice/source obligations."
            ),
        },
    ]
    for package in packages:
        identity = json.dumps(package, sort_keys=True, separators=(",", ":"))
        records.append(
            {
                "SPDXID": "SPDXRef-Package-" + hashlib.sha256(identity.encode()).hexdigest(),
                "name": package["name"],
                "versionInfo": package["version"],
                "filesAnalyzed": False,
                "downloadLocation": "NOASSERTION",
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "comment": identity,
            }
        )
    for name, release, location, checksum in (
        ("SageMath-runtime-distribution", observed["sage"], lock["base"], lock["base"].split("@sha256:")[1]),
        *[
            (name + "-binary-distribution", artifact["version"], artifact["url"], artifact["sha256"])
            for name, artifact in sorted(lock["downloads"].items())
        ],
    ):
        records.append(
            {
                "SPDXID": "SPDXRef-Distribution-" + name,
                "name": name,
                "versionInfo": release,
                "downloadLocation": location,
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "checksums": [{"algorithm": "SHA256", "checksumValue": checksum}],
            }
        )
    records.sort(key=lambda p: p["SPDXID"])
    if len({p["SPDXID"] for p in records}) != len(records):
        raise ValueError("duplicate installed package identities")
    inventory_digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "reasoning-research-cpu-installed-inventory",
        "documentNamespace": f"https://schemas.reasoning.invalid/spdx/research-cpu/{revision}/{inventory_digest}",
        "creationInfo": {"created": timestamp, "creators": ["Tool: reasoning-installed-inventory/1"]},
        "documentComment": "Installed package/distribution inventory, not a complete filesystem scan or license/source audit.",
        "packages": records,
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": p["SPDXID"]} for p in records
        ],
    }
    return capabilities, sbom


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--build-timestamp", required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--mathlib-lock", type=Path)
    args = parser.parse_args()
    mathlib = json.loads(args.mathlib_lock.read_bytes()) if args.mathlib_lock else None
    lock = json.loads(args.lock.read_bytes())
    verify_rust_notices(lock)
    capabilities, sbom = documents(
        lock, probe(), inventory(), args.source_revision, args.build_timestamp, mathlib
    )
    for path, body, limit in ((args.capabilities, capabilities, 1024**2), (args.sbom, sbom, 8 * 1024**2)):
        encoded = (json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(encoded) > limit:
            raise ValueError("installed evidence exceeds worker limits")
        path.write_bytes(encoded)


if __name__ == "__main__":
    main()
