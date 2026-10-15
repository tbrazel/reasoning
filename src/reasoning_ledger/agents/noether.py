"""Manifest-bound, declarative access to an isolated Noether runtime."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .tools import ToolRegistry

NOETHER_MANIFEST_ENV = "REASONING_NOETHER_MANIFEST"
_COMPUTE_OPERATIONS = (
    "factor_integer",
    "binomial_invariants",
    "multinomial_invariants",
    "perfect_matching_polynomial",
    "matrix_invariants",
    "lattice_shortest_vectors",
    "lattice_orthogonal_complements",
    "integer_lattice_membership",
    "integer_kernel_projection",
    "ideal_invariants",
    "simplicial_homology",
    "swarm_topology",
)
_COMPUTE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {"enum": list(_COMPUTE_OPERATIONS)},
        "payload": {
            "type": "object",
            "description": (
                "Operation input: factor_integer uses {value}; binomial_invariants "
                "uses {n, k}; multinomial_invariants uses "
                "{power, parts, modulus?}; perfect_matching_polynomial uses "
                "{multiplicities: [{label, count}], pairings?: [{left, right, value}], "
                "modulus?, max_terms?}; "
                "matrix_invariants uses {ring, rows}; "
                "lattice_shortest_vectors uses {gram, bound?, max_pairs?}; "
                "lattice_orthogonal_complements uses {gram, vectors}; "
                "integer_lattice_membership uses {matrix, target}; "
                "integer_kernel_projection uses {matrix, selected_columns, include_kernel_basis?}; "
                "ideal_invariants uses {field, ambient, variables, generators, "
                "check_smooth?}; simplicial_homology uses {facets}; "
                "swarm_topology uses {facets, edges, directed}."
            ),
            "maxProperties": 8,
        },
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
    },
    "required": ["operation", "payload"],
    "additionalProperties": False,
}


class NoetherRuntimeError(ValueError):
    """A Noether installation or fixed-protocol computation is invalid."""


def configured_noether_manifest() -> Path | None:
    value = os.environ.get(NOETHER_MANIFEST_ENV)
    return Path(value).expanduser().resolve() if value else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class NoetherRuntime:
    manifest_path: Path
    manifest_sha256: str
    installation_root: Path
    python: Path
    python_runtime_root: Path
    site_packages: Path
    wheel: Path
    wheel_sha256: str
    installation_id: str
    version: str
    source_commit: str
    verification: dict[str, Any]

    @classmethod
    def load(cls, manifest_path: Path | str) -> NoetherRuntime:
        path = Path(manifest_path).expanduser().resolve()
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, ValueError) as error:
            raise NoetherRuntimeError(f"cannot read Noether manifest {path}: {error}") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise NoetherRuntimeError("Noether manifest schema_version must be 1")
        try:
            installation_root = Path(value["installation_root"]).expanduser().resolve()
            runtime = value["runtime"]
            wheel = value["wheel"]
            source = value["source"]
            python_runtime_root = Path(runtime["python_runtime_root"]).expanduser().resolve()
            wheel_path = Path(wheel["path"]).expanduser().resolve()
            wheel_hash = str(wheel["sha256"])
            installation_id = str(value["installation_id"])
            version = str(value["package"]["version"])
            source_commit = str(source["commit"])
            verification = value["verification"]
        except (KeyError, TypeError, OSError) as error:
            raise NoetherRuntimeError(f"Noether manifest is incomplete: {error}") from error
        declared_python = Path(os.path.abspath(Path(runtime["python"]).expanduser()))
        if not installation_root.is_dir() or not python_runtime_root.is_dir():
            raise NoetherRuntimeError("Noether installation or Python runtime root is unavailable")
        if not declared_python.is_relative_to(installation_root):
            raise NoetherRuntimeError("Noether runtime Python escapes installation_root")
        if not declared_python.is_file() or not os.access(declared_python, os.X_OK):
            raise NoetherRuntimeError(f"Noether runtime Python is unavailable: {declared_python}")
        site_package_candidates = tuple((installation_root / "runtime" / "lib").glob("python*/site-packages"))
        if len(site_package_candidates) != 1 or not site_package_candidates[0].is_dir():
            raise NoetherRuntimeError("Noether runtime needs exactly one site-packages directory")
        if not wheel_path.is_relative_to(installation_root) or not wheel_path.is_file():
            raise NoetherRuntimeError("Noether wheel is absent or escapes installation_root")
        if len(wheel_hash) != 64 or _sha256(wheel_path) != wheel_hash:
            raise NoetherRuntimeError("Noether wheel SHA-256 does not match its manifest")
        if not isinstance(verification, dict):
            raise NoetherRuntimeError("Noether verification record must be an object")
        return cls(
            manifest_path=path,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            installation_root=installation_root,
            python=declared_python,
            python_runtime_root=python_runtime_root,
            site_packages=site_package_candidates[0],
            wheel=wheel_path,
            wheel_sha256=wheel_hash,
            installation_id=installation_id,
            version=version,
            source_commit=source_commit,
            verification=verification,
        )

    @property
    def read_only_mounts(self) -> tuple[Path, ...]:
        return (self.manifest_path, self.installation_root, self.python_runtime_root)

    def provenance(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "version": self.version,
            "source_commit": self.source_commit,
            "wheel_sha256": self.wheel_sha256,
            "runtime_python": str(self.python),
        }

    def invoke(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        if operation not in {*_COMPUTE_OPERATIONS, "capabilities", "describe"}:
            raise NoetherRuntimeError(f"unsupported Noether operation: {operation}")
        if not 1 <= timeout_seconds <= 120:
            raise NoetherRuntimeError("Noether timeout must be between 1 and 120 seconds")
        request_value = {"operation": operation, "payload": payload}
        request = canonical_json(request_value)
        if len(request.encode("utf-8")) > 2_000_000:
            raise NoetherRuntimeError("Noether request exceeds the 2,000,000-byte limit")
        worker = Path(__file__).with_name("noether_worker.py").resolve()
        environment = {
            "HOME": "",
            "PATH": "",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "REASONING_NOETHER_CPU_SECONDS": str(timeout_seconds),
            "REASONING_NOETHER_MEMORY_BYTES": str(4 * 1024**3),
        }
        try:
            with tempfile.TemporaryDirectory(prefix="reasoning-noether-") as temporary:
                environment["HOME"] = temporary
                if sandbox.enabled() and sandbox.available():
                    inside_installation = Path("/tmp/reasoning-noether-installation")
                    inside_python = Path("/tmp/reasoning-noether-python")
                    inside_worker = Path("/tmp/reasoning-noether-worker.py")
                    inside_site_packages = inside_installation / self.site_packages.relative_to(self.installation_root)
                    environment["REASONING_NOETHER_SITE_PACKAGES"] = str(inside_site_packages)
                    base_command = [
                        str(inside_python / "bin" / self.python.resolve().name),
                        "-I",
                        str(inside_worker),
                    ]
                    read_only = [
                        (self.installation_root, inside_installation),
                        (self.python_runtime_root, inside_python),
                        (worker, inside_worker),
                    ]
                else:
                    base_command = [str(self.python), "-I", str(worker)]
                    read_only = []
                command = sandbox.wrap(
                    [
                        "/usr/bin/env",
                        "-i",
                        *(f"{key}={value}" for key, value in environment.items()),
                        *base_command,
                    ],
                    rw=[Path(temporary)],
                    ro=read_only,
                    chdir=Path(temporary),
                    network=False,
                )
                completed = subprocess.run(
                    command,
                    input=request,
                    cwd=temporary,
                    env={**environment, "PATH": os.defpath},
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds + 5,
                    start_new_session=True,
                )
        except subprocess.TimeoutExpired as error:
            raise NoetherRuntimeError(f"Noether operation exceeded the {timeout_seconds}-second wall timeout") from error
        except OSError as error:
            raise NoetherRuntimeError(f"Noether worker could not start: {error}") from error
        if completed.returncode != 0 and not completed.stdout.strip():
            signal_number = (
                -completed.returncode
                if completed.returncode < 0
                else completed.returncode - 128
                if 128 < completed.returncode <= 255
                else None
            )
            if signal_number is not None:
                try:
                    signal_name = signal.Signals(signal_number).name
                except ValueError:
                    signal_name = f"signal {signal_number}"
                if signal_number == signal.SIGXCPU:
                    detail = f"Noether operation exceeded the {timeout_seconds}-second CPU limit ({signal_name})"
                else:
                    detail = f"Noether worker was terminated by {signal_name}"
            else:
                detail = f"Noether worker exited with status {completed.returncode}"
            stderr = completed.stderr.strip()[-1000:]
            if stderr:
                detail = f"{detail}: {stderr}"
            raise NoetherRuntimeError(detail)
        try:
            response = json.loads(completed.stdout)
        except ValueError as error:
            detail = completed.stderr.strip()[-1000:] or "no JSON response"
            raise NoetherRuntimeError(f"Noether worker protocol failure: {detail}") from error
        if not isinstance(response, dict) or not response.get("ok"):
            error = response.get("error", {}) if isinstance(response, dict) else {}
            error_type = str(error.get("type") or "NoetherError")
            detail = str(error.get("detail") or completed.stderr.strip() or "operation failed")
            raise NoetherRuntimeError(f"{error_type}: {detail}")
        if completed.returncode != 0:
            raise NoetherRuntimeError(f"Noether worker returned inconsistent status {completed.returncode}")
        return {
            "request": request_value,
            "request_sha256": hashlib.sha256(request.encode("utf-8")).hexdigest(),
            "result": response["result"],
            "provenance": self.provenance(),
        }


def register_noether_tools(
    registry: ToolRegistry,
    manifest_path: Path | str,
) -> NoetherRuntime:
    runtime = NoetherRuntime.load(manifest_path)

    @registry.tool(
        name="noether_status",
        description=(
            "Read the exact installed Noether version, immutable source/wheel provenance, "
            "and verification state before relying on a computation."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def noether_status() -> dict[str, Any]:
        capabilities = runtime.invoke("capabilities", {}, timeout_seconds=30)
        return {
            **capabilities,
            "verification": runtime.verification,
            "compute_operations": list(_COMPUTE_OPERATIONS),
        }

    @registry.tool(
        name="noether_describe",
        description=(
            "Describe one canonical operation in the installed versioned Noether surface, "
            "including signatures, schemas, determinism, backends, evidence, and example."
        ),
        input_schema={
            "type": "object",
            "properties": {"operation": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["operation"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def noether_describe(operation: str) -> dict[str, Any]:
        return runtime.invoke("describe", {"operation": operation}, timeout_seconds=30)

    @registry.tool(
        name="noether_compute",
        description=(
            "Run one bounded declarative exact computation in the pinned Noether runtime. "
            "Supported operations are factor_integer, binomial_invariants, "
            "multinomial_invariants, perfect_matching_polynomial, matrix_invariants, lattice_shortest_vectors, "
            "lattice_orthogonal_complements, integer_lattice_membership, integer_kernel_projection, "
            "ideal_invariants, simplicial_homology, and swarm_topology; Python and shell source "
            "are never accepted."
        ),
        input_schema=_COMPUTE_INPUT_SCHEMA,
        risk="read",
        capabilities=("science:compute",),
    )
    def noether_compute(
        operation: str,
        payload: dict[str, Any],
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        return runtime.invoke(operation, payload, timeout_seconds=timeout_seconds)

    edge_matrix_tool = registry.get("cartan_leray_h2_edge_matrix")

    @registry.tool(
        name="cartan_leray_h2_edge_lattice",
        description=(
            "Validate an integral C_m x C_m-equivariant cochain model, assemble "
            "the degree-two Cartan-Leray total-cocycle equations, and solve their "
            "marked H2 projection in the pinned Noether runtime. Returns the "
            "canonical edge-coefficient lattice and full total-cocycle lifts in "
            "one call without transferring the large intermediate matrix."
        ),
        input_schema=edge_matrix_tool.input_schema,
        risk="read",
        capabilities=("science:compute",),
    )
    def cartan_leray_h2_edge_lattice(**model: Any) -> dict[str, Any]:
        assembled = registry.invoke("cartan_leray_h2_edge_matrix", model)
        noether_request = assembled["noether_request"]
        solved = runtime.invoke(
            noether_request["operation"],
            noether_request["payload"],
        )
        projection = solved["result"]
        if projection.get("kernel_basis_included"):
            raise RuntimeError("composite edge computation unexpectedly returned the full kernel basis")
        return {
            "assembly": {key: value for key, value in assembled.items() if key != "noether_request"},
            "edge_coefficient_column_hnf_basis": projection["projected_column_hnf_basis"],
            "edge_coefficient_rank": projection["projected_rank"],
            "total_cocycle_lifts": projection["projected_basis_full_lifts"],
            "projection": projection,
            "noether": {
                "request_sha256": solved["request_sha256"],
                "provenance": solved["provenance"],
            },
            "certificate_verified": (assembled["certificate_verified"] and projection["certificate_verified"]),
        }

    return runtime
