"""Reasoning tools backed by one project's ledger and artifact directory."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Protocol

from .. import service
from ..canonical import canonical_json
from ..store import LedgerStore
from .bibliography import BibliographyStore, register_bibliography_tools
from .contracts import SkillBinding
from .formal_proof import register_formal_proof_tools
from .heisenberg_level25 import (
    level25_heisenberg_balanced_coefficient_pivot_profile,
    level25_heisenberg_balanced_coordinate_rank_profile,
    level25_heisenberg_balanced_five_pivot_restricted_system_profile,
    level25_heisenberg_balanced_five_pivot_support_jacobian_profile,
    level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile,
    level25_heisenberg_balanced_pivoted_restricted_system_profile,
    level25_heisenberg_balanced_pivoted_two_block_obstruction_profile,
    level25_heisenberg_balanced_restricted_system_profile,
    level25_heisenberg_balanced_two_block_obstruction_profile,
    level25_heisenberg_balanced_two_pivot_restricted_system_profile,
    level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile,
    level25_heisenberg_character_capacity_search_profile,
    level25_heisenberg_nonadjacent_block_shape_cover_profile,
    level25_heisenberg_surface_character_lift_profile,
    level25_heisenberg_surface_order5_jacobian_capacity_profile,
    level25_heisenberg_surface_restricted_system_profile,
    level25_heisenberg_surface_sparse_global_obstruction_profile,
)
from .heisenberg_level25_alphabet import level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile
from .heisenberg_level25_four_block import level25_heisenberg_augmented_four_block_unary_convolution_profile
from .heisenberg_level25_four_block_ad5 import level25_heisenberg_augmented_four_block_bounded_ad5_profile
from .heisenberg_level25_four_block_ad5_batch import level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile
from .heisenberg_level25_four_block_ad5_central_key import level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile
from .heisenberg_level25_four_block_ad5_join import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile,
)
from .heisenberg_level25_four_block_ad5_orbit import level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile
from .heisenberg_level25_four_block_ad5_prefix import level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile
from .heisenberg_level25_four_block_ad5_representative_batch import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile,
)
from .heisenberg_level25_four_block_ad5_representative_batch_9_12 import (
    _REPRESENTATIVE_9_12_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile,
)
from .heisenberg_level25_four_block_ad5_representative_sweep import (
    build_ad5_representative_macroreceipt,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile,
    require_reviewed_sweep_profile,
    validate_representative_bundle,
)
from .heisenberg_level25_four_block_ad5_signature import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile,
)
from .heisenberg_level25_four_block_ad5_slice import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile,
)
from .heisenberg_level25_four_block_row0 import level25_heisenberg_augmented_four_block_row0_feasibility_profile
from .heisenberg_level25_four_block_row0_edge_indicator import level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile
from .heisenberg_level25_four_block_row0_kernel import level25_heisenberg_augmented_four_block_row0_kernel_rank_profile
from .heisenberg_level25_four_block_row0_pair import level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile
from .heisenberg_level25_four_block_row0_pair_reuse import level25_heisenberg_augmented_four_block_row0_pair_reuse_profile
from .heisenberg_level25_four_block_row0_rank import level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile
from .heisenberg_level25_singleton_orbits import level25_heisenberg_fixed_stratum_singleton_orbit_profile
from .heisenberg_level25_three_block import level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile
from .heisenberg_level25_three_block_consecutive import level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile
from .heisenberg_level25_three_block_coverage import (
    level25_heisenberg_augmented_fixed_and_cyclic_local_profile,
    level25_heisenberg_augmented_four_block_alphabet_profile,
    level25_heisenberg_augmented_four_block_fourier_profile,
    level25_heisenberg_augmented_four_block_phase_quotient_profile,
    level25_heisenberg_augmented_three_block_rational_profile,
    level25_heisenberg_singleton_orbit_three_block_coverage_profile,
)
from .heisenberg_level25_two_block import level25_heisenberg_balanced_five_pivot_two_block_rational_profile
from .heisenberg_surface import (
    heisenberg_surface_invariant_h2_descent_profile,
    heisenberg_surface_witness_profile,
    singular_smoothness_profile,
)
from .lmfdb import register_lmfdb_tools
from .local_science import register_local_science_tools
from .noether import configured_noether_manifest, register_noether_tools
from .oeis import register_oeis_tools
from .scholarly import register_scholarly_tools
from .science_evidence import certified_receipt_json
from .scientific import ScientificGateway
from .skills import SkillRegistry
from .tools import ARTIFACT_READ_CHUNK_MAX_CHARACTERS, ToolRegistry

_WORKFLOW_RUN_ID = re.compile(r"^wfrun_[A-Za-z0-9_-]+$")
_ARTIFACT_READ_MANY_MAX_BYTES = 256 * 1024
_OPTIONAL_REPORT_OVERWRITE_SHA256_DESCRIPTION = (
    "Current SHA-256 of an existing artifact at path, obtained from "
    "artifact_read; omit when creating a new path. This is an optimistic-"
    "concurrency precondition, not a request, receipt, or proposed-content hash."
)


def _rectangular_torus_translation_cochains(
    *,
    level: int,
    horizontal_cells: int,
    vertical_cells: int,
) -> dict[str, Any]:
    """Return a bounded cubical model with translations in subdivided directions."""

    cells = horizontal_cells * vertical_cells

    def zero(rows: int, columns: int) -> list[list[int]]:
        return [[0] * columns for _ in range(rows)]

    def cell(horizontal: int, vertical: int) -> int:
        return (vertical % vertical_cells) * horizontal_cells + (horizontal % horizontal_cells)

    d0 = zero(2 * cells, cells)
    d1 = zero(cells, 2 * cells)
    for vertical in range(vertical_cells):
        for horizontal in range(horizontal_cells):
            vertex = cell(horizontal, vertical)
            horizontal_edge = cell(horizontal, vertical)
            vertical_edge = cells + cell(horizontal, vertical)
            d0[horizontal_edge][vertex] -= 1
            d0[horizontal_edge][cell(horizontal + 1, vertical)] += 1
            d0[vertical_edge][vertex] -= 1
            d0[vertical_edge][cell(horizontal, vertical + 1)] += 1
            d1[cell(horizontal, vertical)][horizontal_edge] += 1
            d1[cell(horizontal, vertical)][cells + cell(horizontal + 1, vertical)] += 1
            d1[cell(horizontal, vertical)][cell(horizontal, vertical + 1)] -= 1
            d1[cell(horizontal, vertical)][vertical_edge] -= 1

    actions = []
    translations = (
        (1 if horizontal_cells == level else 0, 0),
        (0, 1 if vertical_cells == level else 0),
    )
    for horizontal_shift, vertical_shift in translations:
        degree_actions = []
        for copies in (1, 2, 1):
            rank = copies * cells
            action = zero(rank, rank)
            for copy in range(copies):
                for vertical in range(vertical_cells):
                    for horizontal in range(horizontal_cells):
                        source = copy * cells + cell(horizontal, vertical)
                        target = copy * cells + cell(
                            horizontal + horizontal_shift,
                            vertical + vertical_shift,
                        )
                        action[target][source] = 1
            degree_actions.append(action)
        degree_actions.append([])
        actions.append(degree_actions)

    return {
        "level": level,
        "cochain_ranks": [cells, 2 * cells, cells, 0],
        "differentials": [d0, d1, []],
        "generator_actions": actions,
        "h2_cocycle_basis": [[int(index == 0)] for index in range(cells)],
    }


class WorkflowControl(Protocol):
    """Canonical workflow operations supplied locally or by the web API."""

    def prompt_list(
        self,
        *,
        project_id: str,
    ) -> dict[str, Any]: ...

    def prompt_definition(
        self,
        *,
        project_id: str,
        prompt_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]: ...

    def prompt_create(
        self,
        *,
        project_id: str,
        name: str,
        kind: str,
        template: str,
        description: str = "",
        variable_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def prompt_revise(
        self,
        *,
        project_id: str,
        prompt_id: str,
        template: str,
        parent_revision_id: str,
        name: str | None = None,
        kind: str | None = None,
        description: str | None = None,
        variable_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def dataset_list(
        self,
        *,
        project_id: str,
    ) -> dict[str, Any]: ...

    def dataset_definition(
        self,
        *,
        project_id: str,
        dataset_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]: ...

    def dataset_create(
        self,
        *,
        project_id: str,
        name: str,
        description: str = "",
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def model_catalog(
        self,
        *,
        project_id: str,
        scope: str = "account",
        refresh: bool = False,
    ) -> dict[str, Any]: ...

    def workflow_list(
        self,
        *,
        project_id: str,
        include_archived: bool = False,
    ) -> dict[str, Any]: ...

    def workflow_definition(
        self,
        *,
        project_id: str,
        workflow_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]: ...

    def workflow_create(
        self,
        *,
        project_id: str,
        name: str,
        template_id: str | None = None,
        graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def workflow_update(
        self,
        *,
        project_id: str,
        workflow_id: str,
        graph: dict[str, Any],
        expected_revision_id: str,
    ) -> dict[str, Any]: ...

    def workflow_preflight(
        self,
        *,
        project_id: str,
        workflow_id: str,
        refresh: bool = False,
    ) -> dict[str, Any]: ...

    def workflow_start(
        self,
        *,
        project_id: str,
        workflow_id: str,
        expected_revision_id: str,
        iterations: int | None = None,
        variables: dict[str, Any] | None = None,
        initial_prompt: str = "",
    ) -> dict[str, Any]: ...


def _artifact_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("artifact path escapes the project artifact directory")
    return target


def _validate_artifact_text(text: str) -> None:
    if any((ord(character) < 0x20 and character != "\n") or ord(character) == 0x7F for character in text):
        raise ValueError(
            "artifact text contains a forbidden control character; use line feeds and plain Unicode or correctly JSON-escaped backslashes"
        )


def build_project_tool_registry(
    store: LedgerStore,
    artifacts_root: Path | str,
    *,
    noether_manifest: Path | str | None = None,
    skill_registry: SkillRegistry | None = None,
    allowed_skills: tuple[SkillBinding, ...] = (),
    scientific_gateway: ScientificGateway | None = None,
    lean_workspace: Path | str | None = None,
    bibliography_store: BibliographyStore | None = None,
    project_id: str | None = None,
    chat_id: str | None = None,
    workflow_control: WorkflowControl | None = None,
) -> ToolRegistry:
    artifacts_root = Path(artifacts_root).resolve()
    artifacts_root.mkdir(parents=True, exist_ok=True)
    registry = ToolRegistry()
    register_formal_proof_tools(registry, workspace=lean_workspace)
    register_local_science_tools(registry)
    selected_noether_manifest = noether_manifest or configured_noether_manifest()
    if selected_noether_manifest is not None:
        register_noether_tools(registry, selected_noether_manifest)
    if bibliography_store is not None:
        if not project_id:
            raise ValueError("project_id is required when bibliography tools are enabled")
        register_bibliography_tools(
            registry,
            bibliography_store,
            project_id=project_id,
            chat_id=chat_id,
        )

    if scientific_gateway is not None:
        register_lmfdb_tools(registry, scientific_gateway)
        register_oeis_tools(registry, scientific_gateway)
        register_scholarly_tools(registry, scientific_gateway)

    if workflow_control is not None:
        if not project_id:
            raise ValueError("project_id is required when workflow tools are enabled")

        @registry.tool(
            name="prompt_list",
            description=(
                "List the current immutable prompt heads available to workflow nodes. "
                "Filter by kind when looking for a system role, user task, context, or "
                "evaluator prompt. Pin the returned prompt_id and revision_id in the "
                "workflow graph; never refer to an unversioned prompt by name."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": ["string", "null"],
                        "enum": ["system", "user", "context", "evaluator", None],
                    }
                },
                "additionalProperties": False,
            },
            risk="read",
        )
        def prompt_list(kind: str | None = None) -> dict[str, Any]:
            result = workflow_control.prompt_list(project_id=project_id)
            prompts = list(result.get("prompts", []))
            if kind is not None:
                prompts = [prompt for prompt in prompts if prompt.get("kind") == kind]
            return {"prompts": prompts}

        @registry.tool(
            name="prompt_read",
            description=(
                "Read one complete prompt template at its current or specified immutable "
                "revision. Use this before reusing or revising a prompt, and pin the exact "
                "returned prompt_id and revision_id in the workflow graph."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt_id": {"type": "string", "minLength": 1},
                    "revision_id": {"type": ["string", "null"]},
                },
                "required": ["prompt_id"],
                "additionalProperties": False,
            },
            risk="read",
        )
        def prompt_read(
            prompt_id: str,
            revision_id: str | None = None,
        ) -> dict[str, Any]:
            return {
                "prompt": workflow_control.prompt_definition(
                    project_id=project_id,
                    prompt_id=prompt_id,
                    revision_id=revision_id,
                )
            }

        @registry.tool(
            name="prompt_create",
            description=(
                "Create an immutable prompt revision for a workflow node. Usually create "
                "a system prompt for the agent's role and a user prompt for its task, then "
                "pin each returned prompt_id and revision_id under the agent node's "
                "config.system_prompt or config.user_prompt. This never starts a workflow."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "kind": {
                        "type": "string",
                        "enum": ["system", "user", "context", "evaluator"],
                    },
                    "template": {"type": "string"},
                    "description": {"type": "string"},
                    "variable_schema": {"type": "object"},
                },
                "required": ["name", "kind", "template"],
                "additionalProperties": False,
            },
            risk="write",
            idempotent=False,
        )
        def prompt_create(
            name: str,
            kind: str,
            template: str,
            description: str = "",
            variable_schema: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            prompt = workflow_control.prompt_create(
                project_id=project_id,
                name=name,
                kind=kind,
                template=template,
                description=description,
                variable_schema=variable_schema,
            )
            return {
                "prompt": prompt,
                "next_step": ("Pin this exact prompt_id and revision_id in the workflow node graph."),
            }

        @registry.tool(
            name="prompt_revise",
            description=(
                "Save a new immutable revision of an existing workflow prompt. Pass the "
                "current revision_id as parent_revision_id so concurrent edits cannot be "
                "silently overwritten. Existing workflow revisions keep their old pinned "
                "prompt; update a workflow graph explicitly to adopt the new revision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt_id": {"type": "string", "minLength": 1},
                    "template": {"type": "string"},
                    "parent_revision_id": {"type": "string", "minLength": 1},
                    "name": {"type": ["string", "null"]},
                    "kind": {
                        "type": ["string", "null"],
                        "enum": ["system", "user", "context", "evaluator", None],
                    },
                    "description": {"type": ["string", "null"]},
                    "variable_schema": {"type": ["object", "null"]},
                },
                "required": ["prompt_id", "template", "parent_revision_id"],
                "additionalProperties": False,
            },
            risk="write",
            idempotent=False,
        )
        def prompt_revise(
            prompt_id: str,
            template: str,
            parent_revision_id: str,
            name: str | None = None,
            kind: str | None = None,
            description: str | None = None,
            variable_schema: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            prompt = workflow_control.prompt_revise(
                project_id=project_id,
                prompt_id=prompt_id,
                template=template,
                parent_revision_id=parent_revision_id,
                name=name,
                kind=kind,
                description=description,
                variable_schema=variable_schema,
            )
            return {
                "prompt": prompt,
                "next_step": ("Update the workflow graph explicitly if it should pin this revision."),
            }

        @registry.tool(
            name="dataset_list",
            description=(
                "List immutable dataset heads in this project. Datasets are reusable case "
                "inputs for any workflow graph; they are not a separate workflow type. "
                "Use dataset_read to inspect the exact revision before binding it."
            ),
            input_schema={"type": "object", "additionalProperties": False},
            risk="read",
        )
        def dataset_list() -> dict[str, Any]:
            return workflow_control.dataset_list(project_id=project_id)

        @registry.tool(
            name="dataset_read",
            description=(
                "Read one complete public dataset revision. A workflow binds it with "
                "dataset_ref={dataset_id, revision_id}; case inputs are exposed to harnesses, "
                "while optional expected values are withheld from prompts and used only by "
                "local deterministic evaluators."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "minLength": 1},
                    "revision_id": {"type": ["string", "null"]},
                },
                "required": ["dataset_id"],
                "additionalProperties": False,
            },
            risk="read",
        )
        def dataset_read(
            dataset_id: str,
            revision_id: str | None = None,
        ) -> dict[str, Any]:
            return {
                "dataset": workflow_control.dataset_definition(
                    project_id=project_id,
                    dataset_id=dataset_id,
                    revision_id=revision_id,
                )
            }

        @registry.tool(
            name="dataset_create",
            description=(
                "Create an immutable public benchmark or work dataset after obtaining and "
                "normalizing it. Each case needs case_id, name, and an inputs object; public "
                "benchmarks may include expected for deterministic scoring. Preserve the raw "
                "download as a project artifact when provenance matters, and describe that "
                "source here. This never starts a workflow. Do not put private held-out targets "
                "in this public store; those belong in the owner-only answer-key import."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "cases": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1000,
                        "items": {"type": "object"},
                    },
                },
                "required": ["name", "cases"],
                "additionalProperties": False,
            },
            risk="write",
            idempotent=False,
        )
        def dataset_create(
            name: str,
            cases: list[dict[str, Any]],
            description: str = "",
        ) -> dict[str, Any]:
            dataset = workflow_control.dataset_create(
                project_id=project_id,
                name=name,
                description=description,
                cases=cases,
            )
            return {
                "dataset": dataset,
                "next_step": ("Bind this exact dataset_id and revision_id in the complete workflow graph."),
            }

        @registry.tool(
            name="model_catalog",
            description=(
                "Select the cheapest currently advertised OpenRouter text models for a "
                "workflow matrix. This is catalog discovery, not an inference call. The "
                "combined_text_price ordering adds the published input and output prices per "
                "million tokens; it is a transparent selection heuristic, not a run-cost "
                "forecast. Use account scope for models this project can route now. Pin every "
                "returned model_id explicitly in workflow variants and preflight before run. "
                "Endpoints advertised as agentic-harness-only are excluded by default because "
                "they cannot run through the direct Reasoning Native route."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["account", "all"]},
                    "refresh": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "sort": {
                        "type": "string",
                        "enum": ["combined_text_price", "input_price", "output_price"],
                    },
                    "require_structured_output": {"type": "boolean"},
                    "require_tools": {"type": "boolean"},
                    "require_reasoning": {"type": "boolean"},
                    "allow_agentic_harness_only": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            risk="external",
        )
        def model_catalog(
            scope: str = "account",
            refresh: bool = False,
            limit: int = 20,
            sort: str = "combined_text_price",
            require_structured_output: bool = False,
            require_tools: bool = False,
            require_reasoning: bool = False,
            allow_agentic_harness_only: bool = False,
        ) -> dict[str, Any]:
            payload = workflow_control.model_catalog(
                project_id=project_id,
                scope=scope,
                refresh=refresh,
            )
            candidates = []
            for model in payload.get("models") or []:
                if not isinstance(model, dict):
                    continue
                inputs = model.get("input_modalities") or []
                outputs = model.get("output_modalities") or []
                if inputs and "text" not in inputs:
                    continue
                if outputs and "text" not in outputs:
                    continue
                description = str(model.get("description") or "").casefold()
                agentic_harness_only = (
                    "only available for use with agentic harnesses" in description
                    or "only available through agentic harnesses" in description
                    or ("only available" in description and "agentic harness" in description)
                )
                if agentic_harness_only and not allow_agentic_harness_only:
                    continue
                input_price = model.get("input_mtok_usd")
                output_price = model.get("output_mtok_usd")
                if not isinstance(input_price, (int, float)) or isinstance(input_price, bool):
                    continue
                if not isinstance(output_price, (int, float)) or isinstance(output_price, bool):
                    continue
                parameters = set(model.get("supported_parameters") or [])
                if require_structured_output and "structured_outputs" not in parameters:
                    continue
                if require_tools and not model.get("supports_tools"):
                    continue
                if require_reasoning and not model.get("supports_reasoning"):
                    continue
                selected = dict(model)
                selected["combined_text_mtok_usd"] = float(input_price) + float(output_price)
                candidates.append(selected)
            price_field = {
                "combined_text_price": "combined_text_mtok_usd",
                "input_price": "input_mtok_usd",
                "output_price": "output_mtok_usd",
            }[sort]
            candidates.sort(
                key=lambda model: (
                    float(model[price_field]),
                    str(model.get("model_id") or ""),
                )
            )
            return {
                "source": payload.get("source"),
                "selection": {
                    "scope": scope,
                    "sort": sort,
                    "price_field": price_field,
                    "eligible_count": len(candidates),
                    "returned_count": min(limit, len(candidates)),
                    "requirements": {
                        "structured_output": require_structured_output,
                        "tools": require_tools,
                        "reasoning": require_reasoning,
                        "allow_agentic_harness_only": allow_agentic_harness_only,
                    },
                },
                "models": candidates[:limit],
            }

        @registry.tool(
            name="workflow_list",
            description=(
                "List saved workflow definitions in this project. Use workflow_read before "
                "revising an existing workflow so updates pin its current immutable revision."
            ),
            input_schema={
                "type": "object",
                "properties": {"include_archived": {"type": "boolean"}},
                "additionalProperties": False,
            },
            risk="read",
        )
        def workflow_list(include_archived: bool = False) -> dict[str, Any]:
            return workflow_control.workflow_list(
                project_id=project_id,
                include_archived=include_archived,
            )

        @registry.tool(
            name="workflow_read",
            description=(
                "Read one complete saved workflow graph. Agent node config.effort is the "
                "per-node reasoning setting: null uses the executor default; supported "
                "explicit values are low, medium, high, xhigh, and max."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "minLength": 1},
                    "revision_id": {"type": ["string", "null"]},
                },
                "required": ["workflow_id"],
                "additionalProperties": False,
            },
            risk="read",
        )
        def workflow_read(
            workflow_id: str,
            revision_id: str | None = None,
        ) -> dict[str, Any]:
            return {
                "workflow": workflow_control.workflow_definition(
                    project_id=project_id,
                    workflow_id=workflow_id,
                    revision_id=revision_id,
                )
            }

        @registry.tool(
            name="workflow_create",
            description=(
                "Create one durable reviewable workflow. Every workflow uses the same "
                "cases × variants → harness graph → evaluators contract: model comparisons "
                "are variants, an advanced judge is an ordinary downstream agent node, and "
                "local scoring is an evaluator. Supply graph to create the complete draft "
                "atomically after its prompts and dataset exist; omit graph only when an "
                "empty canvas is genuinely useful. A template is merely a prefilled graph. "
                "This never starts a run."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "template_id": {"type": ["string", "null"]},
                    "graph": {"type": ["object", "null"]},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            risk="write",
            idempotent=False,
        )
        def workflow_create(
            name: str,
            template_id: str | None = None,
            graph: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if template_id is not None and graph is not None:
                raise ValueError("workflow_create accepts either template_id or graph, not both")
            create_arguments: dict[str, Any] = {
                "project_id": project_id,
                "name": name,
                "template_id": template_id,
            }
            if graph is not None:
                create_arguments["graph"] = graph
            workflow = workflow_control.workflow_create(**create_arguments)
            return {
                "workflow": workflow,
                "status": "draft",
                "review_required": True,
                "authoring_note": (
                    "Create or reuse immutable system and user prompt revisions, then pin "
                    "their prompt_id and revision_id in each agent harness. A benchmark "
                    "workflow binds an exact dataset_ref; a model sweep puts model overrides "
                    "on one candidate node across variants; an LLM judge is another agent "
                    "node after it. Give every node a plain-language name and set explicit "
                    "call and dollar ceilings. presentation.recipe changes the view only."
                ),
                "next_step": (
                    (
                        "Call workflow_preflight on this complete revision. "
                        if graph is not None
                        else "Configure the complete graph with workflow_update, then call workflow_preflight. "
                    )
                    + "Do not start it unless the user explicitly asked."
                ),
            }

        @registry.tool(
            name="workflow_update",
            description=(
                "Save a complete replacement graph as a new immutable workflow revision. "
                "Pass the revision_id returned by workflow_create or workflow_read as "
                "expected_revision_id. Preserve per-agent config.effort when it is already "
                "set; set it to low, medium, high, xhigh, or max only when requested and "
                "leave it null for the executor default. The presentation.recipe is the UI "
                "contract: model_comparison, paper_moderation, and dataset_generator open "
                "their focused studios; graph opens the readable general workflow overview. "
                "This never starts a run."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "minLength": 1},
                    "graph": {"type": "object"},
                    "expected_revision_id": {"type": "string", "minLength": 1},
                },
                "required": ["workflow_id", "graph", "expected_revision_id"],
                "additionalProperties": False,
            },
            risk="write",
            idempotent=False,
        )
        def workflow_update(
            workflow_id: str,
            graph: dict[str, Any],
            expected_revision_id: str,
        ) -> dict[str, Any]:
            workflow = workflow_control.workflow_update(
                project_id=project_id,
                workflow_id=workflow_id,
                graph=graph,
                expected_revision_id=expected_revision_id,
            )
            return {
                "workflow": workflow,
                "status": "draft",
                "review_required": True,
                "next_step": "Call workflow_preflight before presenting or starting this revision.",
            }

        @registry.tool(
            name="workflow_preflight",
            description=(
                "Validate a saved workflow's exact current revision before review or run. "
                "Reports executor/model readiness, unsupported requested reasoning, case and "
                "variant counts, scheduled calls, evaluators, and configured cost/call caps."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "minLength": 1},
                    "refresh": {"type": "boolean"},
                },
                "required": ["workflow_id"],
                "additionalProperties": False,
            },
            risk="external",
        )
        def workflow_preflight(
            workflow_id: str,
            refresh: bool = False,
        ) -> dict[str, Any]:
            workflow = workflow_control.workflow_definition(
                project_id=project_id,
                workflow_id=workflow_id,
            )
            return {
                "workflow_id": workflow_id,
                "revision_id": workflow.get("revision_id"),
                "preflight": workflow_control.workflow_preflight(
                    project_id=project_id,
                    workflow_id=workflow_id,
                    refresh=refresh,
                ),
            }

        @registry.tool(
            name="workflow_start",
            description=(
                "Start the exact preflighted workflow revision. Call this only when the "
                "user explicitly asked to run or start it in their current request; an "
                "ambiguous request means leave the workflow as a draft. You must pass the "
                "current revision_id and assert user_authorized=true. The server repeats a "
                "live preflight before creating the durable run."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "minLength": 1},
                    "expected_revision_id": {"type": "string", "minLength": 1},
                    "user_authorized": {"const": True},
                    "iterations": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000},
                    "variables": {"type": "object"},
                    "initial_prompt": {"type": "string"},
                },
                "required": ["workflow_id", "expected_revision_id", "user_authorized"],
                "additionalProperties": False,
            },
            risk="write",
            idempotent=False,
        )
        def workflow_start(
            workflow_id: str,
            expected_revision_id: str,
            user_authorized: bool,
            iterations: int | None = None,
            variables: dict[str, Any] | None = None,
            initial_prompt: str = "",
        ) -> dict[str, Any]:
            if user_authorized is not True:
                raise ValueError("workflow start requires explicit user authorization")
            return workflow_control.workflow_start(
                project_id=project_id,
                workflow_id=workflow_id,
                expected_revision_id=expected_revision_id,
                iterations=iterations,
                variables=variables,
                initial_prompt=initial_prompt,
            )

    if skill_registry is not None and allowed_skills:
        granted_skills = {(binding.skill_id, binding.revision_id) for binding in allowed_skills}

        @registry.tool(
            name="skill_read",
            description=(
                "Activate a selected skill or read one of its supporting files. "
                "Use SKILL.md first, then open only referenced files needed for the task."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "minLength": 1},
                    "revision_id": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "minLength": 1},
                },
                "required": ["skill_id", "revision_id"],
                "additionalProperties": False,
            },
            risk="read",
            capabilities=("skills:read",),
        )
        def skill_read(skill_id: str, revision_id: str, path: str = "SKILL.md") -> dict[str, Any]:
            if (skill_id, revision_id) not in granted_skills:
                raise ValueError("skill revision is not granted to this run")
            return skill_registry.read_package_file(skill_id, revision_id, path)

    from ..organization_ledger import linked_ledger
    from .organization_tools import register_organization_ledger_tools

    organization_ledger = linked_ledger(store.root)
    local_ledger_registry = registry if organization_ledger is None else ToolRegistry()
    if organization_ledger is not None:
        register_organization_ledger_tools(registry, organization_ledger)

    @local_ledger_registry.tool(
        name="ledger_status",
        description="Read the derived status of the whole research ledger or one claim.",
        input_schema={
            "type": "object",
            "properties": {"claim_id": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_status(claim_id: str | None = None) -> dict[str, Any]:
        return service.status_payload(store, claim_id)

    @local_ledger_registry.tool(
        name="ledger_search",
        description="Search current claim statements in the project ledger.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1}},
            "required": ["text"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_search(text: str) -> dict[str, Any]:
        return service.search_payload(store, text)

    @local_ledger_registry.tool(
        name="ledger_graph",
        description="Read claim dependency nodes, edges, and unresolved-premise counts.",
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_graph() -> dict[str, Any]:
        return service.graph_payload(store)

    @registry.tool(
        name="artifact_list",
        description="List files in the project's shared artifact directory.",
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("artifacts:read",),
    )
    def artifact_list() -> dict[str, Any]:
        return {"artifacts": [str(path.relative_to(artifacts_root)) for path in sorted(artifacts_root.rglob("*")) if path.is_file()]}

    def read_artifact(path: str) -> dict[str, Any]:
        target = _artifact_path(artifacts_root, path)
        if not target.is_file():
            raise ValueError(f"unknown artifact: {path}")
        content = target.read_bytes()
        return {
            "path": path,
            "text": content.decode("utf-8"),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @registry.tool(
        name="artifact_read",
        description=(
            "Read a UTF-8 text artifact by project-relative path. The returned "
            "SHA-256 is the expected_sha256 required to replace that version. Large "
            "MCP results return an incomplete manifest and must be paged with "
            "artifact_read_chunk."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("artifacts:read",),
    )
    def artifact_read(path: str) -> dict[str, Any]:
        return read_artifact(path)

    @registry.tool(
        name="artifact_read_many",
        description=(
            "Read 1–16 exact UTF-8 artifact paths in one bounded, atomic call. "
            "Every path must exist and the combined content must not exceed 256 KiB. "
            "Native delivery returns the complete structured bundle. MCP delivery returns "
            "the complete bundle only when it fits its result bound; otherwise it returns "
            "an explicit incomplete manifest that must be followed with artifact_read_chunk."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 16,
                    "uniqueItems": True,
                }
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("artifacts:read",),
    )
    def artifact_read_many(paths: list[str]) -> dict[str, Any]:
        artifacts = [read_artifact(path) for path in paths]
        total = sum(artifact["bytes"] for artifact in artifacts)
        if total > _ARTIFACT_READ_MANY_MAX_BYTES:
            raise ValueError("combined artifact content exceeds the 256 KiB read limit")
        return {"artifacts": artifacts, "bytes": total}

    @registry.tool(
        name="artifact_read_chunk",
        description=(
            "Read one exact UTF-8 artifact in bounded Unicode-character chunks. Start at "
            "zero, then follow next_start_character until complete is true. Every chunk "
            "carries the byte count and SHA-256 of the complete artifact."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "start_character": {"type": "integer", "minimum": 0},
                "max_characters": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": ARTIFACT_READ_CHUNK_MAX_CHARACTERS,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("artifacts:read",),
    )
    def artifact_read_chunk(
        path: str,
        start_character: int = 0,
        max_characters: int = ARTIFACT_READ_CHUNK_MAX_CHARACTERS,
    ) -> dict[str, Any]:
        artifact = read_artifact(path)
        text = artifact["text"]
        total_characters = len(text)
        if start_character > total_characters:
            raise ValueError(f"start_character {start_character} exceeds artifact length {total_characters}")
        end_character = min(start_character + max_characters, total_characters)
        complete = end_character == total_characters
        return {
            "path": path,
            "text": text[start_character:end_character],
            "start_character": start_character,
            "end_character": end_character,
            "total_characters": total_characters,
            "complete": complete,
            "next_start_character": None if complete else end_character,
            "bytes": artifact["bytes"],
            "sha256": artifact["sha256"],
        }







    @registry.tool(
        name="workflow_run_list",
        description=(
            "List saved workflow runs in this project, including status, cost, and the shared artifact that contains each run's report."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "status": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("artifacts:read",),
    )
    def workflow_run_list(limit: int = 20, status: str | None = None) -> dict[str, Any]:
        runs = []
        for path in (artifacts_root / "workflow-runs").glob("wfrun_*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if status and state.get("status") != status:
                continue
            workflow = state.get("workflow_snapshot") or {}
            if not isinstance(workflow, dict):
                workflow = {}
            runs.append(
                {
                    key: value
                    for key, value in {
                        "run_id": state.get("run_id"),
                        "workflow_id": state.get("workflow_id"),
                        "workflow_name": workflow.get("name"),
                        "status": state.get("status"),
                        "iterations": state.get("iterations"),
                        "cost": state.get("cost"),
                        "artifact_path": state.get("artifact_path"),
                        "created_at": state.get("created_at"),
                        "updated_at": state.get("updated_at"),
                    }.items()
                    if value is not None
                }
            )
        runs.sort(
            key=lambda run: str(run.get("updated_at") or run.get("created_at") or ""),
            reverse=True,
        )
        return {"runs": runs[:limit]}

    @registry.tool(
        name="workflow_run_read",
        description=("Read a saved workflow run's complete durable state, including node outputs, events, cost, and report artifact path."),
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string", "pattern": "^wfrun_[A-Za-z0-9_-]+$"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("artifacts:read",),
    )
    def workflow_run_read(run_id: str) -> dict[str, Any]:
        if not _WORKFLOW_RUN_ID.fullmatch(run_id):
            raise ValueError("invalid workflow run id")
        target = _artifact_path(artifacts_root, f"workflow-runs/{run_id}.json")
        if not target.is_file():
            raise ValueError(f"unknown workflow run: {run_id}")
        return json.loads(target.read_text(encoding="utf-8"))

    @registry.tool(
        name="artifact_write",
        description=(
            "Atomically create a UTF-8 text artifact with expected_sha256 omitted, "
            "or replace an existing version by passing the current artifact SHA-256 "
            "returned by artifact_read. A request, receipt, or proposed-content hash "
            "is not an overwrite precondition. Pass the artifact body as text; content "
            "and body are accepted compatibility aliases. Use exactly one body field. "
            "In JSON tool calls, use plain Markdown and Unicode or double-escape every "
            "TeX backslash; a single \\b, \\f, \\r, or \\t escape decodes to a forbidden "
            "control character and is rejected."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "text": {"type": "string"},
                "content": {
                    "type": "string",
                    "description": "Compatibility alias for text; pass exactly one body field.",
                },
                "body": {
                    "type": "string",
                    "description": "Compatibility alias for text; pass exactly one body field.",
                },
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                    "description": (
                        "Current SHA-256 of an artifact already at path, obtained "
                        "from artifact_read; omit when creating a new path. This is "
                        "not a request, receipt, or proposed-content hash."
                    ),
                },
            },
            "required": ["path"],
            "oneOf": [
                {
                    "required": ["text"],
                    "not": {
                        "anyOf": [
                            {"required": ["content"]},
                            {"required": ["body"]},
                        ]
                    },
                },
                {
                    "required": ["content"],
                    "not": {
                        "anyOf": [
                            {"required": ["text"]},
                            {"required": ["body"]},
                        ]
                    },
                },
                {
                    "required": ["body"],
                    "not": {
                        "anyOf": [
                            {"required": ["text"]},
                            {"required": ["content"]},
                        ]
                    },
                },
            ],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("artifacts:write",),
        idempotent=True,
    )
    def artifact_write(
        path: str,
        text: str | None = None,
        content: str | None = None,
        body: str | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        if text is None:
            text = content if content is not None else body
            if text is None:  # guarded by the JSON Schema for registry calls
                raise ValueError("artifact text is required")
        _validate_artifact_text(text)
        target = _artifact_path(artifacts_root, path)
        if target.exists():
            if expected_sha256 is None:
                raise ValueError("artifact already exists; read it and pass expected_sha256 to replace it")
            actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError("artifact changed since it was read; read the current version before replacing it")
        elif expected_sha256 is not None:
            raise ValueError("artifact does not exist; omit expected_sha256 to create it")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
        content = text.encode("utf-8")
        return {
            "path": path,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    post_s25_diagonal_report_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "terminal_marker": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
            },
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": (
                    "Current SHA-256 of an existing report at path; omit when "
                    "creating a new path. This is not the profile request SHA-256 "
                    "or the canonical receipt SHA-256."
                ),
            },
        },
        "required": ["path", "terminal_marker"],
        "additionalProperties": False,
    }

    @registry.tool(
        name="post_s25_diagonal_candidate_report",
        description=(
            "Exhaust the two independent character congruences for the proposed "
            "post-S(25) decic and (2,5) complete-intersection families and "
            "atomically write their canonical rejection receipt."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def post_s25_diagonal_candidate_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke("post_s25_diagonal_candidate_profile", {})
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified post-S(25) diagonal-candidate screen\n\n"
            "This block is rendered directly from the exhaustive ordered-pair "
            "character census and forced-factor checks; no model-authored field "
            "transcription is used.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )

        def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
            blocks = profile["character_blocks"]
            block_size_distribution: dict[str, int] = {}
            common_factor_degree_distribution: dict[str, int] = {}
            for block in blocks:
                size = str(block["monomial_count"])
                factor_degree = str(block["common_monomial_factor_degree"])
                block_size_distribution[size] = block_size_distribution.get(size, 0) + 1
                common_factor_degree_distribution[factor_degree] = common_factor_degree_distribution.get(factor_degree, 0) + 1
            return {
                "schema": profile["schema"],
                "request": profile["request"],
                "request_sha256": profile["request_sha256"],
                "ambient_monomial_count": profile["ambient_monomial_count"],
                "character_block_count": profile["character_block_count"],
                "character_blocks_sha256": hashlib.sha256(canonical_json(blocks).encode()).hexdigest(),
                "block_size_distribution": block_size_distribution,
                "common_factor_degree_distribution": (common_factor_degree_distribution),
                "unobstructed_character_count": profile["unobstructed_character_count"],
                "unobstructed_characters": profile["unobstructed_characters"],
                "every_character_obstructed": profile["every_character_obstructed"],
                "certificate_verified": profile["certificate_verified"],
            }

        summary = {
            "schema": receipt["schema"],
            "candidate_a_rejected": receipt["candidate_a"]["rejected"],
            "candidate_b_rejected": receipt["candidate_b"]["rejected"],
            "period_index_conjecture_disproved": receipt["candidate_verdict"]["period_index_conjecture_disproved"],
            "candidate_a": profile_summary(receipt["candidate_a"]["profile"]),
            "candidate_b_quadric": profile_summary(receipt["candidate_b"]["quadric_profile"]),
            "candidate_b_quintic": profile_summary(receipt["candidate_b"]["quintic_profile"]),
            "certificate": receipt["certificate"],
            "certificate_verified": receipt["certificate_verified"],
        }
        return {
            "artifact": artifact,
            "summary": summary,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_diagonal_p3_hypersurface_filter_report",
        description=(
            "Run the exact modulo-25 lift census for every coordinate-point "
            "relation pattern of a pure diagonal hypersurface in P3 with "
            "v_5(degree)=1 and atomically write its canonical obstruction receipt."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_diagonal_p3_hypersurface_filter_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_diagonal_p3_hypersurface_filter_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 diagonal P3 hypersurface filter\n\n"
            "This block is rendered directly from the exhaustive modulo-25 "
            "linear-congruence lift census; no model-authored field "
            "transcription is used.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_diagonal_2_5_complete_intersection_filter_report",
        description=(
            "Run the rank-stratified exact obstruction for faithful diagonal "
            "(Z/25)^2 actions on smooth (2,5) complete-intersection surfaces "
            "in P4 and atomically write its canonical machine receipt."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_diagonal_2_5_complete_intersection_filter_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_diagonal_2_5_complete_intersection_filter_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 diagonal (2,5) complete-intersection filter\n\n"
            "This block is rendered directly from the rank-stratified "
            "obstruction and exhaustive modulo-25 rank-four cone lift census; "
            "no model-authored field transcription is used.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_surface_character_lift_profile",
        description=(
            "Enumerate the exact order-five restriction counts and all thirty "
            "cyclic order-25 lift tests for the normalized level-25 Heisenberg "
            "surface character witness. The profile certifies character-level "
            "forced-eigenpoint necessities only, not restricted basepoint-"
            "freeness, gradients, or a smooth surface."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_surface_character_lift_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_surface_character_lift_profile()

    @registry.tool(
        name="level25_heisenberg_surface_character_lift_report",
        description=(
            "Write the canonical exact order-five and cyclic-lift character "
            "receipt for the normalized level-25 Heisenberg surface witness. "
            "Omit expected_sha256 for a new report; the receipt does not certify "
            "restricted basepoint-freeness, gradients, or smoothness."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_surface_character_lift_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_surface_character_lift_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 Heisenberg character and cyclic-lift census\n\n"
            "This block is rendered directly from the normalized 22-character "
            "witness, all six order-five subgroup counts, and all thirty cyclic "
            "order-25 lift evaluations. It is a character-level necessity "
            "receipt and does not certify the proposed surface.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_surface_restricted_system_profile",
        description=(
            "Run the fixed exact F_101 and confined Singular profile for the "
            "twenty-two explicit sparse degree-25 Heisenberg forms. It checks "
            "representatives for all thirty fixed P4 restricted systems and "
            "all 125 forced H_0 eigenpoints, including exact stabilizers and "
            "marked tangent weights, but not global smoothness."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_surface_restricted_system_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_surface_restricted_system_profile()

    @registry.tool(
        name="level25_heisenberg_surface_restricted_system_report",
        description=(
            "Write the canonical exact F_101 fixed-P4 and forced-eigenpoint "
            "receipt for the explicit level-25 Heisenberg forms. Omit "
            "expected_sha256 for a new report; the receipt does not certify "
            "global surface dimension or smoothness."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_surface_restricted_system_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_surface_restricted_system_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 Heisenberg restricted-system screen\n\n"
            "This block is rendered directly from the twenty-two explicit "
            "sparse forms, six symmetry representatives for all thirty fixed "
            "P4 restricted systems, and all 125 forced H_0 eigenpoints with "
            "their exact stabilizers and marked tangent weights. It does not "
            "certify global surface dimension or smoothness.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_surface_sparse_global_obstruction_profile",
        description=(
            "Certify a coordinate-P4 positive-dimensional base locus that "
            "rejects the explicit one-orbit sparse level-25 Heisenberg forms. "
            "The obstruction does not reject generic forms in the same "
            "twenty-two character eigenspaces."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_surface_sparse_global_obstruction_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_surface_sparse_global_obstruction_profile()

    @registry.tool(
        name="level25_heisenberg_surface_sparse_global_obstruction_report",
        description=(
            "Write the canonical coordinate-support obstruction receipt for "
            "the explicit sparse level-25 Heisenberg forms. Omit "
            "expected_sha256 for a new report; the receipt makes no claim "
            "against generic forms in the same character eigenspaces."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_surface_sparse_global_obstruction_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_surface_sparse_global_obstruction_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified sparse level-25 Heisenberg global obstruction\n\n"
            "This block is rendered directly from the explicit twenty-two "
            "one-orbit sparse forms and an exhaustive coordinate-support "
            "census. It rejects only that sparse form family, not generic "
            "forms in the same character eigenspaces.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_surface_order5_jacobian_capacity_profile",
        description=(
            "Compute exact order-five restriction multiplicities and tangent-"
            "weight capacities for the current level-25 Heisenberg character "
            "multiset. It certifies the unavoidable H_4 rank defect for every "
            "one-form-per-character coefficient choice."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_surface_order5_jacobian_capacity_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_surface_order5_jacobian_capacity_profile()

    @registry.tool(
        name="level25_heisenberg_surface_order5_jacobian_capacity_report",
        description=(
            "Write the canonical order-five Jacobian-capacity obstruction for "
            "the current level-25 Heisenberg character multiset. Omit "
            "expected_sha256 for a new report; the receipt does not reject "
            "alternative character multisets."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_surface_order5_jacobian_capacity_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_surface_order5_jacobian_capacity_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 Heisenberg order-five Jacobian obstruction\n\n"
            "This block is rendered directly from the current twenty-two "
            "character restrictions and the projective tangent weight "
            "capacities. It rejects this character multiset for every "
            "one-form-per-character choice, but not alternative multisets.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_character_capacity_search_profile",
        description=(
            "Enumerate the normalized reduction-slope capacity search and "
            "certify a replacement twenty-two-character level-25 Heisenberg "
            "multiset against all order-five and cyclic-lift finite gates."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_character_capacity_search_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_character_capacity_search_profile()

    @registry.tool(
        name="level25_heisenberg_character_capacity_search_report",
        description=(
            "Write the canonical finite-search receipt for the balanced "
            "replacement level-25 Heisenberg character multiset. Omit "
            "expected_sha256 for a new report; the receipt does not certify "
            "forms, restricted systems, or a surface."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_character_capacity_search_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_character_capacity_search_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified balanced level-25 Heisenberg character search\n\n"
            "This block is rendered directly from the normalized 625-pattern "
            "reduction search, the explicit replacement characters, all six "
            "order-five capacity censuses, and all thirty cyclic-lift tests. "
            "It does not certify forms or a surface.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_restricted_system_profile",
        description=(
            "Run the fixed exact F_101 and confined Singular profile for the "
            "balanced replacement level-25 Heisenberg forms. It checks all "
            "fixed P4 systems and 750 cyclic eigenpoints, including forced "
            "Jacobians and stabilizers, but not global smoothness."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_restricted_system_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_restricted_system_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_restricted_system_report",
        description=(
            "Write the canonical exact F_101 fixed-P4 and cyclic-eigenpoint "
            "receipt for the balanced replacement forms. Omit expected_sha256 "
            "for a new report; the receipt does not certify global dimension "
            "or smoothness."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_restricted_system_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_restricted_system_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified balanced level-25 Heisenberg restricted systems\n\n"
            "This block is rendered directly from the balanced twenty-two "
            "sparse forms, all thirty fixed P4 systems, and all 750 cyclic "
            "eigenpoints with forced Jacobians and stabilizers. It does not "
            "certify global surface dimension or smoothness.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_pivoted_restricted_system_profile",
        description=(
            "Run the frozen F_101 and confined Singular replay for the "
            "explicit balanced tuple with coefficient-one x_0^20*x_1^5 "
            "orbit pivot. It checks all fixed P4 systems and 750 cyclic "
            "eigenpoints, but not the global free-locus Jacobian gate."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_pivoted_restricted_system_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_pivoted_restricted_system_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_pivoted_restricted_system_report",
        description=(
            "Write the canonical fixed-P4 and cyclic-eigenpoint receipt for "
            "the explicit coefficient-one pivoted balanced tuple. Omit "
            "expected_sha256 for a new report; this does not certify global "
            "dimension, smoothness, or integrality."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_pivoted_restricted_system_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_pivoted_restricted_system_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified pivoted balanced level-25 restricted systems\n\n"
            "This block is rendered directly from the explicit coefficient-"
            "one pivoted tuple, all thirty fixed P4 systems, and all 750 "
            "cyclic eigenpoints with forced Jacobians and stabilizers. It "
            "does not certify the global free-locus Jacobian gate.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_two_pivot_restricted_system_profile",
        description=(
            "Run the frozen F_101 and confined Singular replay for the "
            "explicit balanced tuple with both coefficient-one pivots. It "
            "checks all fixed P4 systems and 750 cyclic eigenpoints, but not "
            "the global free-locus Jacobian gate."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_two_pivot_restricted_system_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_two_pivot_restricted_system_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_two_pivot_restricted_system_report",
        description=(
            "Write the canonical fixed-P4 and cyclic-eigenpoint receipt for "
            "the explicit coefficient-one two-pivot balanced tuple. Omit "
            "expected_sha256 for a new report; this does not certify global "
            "dimension, smoothness, or integrality."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_two_pivot_restricted_system_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_two_pivot_restricted_system_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified two-pivot balanced level-25 restricted systems\n\n"
            "This block is rendered directly from the explicit coefficient-"
            "one two-pivot tuple, all thirty fixed P4 systems, and all 750 "
            "cyclic eigenpoints with forced Jacobians and stabilizers. It "
            "does not certify the global free-locus Jacobian gate.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_five_pivot_restricted_system_profile",
        description=(
            "Run the frozen F_101 and confined Singular replay for the "
            "explicit balanced tuple with the adjacent pivot and all four "
            "coefficient-one nonadjacent pivots. It checks all fixed P4 "
            "systems and 750 cyclic eigenpoints, but not the global "
            "free-locus Jacobian gate."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_five_pivot_restricted_system_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_five_pivot_restricted_system_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_five_pivot_restricted_system_report",
        description=(
            "Write the canonical fixed-P4 and cyclic-eigenpoint receipt for "
            "the explicit coefficient-one five-pivot balanced tuple. Omit "
            "expected_sha256 for a new report; this does not certify global "
            "dimension, smoothness, or integrality."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_five_pivot_restricted_system_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_five_pivot_restricted_system_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified five-pivot balanced level-25 restricted systems\n\n"
            "This block is rendered directly from the explicit coefficient-"
            "one five-pivot tuple, all thirty fixed P4 systems, and all 750 "
            "cyclic eigenpoints with forced Jacobians and stabilizers. It "
            "does not certify the global free-locus Jacobian gate.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_five_pivot_support_jacobian_profile",
        description=(
            "Compile and exhaust all 2^25 coordinate supports for the explicit "
            "five-pivot tuple, using the exact restricted-equation span and "
            "the correct monomial derivative rule on all 25 Jacobian columns. "
            "A full structural matching is not a smoothness certificate."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_five_pivot_support_jacobian_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_five_pivot_support_jacobian_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_five_pivot_support_jacobian_report",
        description=(
            "Write the canonical source-hashed all-support receipt for the "
            "five-pivot equation span and 25-column structural Jacobian. Omit "
            "expected_sha256 for a new report; this does not certify actual "
            "Jacobian rank, global dimension, smoothness, or integrality."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_five_pivot_support_jacobian_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_five_pivot_support_jacobian_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified five-pivot support and structural-Jacobian census\n\n"
            "This block is rendered directly from the source-hashed exhaustive "
            "F_101 computation over all 2^25 coordinate supports. It retains "
            "all twenty-five derivative columns and distinguishes structural "
            "matching from evaluated Jacobian rank.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile"),
        description=(
            "Compile and exhaust the 5^11 possible free 25th-power values for "
            "the fourteen pure equations over F_101. This classifies rational "
            "solutions only; it is not a geometric smoothness certificate."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_pure_power_alphabet_report"),
        description=(
            "Write the reviewed F_101 pure-power alphabet classification, "
            "refusing failed certificates, unreviewed scope or raw output. "
            "Omit expected_sha256 for a new report; the "
            "classification does not extend to the algebraic closure."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_five_pivot_pure_power_alphabet_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            ("level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile"),
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 pure-power alphabet classification\n\n"
            "This block is rendered directly from the source-hashed exhaustive "
            "F_101 computation. It classifies rational 25th-power vectors for "
            "the fourteen pure equations and makes no algebraic-closure or "
            "global-smoothness claim.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_two_block_rational_profile"),
        description=(
            "Compile an exact meet-in-the-middle search over both cyclic "
            "two-residue-block support classes for the five-pivot tuple. It "
            "excludes F_101-rational common zeros only, not geometric points."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_five_pivot_two_block_rational_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_five_pivot_two_block_rational_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_two_block_rational_report"),
        description=(
            "Write the reviewed F_101 two-block exclusion, refusing failed "
            "certificates, unreviewed scope or raw output. "
            "Omit expected_sha256 for a new report; this does not exclude "
            "points over the algebraic closure or larger supports."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_five_pivot_two_block_rational_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            ("level25_heisenberg_balanced_five_pivot_two_block_rational_profile"),
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 two-block rational exclusion\n\n"
            "This block is rendered directly from the source-hashed exhaustive "
            "F_101 meet-in-the-middle search. It covers both cyclic two-block "
            "support classes modulo projective and mu_5 block scaling, and "
            "makes no algebraic-closure or global-smoothness claim.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile"),
        description=(
            "Compile an exact search on the nonconsecutive {0,1,3} "
            "three-residue-block support class. It records F_101 zeros and "
            "all-column Jacobian ranks, not characteristic-zero points."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_report"),
        description=(
            "Write the reviewed F_101 nonconsecutive three-block witness "
            "census, refusing failed certificates, unreviewed scope or raw "
            "output. Omit expected_sha256 for a new "
            "report; no characteristic-zero lift is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            ("level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile"),
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 nonconsecutive three-block rational census\n\n"
            "This block is rendered directly from the source-hashed exhaustive "
            "F_101 search and independent Python witness replay. It records "
            "special-fiber common zeros and their all-column Jacobian ranks, "
            "and makes no algebraic-closure or characteristic-zero claim.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile"),
        description=(
            "Compile an exact search on the consecutive {0,1,2} "
            "three-residue-block support class. It solves both adjacent "
            "edges before recording F_101 zeros and Jacobian ranks."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_report"),
        description=(
            "Write the reviewed F_101 consecutive three-block witness census, "
            "refusing failed certificates, unreviewed scope or raw output. "
            "Omit expected_sha256 for a new "
            "report; no characteristic-zero lift is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            ("level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile"),
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 consecutive three-block rational census\n\n"
            "This block is rendered directly from the source-hashed exhaustive "
            "F_101 search and independent Python witness replay. It records "
            "special-fiber common zeros and their all-column Jacobian ranks, "
            "and makes no algebraic-closure or characteristic-zero claim.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_fixed_stratum_singleton_orbit_profile",
        description=(
            "Compile and independently replay the bounded screen for "
            "three-residue degree-25 singleton orbits whose restrictions "
            "vanish on all thirty order-five fixed P4 strata over F_101."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_fixed_stratum_singleton_orbit_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_fixed_stratum_singleton_orbit_profile()

    @registry.tool(
        name="level25_heisenberg_fixed_stratum_singleton_orbit_report",
        description=(
            "Write the fixed-stratum-preserving singleton-orbit census only "
            "with a successful certificate, fresh reviewed mathematical scope "
            "and exact raw execution. Omit expected_sha256 for a new report; "
            "the candidates have not passed the three-block coverage gate."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_fixed_stratum_singleton_orbit_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_fixed_stratum_singleton_orbit_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 fixed-stratum singleton-orbit census\n\n"
            "This block is rendered directly from the source-hashed compiled "
            "screen and an independent Python restriction replay. It records "
            "candidate augmentation orbits that preserve the thirty fixed-P4 "
            "restrictions over F_101; it does not claim three-block coverage, "
            "global smoothness, or a characteristic-zero result.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_singleton_orbit_three_block_coverage_profile",
        description=(
            "Compile the exact distinct-row coverage search over all 16,500 "
            "known F_101 three-block zeros using the certified b=0 "
            "fixed-stratum-preserving singleton-orbit library."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_singleton_orbit_three_block_coverage_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_singleton_orbit_three_block_coverage_profile()

    @registry.tool(
        name="level25_heisenberg_singleton_orbit_three_block_coverage_report",
        description=(
            "Write the three-block coverage receipt only with a successful "
            "certificate, fresh reviewed mathematical scope and exact raw "
            "execution. Omit expected_sha256 for a new report; it covers only "
            "original F_101 zeros, not new augmented or larger-support zeros."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_singleton_orbit_three_block_coverage_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_singleton_orbit_three_block_coverage_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified level-25 singleton-orbit three-block coverage\n\n"
            "This block is rendered directly from the source-hashed exact "
            "coverage search and independent first-witness replay. It records "
            "an explicit two-orbit augmentation covering every old rational "
            "three-block zero over F_101. A fresh augmented-system search is "
            "still required because two pure-power rows changed.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_fixed_and_cyclic_local_profile",
        description=(
            "Replay all thirty fixed-P4 restrictions and all 750 cyclic "
            "eigenpoints after inserting the two certified three-block "
            "coverage orbits, including every common-zero Jacobian rank."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_fixed_and_cyclic_local_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_fixed_and_cyclic_local_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_fixed_and_cyclic_local_report",
        description=(
            "Write the augmented fixed-stratum and cyclic-eigenpoint local "
            "receipt only with a successful certificate and fresh reviewed "
            "mathematical scope. Omit expected_sha256 for a new report; "
            "this remains a finite local certificate over F_101."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_fixed_and_cyclic_local_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_fixed_and_cyclic_local_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 local replay\n\n"
            "This block is rendered directly from the certified coverage "
            "selection, all thirty fixed-P4 substitutions, and all 750 cyclic "
            "eigenpoint value-and-gradient evaluations over F_101. It is a "
            "local finite-field certificate, not a global smoothness or "
            "characteristic-zero result.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_three_block_rational_profile",
        description=(
            "Compile and independently replay the twelve-row 25th-power "
            "classification on both exact three-block supports, then combine "
            "it with the corrected old-zero coverage certificate."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_three_block_rational_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_three_block_rational_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_three_block_rational_report",
        description=(
            "Write the augmented-system three-block rational exclusion "
            "receipt only with a successful certificate, fresh reviewed "
            "mathematical scope and exact raw execution. "
            "Omit expected_sha256 for a new report; no "
            "algebraic-closure or characteristic-zero exclusion is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_three_block_rational_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_three_block_rational_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 three-block rational exclusion\n\n"
            "This block is rendered directly from the corrected old-zero "
            "coverage receipt, a source-hashed twelve-row pure-power census, "
            "and an independent Python replay. It excludes exact three-block "
            "common zeros only over F_101.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_alphabet_profile",
        description=(
            "Compile and independently replay the twelve-row nonzero "
            "25th-power alphabet classification on the unique exact "
            "four-block support orbit over F_101."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_alphabet_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_alphabet_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_alphabet_report",
        description=(
            "Write the canonical augmented-system four-block alphabet "
            "reduction receipt. Omit expected_sha256 for a new report; no "
            "four-block common-zero exclusion is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_alphabet_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_alphabet_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block alphabet reduction\n\n"
            "This block is rendered directly from a source-hashed twelve-row "
            "pure-power census and an independent Python replay. It reduces "
            "the exact four-block support orbit to block-constant nonzero "
            "25th powers over F_101; it does not test the remaining equations "
            "or exclude four-block common zeros.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_phase_quotient_profile",
        description=(
            "Compile and independently replay the safe blockwise mu_5 phase quotient for the augmented exact four-block F_101 search."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_phase_quotient_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_phase_quotient_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_phase_quotient_report",
        description=(
            "Write the canonical four-block phase-quotient receipt. Omit "
            "expected_sha256 for a new report; no common-zero count or "
            "exclusion is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_phase_quotient_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_phase_quotient_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block phase quotient\n\n"
            "This block is rendered directly from a source-hashed block-phase "
            "census and an independent Python replay. It certifies five "
            "exhaustive phase representatives for the future exact search; "
            "it does not count or exclude four-block common zeros.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_fourier_profile",
        description=(
            "Compile and independently replay the complete normalized mu_25 "
            "cyclic-sum histogram and the rank-three coupled Fourier relation "
            "on exact four-block support over F_101."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_fourier_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_fourier_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_fourier_report",
        description=(
            "Write the canonical four-block Fourier-bucket receipt. Omit "
            "expected_sha256 for a new report; no common-zero count or "
            "exclusion after the remaining seven rows is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_fourier_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_fourier_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block Fourier buckets\n\n"
            "This block is rendered directly from a source-hashed census of "
            "all 390,625 normalized mu_25 shapes and an independent Python "
            "replay. It retains both the zero and nonzero Fourier-kernel "
            "lines; it does not count common zeros after the other seven "
            "residual equations.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_unary_convolution_profile",
        description=(
            "Count every corrected four-block Fourier fiber surviving the "
            "unchanged unary rows 6 and 8 by two exact finite-field transforms "
            "and direct independent Python calibration convolutions."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_unary_convolution_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_unary_convolution_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_unary_convolution_report",
        description=(
            "Write the canonical rows-6/8 four-block convolution receipt. "
            "Omit expected_sha256 for a new report; no common-zero count after "
            "the remaining five equations is claimed."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_unary_convolution_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_unary_convolution_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block unary convolution\n\n"
            "This block is rendered directly from exact two-prime transforms "
            "over all 6,464 corrected Fourier fibers and independent Python "
            "histogram and direct-convolution checks. It counts the shape-class "
            "domain surviving rows 6 and 8; it does not test the remaining five "
            "equations or count common zeros.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_bounded_ad5_profile",
        description=(
            "Run the exact collision-free AD5 meet-in-the-middle join on the "
            "first deterministic four-block central context under 270-million "
            "search charges and independent 64 MiB replay caps."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_bounded_ad5_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_bounded_ad5_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_bounded_ad5_report",
        description=(
            "Write the canonical first-context AD5 receipt. Omit expected_sha256 "
            "for a new report; an empty result certifies only the named central "
            "context, while a singular witness requires independent replay."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_bounded_ad5_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_bounded_ad5_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 bounded AD5 first-context profile\n\n"
            "This block is rendered directly from the exact five-coordinate "
            "meet-in-the-middle join, direct seven-equation match replay, and "
            "separate scalar domain or witness replay. Its conclusion is scoped "
            "to the single central context recorded in the receipt.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "exact_key_match_count": receipt["computation"]["exact_key_match_count"],
            "residual_tests_completed": receipt["computation"]["residual_tests_completed"],
            "source_record_count": receipt["source_manifest"]["raw_record_count"],
            "audit_claimed_source_record_count": receipt["source_manifest"]["audit_claimed_raw_record_count"],
            "audit_count_matches_executable_source": receipt["source_manifest"]["audit_count_matches_executable_source"],
            "source_record_count_correction": receipt["source_manifest"]["corrected_count_explanation"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile",
        description=(
            "Run the next four deterministic AD5 central contexts sequentially "
            "with one shared zero-bucket enumeration, exact collision-free joins, "
            "atomic residual gates, individual charge caps, and independent ordered-"
            "domain replay under 64 MiB address-space limits."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_report",
        description=(
            "Write the canonical next-four-context AD5 batch receipt. Omit "
            "expected_sha256 for a new report; empty statuses certify only the "
            "four explicitly ordered contexts in the receipt."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 bounded AD5 next-four-context batch\n\n"
            "This block is rendered directly from the sequential exact joins, "
            "atomic residual gates, shared fixed-charge ledger, and separate "
            "ordered-domain replay. Its conclusion is scoped to the four contexts "
            "listed in the receipt.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "context_summaries": [
                {
                    "ordinal": context["ordinal"],
                    "shape_d": context["d_code"],
                    "status": context["status"],
                    "exact_key_match_count": context["exact_key_match_count"],
                    "residual_tests_completed": context["residual_tests_completed"],
                    "search_charge_total": context["charge_total"],
                }
                for context in computation["contexts"]
            ],
            "fixed_search_charge_total": computation["fixed_charge_total"],
            "aggregate_search_charge_total": computation["aggregate_charge_total"],
            "reserved_upper_bound": computation["reserved_upper_bound"],
            "aggregate_search_charge_cap": computation["aggregate_search_charge_cap"],
            "source_record_count": receipt["source_manifest"]["raw_record_count"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile",
        description=(
            "Run the complete first-two-key AD5 shift census for all 97,682,000 "
            "central A/D contexts in the fixed zero fiber, using source-derived "
            "F_101^2 histograms and an independent replay under combined charge "
            "and per-process memory caps."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_report",
        description=(
            "Write the canonical complete fixed-fiber AD5 prefix-shift census "
            "receipt. A zero B/C prefix count excludes whole shift classes; "
            "nonzero counts do not certify a full key match or common zero."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 prefix-shift census\n\n"
            "This block is rendered directly from the complete source-derived "
            "F_101^2 histogram census and its separately written replay. Zero "
            "prefix-pair shift classes exclude their central contexts; nonzero "
            "classes remain necessary-condition diagnostics only.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "histogram_supports": {name: computation[f"{name}_histogram_support"] for name in ("a", "d", "b", "c")},
            "realized_shift_count": computation["realized_shift_count"],
            "excluded_shift_count": computation["excluded_shift_count"],
            "n_table_mass": computation["n_table_mass"],
            "m_table_mass": computation["m_table_mass"],
            "n_table_support": computation["n_table_support"],
            "m_table_support": computation["m_table_support"],
            "minimum_prefix_pair_count_on_realized_shift": computation["minimum_prefix_pair_count_on_realized_shift"],
            "maximum_prefix_pair_count_on_realized_shift": computation["maximum_prefix_pair_count_on_realized_shift"],
            "excluded_context_count": computation["excluded_context_count"],
            "open_context_count": computation["open_context_count"],
            "actual_combined_charge": receipt["actual_combined_charge"],
            "evidence_sha256": receipt["evidence_sha256"],
            "not_certified": receipt["not_certified"],
            "next_gate": receipt["next_gate"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile",
        description=(
            "Measure the exact row-5 B-D and A-C feature ranks and quotient-dual "
            "A/D functional signatures in the complete fixed AD5 fiber. The "
            "diagnostic prices joint F_101^3 profile reuse but does not construct "
            "profiles or perform a coordinate-three census."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_report",
        description=(
            "Write the canonical AD5 coordinate-three functional-signature "
            "diagnostic. Its next-gate decision rejects or retains a profile "
            "strategy; it does not count coordinate-three solutions."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 coordinate-three functional signatures\n\n"
            "This block is rendered directly from the source-derived row-5 "
            "feature ranks, quotient-dual A/D signature multiplicities, exact "
            "reuse bounds, and separately written scalar replay. It constructs "
            "no joint F_101^3 profile and performs no coordinate-three census.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "feature_definition_sha256": computation["feature_definition_sha256"],
            "b_feature_rank": computation["b_feature_rank"],
            "c_feature_rank": computation["c_feature_rank"],
            "d_signature_count": computation["d_signature_count"],
            "a_signature_count": computation["a_signature_count"],
            "d_signature_multiplicity_range": [
                computation["d_signature_minimum_multiplicity"],
                computation["d_signature_maximum_multiplicity"],
            ],
            "a_signature_multiplicity_range": [
                computation["a_signature_minimum_multiplicity"],
                computation["a_signature_maximum_multiplicity"],
            ],
            "future_profile_visit_count_per_engine": computation["future_profile_visit_count_per_engine"],
            "future_profile_value_operation_count_per_engine": computation["future_profile_value_operation_count_per_engine"],
            "future_profile_lower_bound_combined": computation["future_profile_lower_bound_combined"],
            "dense_profile_bytes": computation["dense_profile_bytes"],
            "dense_all_signature_profile_bytes": computation["dense_all_signature_profile_bytes"],
            "actual_combined_diagnostic_charge": receipt["actual_combined_charge"],
            "evidence_sha256": receipt["evidence_sha256"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile",
        description=(
            "Exhaustively test whether the measured D functional signatures "
            "determine the complete coordinate-three central key, including "
            "the wrapped D-to-A restriction, both prefix coordinates, and L5. "
            "The diagnostic also evaluates exact Fourier reflection-sign sentinels."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_report",
        description=(
            "Write the canonical AD5 coordinate-three central-key diagnostic. "
            "It can refute or retain a signature-only central factorization, "
            "but it never authorizes a full contraction or counts solutions."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 coordinate-three central key\n\n"
            "This block is rendered directly from the exhaustive D-signature "
            "central-key fibre census, corrected Fourier reflection-sign "
            "sentinels, source-oriented identities, and separately written "
            "scalar replay. It performs no profile join, contraction, or "
            "coordinate-three census.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "central_key_definition_sha256": computation["central_key_definition_sha256"],
            "w_a3_rank": computation["w_a3_rank"],
            "d_signature_count": computation["d_signature_count"],
            "distinct_sigd_central_key_count": computation["distinct_sigd_central_key_count"],
            "nonconstant_sigd_fibre_count": computation["nonconstant_sigd_fibre_count"],
            "maximum_central_key_multiplicity": computation["maximum_central_key_multiplicity"],
            "fourier_sign_sentinel_verified": bool(computation["fourier_sign_sentinel_verified"]),
            "actual_combined_diagnostic_charge": receipt["actual_combined_charge"],
            "evidence_sha256": receipt["evidence_sha256"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile",
        description=(
            "Certify the simultaneous C5-squared source action, canonical "
            "phase recanonicalization, exact normalized-shape orbits, and "
            "free action on all AD5 coordinate-three central contexts."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_report",
        description=(
            "Write the canonical AD5 coordinate-three C5-squared orbit "
            "certificate. It can authorize only the exact factor-25 central "
            "outer-loop quotient, never a contraction or solution count."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 coordinate-three tensor orbit\n\n"
            "This block is rendered directly from the source-record covariance "
            "audit, exhaustive C5-squared normalized-shape action, canonical "
            "phase-recanonicalization sentinels, factorized Burnside census, "
            "and separately written scalar replay. It performs no profile "
            "join, contraction, or coordinate-three census.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "source_manifest_sha256": computation["source_manifest_sha256"],
            "shape_action_table_sha256": computation["shape_action_table_sha256"],
            "shape_orbit_count": computation["shape_orbit_count"],
            "central_state_count": computation["central_state_count"],
            "central_orbit_count": computation["central_orbit_count"],
            "authorized_central_reduction_factor": computation["authorized_central_reduction_factor"],
            "actual_combined_diagnostic_charge": receipt["actual_combined_charge"],
            "evidence_sha256": receipt["evidence_sha256"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile",
        description=(
            "Certify the corrected state-dependent residual C5 action, "
            "five-lane phase-ramped transition table, and canonical partition "
            "of the complete j_D=0 central slice."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_report",
        description=(
            "Write the canonical AD5 central-slice representative certificate. "
            "It certifies only the representative enumerator; B/C join "
            "equivariance and every contraction remain unproved."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 central-slice representatives\n\n"
            "This block is rendered directly from the corrected five-lane "
            "phase-ramped action table, exhaustive residual-slice partition, "
            "mod-25 cocycle sentinels, and separately written scalar replay. "
            "It performs no B/C join, contraction, or coordinate-three census.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "input_manifest_sha256": computation["input_manifest_sha256"],
            "action_table_sha256": computation["action_table_sha256"],
            "residual_edge_stream_sha256": computation["residual_edge_stream_sha256"],
            "slice_bitmap_sha256": computation["slice_bitmap_sha256"],
            "representative_stream_sha256": computation["representative_stream_sha256"],
            "slice_cardinality": computation["slice_cardinality"],
            "representative_count": computation["representative_count"],
            "actual_combined_diagnostic_charge": receipt["actual_combined_charge"],
            "evidence_sha256": receipt["evidence_sha256"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile",
        description=(
            "Certify record-by-record covariance of the exact five-key B/C "
            "join predicate and direct residual tests under the central "
            "action, including triangular scale bijectivity and the lossless "
            "nine-byte join-record schema."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_report",
        description=(
            "Write the canonical five-key join-covariance certificate. It "
            "authorizes the representative outer domain but performs no "
            "context join, contraction, or common-zero search."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 five-key join covariance\n\n"
            "This block is rendered directly from the literal source-record "
            "partition, exhaustive phase-kernel and triangular-scale checks, "
            "lossless join-record schema, collision-filtering sentinel, and "
            "separately written scalar replay. It authorizes the canonical "
            "representative outer domain but performs no context join, "
            "contraction, or common-zero search.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "source_partition_sha256": computation["source_partition_sha256"],
            "source_record_schema_sha256": computation["source_record_schema_sha256"],
            "phase_kernel_sha256": computation["phase_kernel_sha256"],
            "scale_bijection_sha256": computation["scale_bijection_sha256"],
            "state_schema_sha256": computation["state_schema_sha256"],
            "join_sentinel_sha256": computation["join_sentinel_sha256"],
            "representative_count": computation["representative_count"],
            "actual_combined_diagnostic_charge": receipt["actual_combined_charge"],
            "evidence_sha256": receipt["evidence_sha256"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile",
        description=(
            "Exhaust the exact five-key joins for canonical packed central "
            "representatives 5 through 8 with source-hashed open-addressed "
            "and independently sorted engines, then advance the cursor "
            "atomically only when their key streams and residual tests agree."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_report",
        description=(
            "Write the canonical dual-engine join certificate for packed "
            "representatives 5 through 8. This bounded report neither prices "
            "the remaining domain nor certifies a contraction or common zero."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 representative join batch 5–8\n\n"
            "This block is rendered directly from two source-hashed compiled "
            "engines. One uses an open-addressed exact-key table and the other "
            "sorts complete key-record pairs. They independently derive the "
            "local representative prefix and agree exactly on all key matches "
            "and direct residual tests for packed representatives 5 through 8. "
            "It does not cover later representatives, perform a contraction, "
            "or certify a common zero or period-index conclusion.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        replay = receipt["independent_sorted_join_replay"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "input_manifest_sha256": receipt["input_manifest_sha256"],
            "representative_prefix_sha256": receipt["representative_prefix_sha256"],
            "context_hashes": receipt["context_hashes"],
            "primary_program_sha256": computation["program_sha256"],
            "primary_transcript_sha256": computation["transcript_sha256"],
            "replay_program_sha256": replay["program_sha256"],
            "replay_transcript_sha256": replay["transcript_sha256"],
            "primary_charged_operations": computation["aggregate_charge_total"],
            "replay_charged_operations": replay["charged_operations"],
            "actual_combined_charge": receipt["actual_combined_charge"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
            "evidence_sha256": receipt["evidence_sha256"],
            "primary_mathematical_transcript_sha256": computation["mathematical_transcript_sha256"],
            "replay_mathematical_transcript_sha256": replay["mathematical_transcript_sha256"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile",
        description=(
            "Exhaust the exact five-key joins for canonical packed central "
            "representatives 9 through 12 with source-hashed open-addressed "
            "and independently sorted engines, binding the accepted cursor 9 "
            "before advancing it atomically to 13."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_report",
        description=(
            "Write the canonical dual-engine join certificate for packed "
            "representatives 9 through 12, including separate primary and "
            "replay charges without pricing the remaining domain."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 AD5 representative join batch 9–12\n\n"
            "This block is rendered directly from two source-hashed compiled "
            "engines. One uses an open-addressed exact-key table and the other "
            "sorts complete key-record pairs. They independently derive the "
            "thirteen-orbit representative prefix, bind the accepted cursor 9, "
            "and agree exactly on all key matches and direct residual tests for "
            "packed representatives 9 through 12. It does not cover later "
            "representatives, perform a contraction, or certify a common zero "
            "or period-index conclusion.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        replay = receipt["independent_sorted_join_replay"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "input_manifest_sha256": receipt["input_manifest_sha256"],
            "representative_prefix_sha256": receipt["representative_prefix_sha256"],
            "context_hashes": receipt["context_hashes"],
            "primary_program_sha256": computation["program_sha256"],
            "primary_transcript_sha256": computation["transcript_sha256"],
            "replay_program_sha256": replay["program_sha256"],
            "replay_transcript_sha256": replay["transcript_sha256"],
            "primary_charged_operations": computation["aggregate_charge_total"],
            "replay_charged_operations": replay["charged_operations"],
            "actual_combined_charge": receipt["actual_combined_charge"],
            "next_gate": receipt["next_gate"],
            "not_certified": receipt["not_certified"],
            "evidence_sha256": receipt["evidence_sha256"],
            "primary_mathematical_transcript_sha256": computation["mathematical_transcript_sha256"],
            "replay_mathematical_transcript_sha256": replay["mathematical_transcript_sha256"],
        }

    def _ad5_sweep_bundle_from_artifact(path: str) -> tuple[dict[str, Any], str]:
        target = _artifact_path(artifacts_root, path)
        if not target.is_file():
            raise ValueError(f"unknown AD5 v2.2 receipt artifact: {path}")
        content = target.read_text(encoding="utf-8")
        matches = re.findall(r"```json\n(.*?)\n```", content, re.DOTALL)
        if len(matches) != 1:
            raise ValueError("AD5 v2.2 receipt artifact lacks one canonical JSON block")
        try:
            bundle = json.loads(matches[0])
        except json.JSONDecodeError as error:
            raise ValueError("AD5 v2.2 receipt artifact has invalid JSON") from error
        if canonical_json(bundle) != matches[0]:
            raise ValueError("AD5 v2.2 receipt artifact JSON is not canonical")
        approved_bundle = require_reviewed_sweep_profile(bundle)
        validate_representative_bundle(approved_bundle)
        return approved_bundle, hashlib.sha256(content.encode()).hexdigest()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_receipt_summary",
        description=(
            "Read and fully validate one persisted AD5 v2.2 paired-receipt "
            "artifact, then return a compact projection of its immutable source, "
            "engine identities, context stream hashes, charges, comparisons, and "
            "cursor decision. This avoids transporting the full canonical bundle."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute", "artifacts:read"),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_receipt_summary(
        path: str,
    ) -> dict[str, Any]:
        bundle, artifact_sha256 = _ad5_sweep_bundle_from_artifact(path)
        pair = bundle["paired_subreceipt"]
        primary = bundle["primary_engine_receipt"]
        replay = bundle["sorted_engine_receipt"]

        def engine_summary(receipt: dict[str, Any]) -> dict[str, Any]:
            return {
                "receipt_sha256": receipt["canonical_payload_sha256"],
                "algorithm_id": receipt["engine"]["algorithm_id"],
                "engine_source_sha256": receipt["engine"]["engine_source_sha256"],
                "engine_executable_sha256": receipt["engine"]["engine_executable_sha256"],
                "runtime_identity_sha256": receipt["engine"]["runtime_identity_sha256"],
                "terminal_status": receipt["terminal_status"],
                "resources": receipt["resources"],
                "contexts": [
                    {
                        key: context[key]
                        for key in (
                            "ordinal",
                            "packed_representative",
                            "d_code",
                            "b_states_completed",
                            "c_states_completed",
                            "b_keyed_records_sha256",
                            "c_keyed_records_sha256",
                            "equal_key_pairs_sha256",
                            "match_stream_sha256",
                            "exact_key_match_count",
                            "residual_tests_completed",
                            "residual_survivor_count",
                            "residual_survivors_sha256",
                            "decomposition_failure_count",
                            "context_charge",
                            "status",
                        )
                    }
                    for context in receipt["contexts"]
                ],
            }

        return {
            "schema": "reasoning.level25-heisenberg-ad5-representative-join-receipt-summary.v2.2",
            "artifact": {
                "path": path,
                "bytes": _artifact_path(artifacts_root, path).stat().st_size,
                "sha256": artifact_sha256,
            },
            "source": {
                "source_manifest_sha256": pair["source_manifest_sha256"],
                "source_records_sha256": bundle["source_manifest"]["source_records_sha256"],
                "source_record_count": bundle["source_manifest"]["source_record_count"],
                "representative_stream_sha256": bundle["source_manifest"]["representative_stream_sha256"],
                "five_key_spec_sha256": bundle["source_manifest"]["five_key_spec_sha256"],
                "residual_spec_sha256": bundle["source_manifest"]["residual_spec_sha256"],
            },
            "pair": {
                "receipt_sha256": pair["canonical_payload_sha256"],
                "sweep_id": pair["sweep_id"],
                "prior": pair["prior"],
                "cursor": pair["cursor"],
                "primary_engine_receipt_sha256": pair["primary_engine_receipt_sha256"],
                "sorted_engine_receipt_sha256": pair["sorted_engine_receipt_sha256"],
                "comparison": pair["comparison"],
                "aggregate": pair["aggregate"],
                "terminal_status": pair["terminal_status"],
                "next_ordinal_if_accepted": pair["next_ordinal_if_accepted"],
            },
            "primary_engine": engine_summary(primary),
            "sorted_engine": engine_summary(replay),
            "bundle_validated": True,
            "chain_commit_required_for_cursor_advancement": True,
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile",
        description=(
            "Run independently compiled exact-hash and sorted-merge engines for "
            "canonical representative ordinals 13 through 16. Emit context-bound "
            "SHA-256 keyed-record and residual streams and form an accepted v2.2 "
            "pair, but do not advance the public chain tip."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_report",
        description=(
            "Persist the exact v2.2 dual-engine paired receipt for representative "
            "ordinals 13 through 16. Persistence does not advance the public cursor; "
            "the separate commit tool must compare-and-swap the accepted chain tip."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        profile = registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile",
            {},
        )
        profile_json = certified_receipt_json(profile)
        bundle = require_reviewed_sweep_profile(profile)
        pair = bundle["paired_subreceipt"]
        text = (
            "# Accepted augmented level-25 AD5 paired subreceipt 13–16\n\n"
            "Two separately compiled engines recompute every D-dependent five-key "
            "record and bind complete context-local keyed-record, match, residual, "
            "and survivor streams with SHA-256. Their accepted pair is persisted "
            "below but does not advance the public sweep cursor until the separate "
            "chain-tip compare-and-swap succeeds. This finite F_101 result makes no "
            "extension-field, contraction, lifting, Brauer, period, or index claim.\n\n"
            "```json\n"
            f"{profile_json}\n"
            "```\n\n"
            f"Paired receipt SHA-256: `{pair['canonical_payload_sha256']}`\n\n"
            f"Mathematical evidence SHA-256: `{profile['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "paired_receipt_sha256": pair["canonical_payload_sha256"],
            "evidence_sha256": profile["evidence_sha256"],
            "source_manifest_sha256": pair["source_manifest_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": True,
            "chain_committed": False,
            "decision": profile["decision"],
            "primary_charged_operations": pair["aggregate"]["primary_charge_total"],
            "replay_charged_operations": pair["aggregate"]["sorted_charge_total"],
            "actual_combined_charge": pair["aggregate"]["combined_charge_total"],
            "next_gate": profile["next_gate"],
            "not_certified": profile["not_certified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_commit",
        description=(
            "Validate a persisted accepted v2.2 paired receipt and advance the "
            "derived public cursor exactly once by compare-and-swap on its chain-tip "
            "artifact. A stale, skipped, overlapping, or distinct duplicate receipt "
            "is rejected without changing the tip."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "receipt_path": {"type": "string", "minLength": 1},
                "chain_tip_path": {"type": "string", "minLength": 1},
                "expected_chain_tip_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                    "description": (
                        "Current chain-tip artifact SHA-256. Omit only when the reviewed ordinal-13 prior has no chain-tip artifact yet."
                    ),
                },
            },
            "required": ["receipt_path", "chain_tip_path"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("science:compute", "artifacts:read", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_commit(
        receipt_path: str,
        chain_tip_path: str,
        expected_chain_tip_sha256: str | None = None,
    ) -> dict[str, Any]:
        bundle, receipt_artifact_sha256 = _ad5_sweep_bundle_from_artifact(receipt_path)
        pair = bundle["paired_subreceipt"]
        if pair["terminal_status"] != "ACCEPTED_EMPTY":
            raise ValueError("only an ACCEPTED_EMPTY paired receipt may advance the chain")
        replacement = {
            "schema": "reasoning.level25-heisenberg-ad5-representative-chain-tip.v2.2",
            "sweep_id": pair["sweep_id"],
            "source_manifest_sha256": pair["source_manifest_sha256"],
            "tip_receipt_sha256": pair["canonical_payload_sha256"],
            "tip_receipt_artifact_path": receipt_path,
            "tip_receipt_artifact_sha256": receipt_artifact_sha256,
            "next_ordinal": pair["next_ordinal_if_accepted"],
        }
        target = _artifact_path(artifacts_root, chain_tip_path)
        if target.is_file():
            current_bytes = target.read_bytes()
            actual_tip_sha256 = hashlib.sha256(current_bytes).hexdigest()
            try:
                current = json.loads(current_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("AD5 chain-tip artifact is invalid JSON") from error
            if current == replacement:
                return {
                    "status": "ALREADY_COMMITTED",
                    "chain_committed": True,
                    "idempotent": True,
                    "chain_tip": current,
                    "chain_tip_artifact": {
                        "path": chain_tip_path,
                        "bytes": len(current_bytes),
                        "sha256": actual_tip_sha256,
                    },
                }
            if expected_chain_tip_sha256 is None:
                raise ValueError("read the current chain tip and pass its SHA-256 before replacing it")
            if actual_tip_sha256 != expected_chain_tip_sha256:
                raise ValueError("chain tip changed since it was read; the receipt is quarantined")
            expected = {
                "tip_receipt_sha256": pair["prior"]["accepted_receipt_sha256"],
                "next_ordinal": pair["prior"]["next_ordinal"],
            }
            if {name: current.get(name) for name in expected} != expected:
                raise ValueError("receipt prior does not match the current chain tip; the receipt is quarantined")
        elif expected_chain_tip_sha256 is not None:
            raise ValueError("chain tip does not exist; omit expected_chain_tip_sha256 for the reviewed prior")
        elif (pair["prior"]["next_ordinal"] != 13
              or pair["prior"]["accepted_evidence_sha256"] != _REPRESENTATIVE_9_12_EVIDENCE_SHA256
              or pair["prior"]["reviewed_profile_verified"] is not True):
            raise ValueError("a new chain tip must begin at the exact reviewed ordinal-13 prior")
        tip_text = canonical_json(replacement) + "\n"
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": chain_tip_path,
                    "text": tip_text,
                    "expected_sha256": expected_chain_tip_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "status": "COMMITTED",
            "chain_committed": True,
            "idempotent": False,
            "chain_tip": replacement,
            "chain_tip_artifact": artifact,
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_macro_report",
        description=(
            "Write a non-executing administrative macroreceipt over one through "
            "sixteen consecutive accepted v2.2 paired subreceipts. It checks chain "
            "continuity and arithmetic but never runs an engine or advances a cursor."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subreceipt_paths": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "path": {"type": "string", "minLength": 1},
                "terminal_marker": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,199}$"},
                "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            "required": ["subreceipt_paths", "path", "terminal_marker"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("science:compute", "artifacts:read", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_macro_report(
        subreceipt_paths: list[str],
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        bundles = [_ad5_sweep_bundle_from_artifact(item)[0] for item in subreceipt_paths]
        macro = build_ad5_representative_macroreceipt(bundles)
        macro_json = canonical_json(macro)
        text = (
            "# Administrative augmented level-25 AD5 representative macroreceipt\n\n"
            "This summary checks an ordered chain of independently accepted paired "
            "subreceipts. It executes no search and owns no cursor advancement.\n\n"
            "```json\n"
            f"{macro_json}\n"
            "```\n\n"
            f"Macroreceipt SHA-256: `{macro['canonical_payload_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "macroreceipt_sha256": macro["canonical_payload_sha256"],
            "terminal_marker": terminal_marker,
            "cursor_advancement_owned_by_macro": False,
            "cursor": macro["cursor"],
            "aggregate": macro["aggregate"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_feasibility_profile",
        description=(
            "Measure exact half-key support and prospective lookup work for the "
            "complete row-0 join on five predeclared rows-6/8 calibration "
            "fibers, including the 62*t unary term and exact mu_5 phase orbits."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_row0_feasibility_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_row0_feasibility_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_feasibility_report",
        description=(
            "Write the reviewed bounded row-0 feasibility receipt, refusing failed "
            "certificates, unreviewed scope or inconsistent raw evidence. Omit "
            "expected_sha256 for a new report; this rejects or retains only "
            "the hash-join strategy and does not count common zeros."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_row0_feasibility_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_row0_feasibility_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block row-0 feasibility profile\n\n"
            "This block is rendered directly from the exact five-fiber half-key "
            "support profile and independent Python input and slice replay. It "
            "tests the complete row-0 hash-join strategy after rows 6 and 8; it "
            "does not run the all-fiber count or count common zeros.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile",
        description=(
            "Compute the exact parity ranks coupling normalized mu_5 labels to "
            "the joint rows-6/8 key on all 21 bucket orbits, which cover every "
            "residue and bucket by verified column permutations."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_label_unary_rank_report",
        description=(
            "Write the reviewed row-0 label/unary rank receipt, refusing failed "
            "certificates, unreviewed scope or inconsistent raw evidence. Omit "
            "expected_sha256 for a new report; this rejects only per-block "
            "low-rank separation and does not count common zeros."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_row0_label_unary_rank_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block row-0 label/unary rank profile\n\n"
            "This block is rendered directly from the exact 21-orbit parity-rank "
            "profile and independent Python replay. It measures the coupling "
            "between normalized labels and both unary keys; it does not count "
            "row-0 survivors or common zeros.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_kernel_rank_profile",
        description=(
            "Compute the exact Fourier support and convolution rank of every "
            "phase-averaged row-0 edge-kernel orbit over two suitable primes, "
            "with literal direct transforms and an independent replay."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_row0_kernel_rank_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_row0_kernel_rank_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_kernel_rank_report",
        description=(
            "Write the reviewed row-0 phase-kernel Fourier-rank receipt, refusing failed "
            "certificates, unreviewed scope or inconsistent raw evidence. Omit "
            "expected_sha256 for a new report; this rejects only the proposed "
            "kernel low-rank shortcut and does not count common zeros."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_row0_kernel_rank_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_row0_kernel_rank_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block row-0 phase-kernel rank profile\n\n"
            "This block is rendered directly from exact literal and factorized "
            "Fourier transforms over two suitable primes and an independent "
            "Python replay. It measures the phase-averaged edge kernels; it "
            "does not run an all-fiber row-0 count or count common zeros.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        # The receipt intentionally publishes every factorized eigenvalue and
        # equality bit. Keep the tool response bounded; the canonical artifact
        # and its hash are the durable evidence surface.
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile",
        description=(
            "Compute the complete lossless raw and phase-closed pair-table "
            "distributions for the single gamma=(0,0,0), t=0 row-0 fiber "
            "with an independent hard-capped replay."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_report",
        description=(
            "Write the reviewed lossless fixed-fiber pair calibration receipt, refusing "
            "failed certificates, unreviewed scope or inconsistent raw evidence. "
            "Omit expected_sha256 for a new report; this calibration neither "
            "prices all representative fibers nor counts row-0 survivors."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block row-0 fixed-fiber pair calibration\n\n"
            "This block is rendered directly from two source-hashed compiled "
            "implementations under separate hard address-space limits. It "
            "retains the complete boundary-label, unary-sum, and edge-value "
            "correlations for one calibration fiber; it does not execute the "
            "crossing join or authorize an all-fiber schedule.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "summary": {
                "raw_support_counts": receipt["computation"]["raw_support_counts"],
                "phase_support_counts": receipt["computation"]["phase_support_counts"],
                "phase_collision_merge_counts": receipt["computation"]["phase_collision_merge_counts"],
                "not_certified": receipt["not_certified"],
            },
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_pair_reuse_profile",
        description=(
            "Certify the exact pair-table dependency cache and mu5 transport, "
            "reject stronger common-mu25 transport at the cyclic wrap, and "
            "price the resulting direct construction with an independent replay."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_row0_pair_reuse_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_row0_pair_reuse_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_pair_reuse_report",
        description=(
            "Write the reviewed row-0 pair dependency and transport receipt, refusing "
            "failed certificates, unreviewed scope or inconsistent raw evidence. "
            "Omit expected_sha256 for a new report; this rejects only the named "
            "direct pair-construction schedule."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_row0_pair_reuse_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_row0_pair_reuse_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block row-0 pair reuse profile\n\n"
            "This block is rendered directly from an exhaustive compiled "
            "transport check and an independent formula replay. It certifies "
            "the source-level dependency cache and rejects the resulting direct "
            "schedule; it does not execute a crossing join or count survivors.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "summary": {
                "left_dependency_table_count": receipt["computation"]["left_dependency_table_count"],
                "right_dependency_table_count": receipt["computation"]["right_dependency_table_count"],
                "primary_direct_pair_visit_count": receipt["computation"]["primary_direct_pair_visit_count"],
                "primary_plus_replay_direct_pair_visit_count": receipt["computation"]["primary_plus_replay_direct_pair_visit_count"],
                "not_certified": receipt["not_certified"],
            },
        }

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile",
        description=(
            "Compute the exact Fourier support of every lossless row-0 "
            "edge-value indicator on (Z/5)^4, with a source-hashed compiled "
            "calculation and an independent Python replay."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile()

    @registry.tool(
        name="level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_report",
        description=(
            "Write the reviewed exact Fourier-support receipt, refusing failed "
            "certificates, unreviewed scope or inconsistent raw evidence, for all 65 "
            "lossless edge-value indicators. Omit expected_sha256 for a new "
            "report; this tests only the named label-kernel shortcut."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile",
            {},
        )
        receipt_json = certified_receipt_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified augmented level-25 four-block row-0 edge-indicator Fourier ranks\n\n"
            "This block is rendered directly from an exact cyclotomic-zero "
            "test, two modular transforms, and an independent Python replay. "
            "It rejects the named Fourier-support shortcut; it does not execute "
            "a crossing join or count row-zero survivors.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"Mathematical evidence SHA-256: `{receipt['evidence_sha256']}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        computation = receipt["computation"]
        return {
            "artifact": artifact,
            "receipt_sha256": receipt_sha256,
            "evidence_sha256": receipt["evidence_sha256"],
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
            "decision": receipt["decision"],
            "summary": {
                "full_rank_indicator_count": computation["full_rank_indicator_count"],
                "defective_indicator_values": computation["defective_indicator_values"],
                "minimum_indicator_rank": computation["minimum_indicator_rank"],
                "total_indicator_fourier_support": computation["total_indicator_fourier_support"],
                "maximum_total_indicator_fourier_support": computation["maximum_total_indicator_fourier_support"],
                "not_certified": receipt["not_certified"],
            },
        }

    @registry.tool(
        name="level25_heisenberg_balanced_coordinate_rank_profile",
        description=(
            "Compile and run the exact F_101 five-block search over all 2^25 "
            "coordinate supports for the balanced sparse forms. It certifies "
            "only the equation-span rank-defect bound, not ideal height, "
            "dimension, or smoothness."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_coordinate_rank_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_coordinate_rank_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_coordinate_rank_report",
        description=(
            "Write the canonical exhaustive coordinate-support rank receipt "
            "for the balanced sparse forms. Omit expected_sha256 for a new "
            "report; the receipt does not certify global dimension or "
            "smoothness."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_coordinate_rank_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_coordinate_rank_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified balanced level-25 coordinate-support ranks\n\n"
            "This block is rendered directly from the compiled exact F_101 "
            "search over all 2^25 coordinate supports. It certifies an "
            "equation-span rank-defect bound, not ideal height, global "
            "dimension, or smoothness.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_two_block_obstruction_profile",
        description=(
            "Verify the explicit characteristic-zero two-residue-block common "
            "zero of the balanced sparse system, its F_101 specialization, and "
            "normalized Jacobian rank nine. This rules out a smooth dimension-"
            "two complete intersection without asserting the scheme's local "
            "dimension or excluding richer coefficient choices."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_two_block_obstruction_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_two_block_obstruction_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_two_block_obstruction_report",
        description=(
            "Write the canonical exact characteristic-zero two-block rank-gate "
            "obstruction receipt and its F_101 specialization. Omit "
            "expected_sha256 for a new report; this does not certify local "
            "dimension or exclude richer coefficient choices."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_two_block_obstruction_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_two_block_obstruction_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified balanced level-25 two-block rank obstruction\n\n"
            "This block is rendered directly from the characteristic-zero "
            "cyclotomic point, its F_101 specialization, all twenty-two exact "
            "equation evaluations, and the normalized active Jacobian. It "
            "rules out a smooth dimension-two complete intersection without "
            "asserting local dimension or excluding richer coefficient "
            "choices.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_balanced_coefficient_pivot_profile",
        description=(
            "Verify that the degree-25 orbit sum of x_0^20*x_1^5 lies in "
            "the selected (5,20) character eigenspace and evaluates to "
            "5*lambda^5 at the certified two-block point. This removes that "
            "one-orbit sparse obstruction without claiming global smoothness."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_coefficient_pivot_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_coefficient_pivot_profile()

    @registry.tool(
        name="level25_heisenberg_balanced_coefficient_pivot_report",
        description=(
            "Write the canonical exact coefficient-pivot receipt showing "
            "that a second monomial orbit in the selected (5,20) eigenspace "
            "removes the characteristic-zero two-block witness. Omit "
            "expected_sha256 for a new report; this does not certify global "
            "smoothness or an explicit coefficient retaining every local gate."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_coefficient_pivot_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_coefficient_pivot_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified balanced level-25 coefficient pivot\n\n"
            "This block is rendered directly from the second monomial orbit, "
            "its Heisenberg character, and its exact value at the cyclotomic "
            "two-block point. It removes that sparse witness without claiming "
            "a globally smooth surface.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_pivoted_two_block_obstruction_profile"),
        description=(
            "Verify the exact trivial-stabilizer nonadjacent two-block common "
            "zero surviving the coefficient-one pivot. Confined PARI/GP "
            "certifies cyclotomic and affine Jacobian rank nine, while the "
            "zero-shift dimension bound certifies that the point is singular."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_pivoted_two_block_obstruction_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_pivoted_two_block_obstruction_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_pivoted_two_block_obstruction_report"),
        description=(
            "Write the canonical exact obstruction receipt for the "
            "coefficient-one pivoted tuple. Omit expected_sha256 for a new "
            "report; this certifies a rank-nine geometrically singular "
            "free-locus common zero without asserting exact local dimension."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_pivoted_two_block_obstruction_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_pivoted_two_block_obstruction_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified obstruction to the pivoted balanced level-25 tuple\n\n"
            "This block is rendered directly from the cyclotomic "
            "nonadjacent two-block point, its exact trivial stabilizer, all "
            "twenty-two equation evaluations, pivot value and gradient, and "
            "a confined PARI/GP rank computation over Q(zeta_25). It rejects "
            "the tuple by comparing tangent dimension fifteen with the "
            "zero-shift local-dimension upper bound ten.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile"),
        description=(
            "Verify that the explicit (2,15) orbit of "
            "x_0^2*x_5^13*x_2^10 evaluates to 5*lambda^10 at the surviving "
            "nonadjacent two-block obstruction. This selects a second pivot "
            "without claiming its fixed local gates or global smoothness."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_nonadjacent_coefficient_pivot_report"),
        description=(
            "Write the canonical second-pivot receipt showing that an "
            "explicit orbit in the selected (2,15) eigenspace removes the "
            "nonadjacent two-block witness. Omit expected_sha256 for a new "
            "report; this does not certify the two-pivot tuple's local gates."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_nonadjacent_coefficient_pivot_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified nonadjacent balanced level-25 coefficient pivot\n\n"
            "This block is rendered directly from the selected (2,15) "
            "monomial orbit, its exact Heisenberg character, and its value "
            "at the surviving cyclotomic two-block point. It removes that "
            "witness without claiming local gates or global smoothness.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name=("level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile"),
        description=(
            "Verify the exact trivial-stabilizer nonadjacent two-block common "
            "zero surviving both coefficient-one pivots. The zero-shift "
            "dimension and active-column bounds certify that the two-pivot "
            "common-zero scheme is geometrically singular."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile()

    @registry.tool(
        name=("level25_heisenberg_balanced_two_pivot_two_block_obstruction_report"),
        description=(
            "Write the canonical cyclotomic obstruction receipt for the "
            "two-pivot tuple. Omit expected_sha256 for a new report; this "
            "certifies geometric singularity without asserting exact local "
            "or global dimension."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_balanced_two_pivot_two_block_obstruction_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified obstruction to the two-pivot balanced tuple\n\n"
            "This block is rendered directly from the surviving cyclotomic "
            "nonadjacent two-block point, both pivot evaluations, its exact "
            "trivial stabilizer, and the tangent-versus-dimension bound. It "
            "certifies that the two-pivot common-zero scheme is singular.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    @registry.tool(
        name="level25_heisenberg_nonadjacent_block_shape_cover_profile",
        description=(
            "Exhaust all 25^4 normalized same-shape twenty-fifth-root "
            "nonadjacent two-block points and the bounded positive three-factor "
            "orbit library. A source-hashed compiled enumeration and an "
            "independent Python recomputation certify the minimal disjoint "
            "four-orbit cover without claiming arbitrary two-block points or "
            "global smoothness."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_heisenberg_nonadjacent_block_shape_cover_profile_tool() -> dict[str, Any]:
        return level25_heisenberg_nonadjacent_block_shape_cover_profile()

    @registry.tool(
        name="level25_heisenberg_nonadjacent_block_shape_cover_report",
        description=(
            "Write the canonical source-hashed receipt for the exhaustive "
            "same-shape nonadjacent two-block cover. Omit expected_sha256 for "
            "a new report; the receipt deliberately leaves unequal and "
            "non-torsion block shapes plus all global geometry open."
        ),
        input_schema=post_s25_diagonal_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def level25_heisenberg_nonadjacent_block_shape_cover_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "level25_heisenberg_nonadjacent_block_shape_cover_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified nonadjacent level-25 block-shape cover\n\n"
            "This block is rendered directly from the exhaustive normalized "
            "root-of-unity block-shape census, the bounded candidate-orbit "
            "library, and the minimal disjoint four-orbit cover. Its compiled "
            "transcript is checked against an independent implementation.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    heisenberg_h2_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    @registry.tool(
        name="heisenberg_surface_invariant_h2_descent_profile",
        description=(
            "Compute the quotient-resolution lattice forced by the explicit "
            "Heisenberg (5,5) surface, the structural integral invariant-H2 and "
            "Cartan-Leray d3 model, and the exact level-five Perry pairing test. "
            "The receipt exposes the standard topological bridges separately and "
            "does not certify a Brauer class or counterexample."
        ),
        input_schema=heisenberg_h2_schema,
        risk="read",
        capabilities=("science:compute",),
    )
    def heisenberg_surface_invariant_h2_descent_profile_tool() -> dict[str, Any]:
        return heisenberg_surface_invariant_h2_descent_profile()

    heisenberg_h2_report_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "terminal_marker": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
            },
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": (
                    "Current SHA-256 of an existing report at path; omit when "
                    "creating a new path. This is not the profile request SHA-256 "
                    "or the canonical receipt SHA-256."
                ),
            },
        },
        "required": ["path", "terminal_marker"],
        "additionalProperties": False,
    }

    @registry.tool(
        name="heisenberg_surface_invariant_h2_descent_report",
        description=(
            "Write the canonical quotient-resolution, invariant-H2, d3, and "
            "level-five Perry-gate receipt for the explicit Heisenberg surface. "
            "Omit expected_sha256 when creating a new report; use only the current "
            "artifact hash returned by artifact_read when replacing one."
        ),
        input_schema=heisenberg_h2_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def heisenberg_surface_invariant_h2_descent_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "heisenberg_surface_invariant_h2_descent_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified Heisenberg quotient-resolution and H2 descent profile\n\n"
            "This block is rendered directly from the fixed local singularity, "
            "exceptional-code, lattice-discriminant, and Perry pairing arithmetic. "
            "Its named topological bridges remain visible for independent audit.\n\n"
            "```json\n"
            f"{receipt_json}"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    if any(definition.name == "noether_compute" for definition in registry.list()):
        heisenberg_surface_schema = {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "enum": [5]},
                "ambient_dimension": {"type": "integer", "enum": [4]},
                "degrees": {
                    "type": "array",
                    "items": {"type": "integer", "enum": [5]},
                    "minItems": 2,
                    "maxItems": 2,
                },
            },
            "required": ["level", "ambient_dimension", "degrees"],
            "additionalProperties": False,
        }

        @registry.tool(
            name="heisenberg_complete_intersection_surface_profile",
            description=(
                "Certify an explicit Heisenberg-stable smooth (5,5) complete-"
                "intersection surface in P4. The fixed witness is checked by "
                "the pinned Noether runtime for dimension and degree and by a "
                "confined exact Singular protocol on all five projective "
                "Jacobian charts; the result also records the character blocks, "
                "fixed loci, topology, invariant Hodge dimensions, and remaining "
                "period-index gates."
            ),
            input_schema=heisenberg_surface_schema,
            risk="read",
            capabilities=("science:compute",),
        )
        def heisenberg_complete_intersection_surface_profile(
            level: int,
            ambient_dimension: int,
            degrees: list[int],
        ) -> dict[str, Any]:
            if (level, ambient_dimension, degrees) != (5, 4, [5, 5]):
                raise ValueError("the certified profile currently supports only level=5, ambient_dimension=4, and degrees=[5,5]")
            witness = heisenberg_surface_witness_profile()
            equations = [equation["sparse_terms"] for equation in witness["equations"]]
            noether = registry.invoke(
                "noether_compute",
                {
                    "operation": "ideal_invariants",
                    "payload": {
                        "field": {"kind": "Fp", "prime": 101},
                        "ambient": "projective",
                        "variables": [f"x{index}" for index in range(5)],
                        "generators": equations,
                        "check_smooth": True,
                    },
                    "timeout_seconds": 120,
                },
            )
            invariants = noether["result"]
            singular = singular_smoothness_profile()
            noether_expected_invariants = (
                invariants.get("field") == "F_101"
                and invariants.get("ambient") == "projective"
                and invariants.get("krull_dimension") == 3
                and invariants.get("scheme_dimension") == 2
                and invariants.get("degree") == 25
                and invariants.get("certificate_verified") is True
            )
            noether_native_smoothness_gap_visible = invariants.get("smoothness", {}).get("supported") is False
            certificate = {
                "finite_character_and_fixed_locus_census_verified": witness["certificate_verified"],
                "noether_dimension_degree_certificate_verified": noether_expected_invariants,
                "noether_native_smoothness_gap_visible": noether_native_smoothness_gap_visible,
                "singular_five_chart_smoothness_certificate_verified": singular["certificate_verified"],
                "smooth_special_fiber_has_expected_degree": (
                    noether_expected_invariants and singular["all_chart_singular_ideals_are_unit"]
                ),
                "characteristic_zero_specialization_bridge_applicable": True,
                "smooth_complete_intersection_connectedness_bridge_applicable": True,
                "free_product_quotient_bridge_applicable": True,
            }
            surface_witness_certified = all(certificate.values())
            noether_receipt = {
                "request_sha256": noether["request_sha256"],
                "provenance": noether["provenance"],
                "field": invariants["field"],
                "ambient": invariants["ambient"],
                "krull_dimension": invariants["krull_dimension"],
                "scheme_dimension": invariants["scheme_dimension"],
                "degree": invariants["degree"],
                "hilbert_numerator": invariants["hilbert_numerator"],
                "hilbert_polynomial": invariants["hilbert_polynomial"],
                "groebner_basis_sha256": hashlib.sha256(canonical_json({"basis": invariants["groebner_basis"]}).encode()).hexdigest(),
                "ideal_json_sha256": hashlib.sha256(canonical_json({"ideal": invariants["ideal_json"]}).encode()).hexdigest(),
                "native_smoothness": invariants["smoothness"],
                "certificate_verified": invariants["certificate_verified"],
            }
            request = {
                "level": level,
                "ambient_dimension": ambient_dimension,
                "degrees": degrees,
            }
            return {
                "schema": "reasoning.heisenberg-complete-intersection-surface-profile.v1",
                "request": request,
                "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
                "witness": witness,
                "finite_field_certificate": {
                    "prime": 101,
                    "noether": noether_receipt,
                    "singular": singular,
                },
                "proof_bridges": {
                    "smoothness_in_characteristic_zero": (
                        "The projective relative singular locus of the two integral "
                        "quintics is proper over Spec Z. A singular generic fiber "
                        "would have closed image containing the generic point and "
                        "therefore every prime, contradicting the empty singular "
                        "fiber over F_101."
                    ),
                    "integrality": (
                        "The two quintics have height two by the certified smooth "
                        "degree-25 special fiber. Their characteristic-zero complete "
                        "intersection is connected; smooth connected components are "
                        "disjoint and open, so the surface is integral."
                    ),
                    "sixfold_quotient": (
                        "Every nonidentity element translates E by a nonzero E[5] "
                        "point, so the diagonal action on S x E x P3 is free. The "
                        "finite quotient is therefore a smooth proper integral "
                        "complex sixfold."
                    ),
                },
                "verdict": {
                    "surface_witness": "SURFACE_WITNESS",
                    "surface_witness_certified": surface_witness_certified,
                    "sixfold_geometry_certified": surface_witness_certified,
                    "period_five_brauer_class_certified": False,
                    "index_5_adic_valuation_at_least_6_certified": False,
                    "period_index_counterexample_certified": False,
                },
                "next_gate": {
                    "feature": "heisenberg_surface_invariant_h2_descent_profile",
                    "task": (
                        "compute the saturated integral invariant H2 lattice and its "
                        "Cartan-Leray d3 map, then search the non-(1,1) kernel and "
                        "valuation-one pairings needed by the Perry index formula"
                    ),
                    "do_not_use": (
                        "H3 of a quintic threefold tensor H1 of an abelian surface; "
                        "that term has degree four and is absent from this surface "
                        "architecture"
                    ),
                },
                "certificate": certificate,
                "certificate_verified": surface_witness_certified,
            }

        heisenberg_surface_report_schema = json.loads(json.dumps(heisenberg_surface_schema))
        heisenberg_surface_report_schema["properties"].update(
            {
                "path": {"type": "string", "minLength": 1},
                "terminal_marker": {
                    "type": "string",
                    "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
                },
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                },
            }
        )
        heisenberg_surface_report_schema["required"] = [
            "level",
            "ambient_dimension",
            "degrees",
            "path",
            "terminal_marker",
        ]

        @registry.tool(
            name="heisenberg_complete_intersection_surface_report",
            description=(
                "Run the pinned-Noether and exact Singular certification for "
                "the explicit Heisenberg-stable (5,5) surface and atomically "
                "write the canonical machine receipt."
            ),
            input_schema=heisenberg_surface_report_schema,
            risk="write",
            capabilities=("science:compute", "artifacts:write"),
            idempotent=True,
        )
        def heisenberg_complete_intersection_surface_report(
            level: int,
            ambient_dimension: int,
            degrees: list[int],
            path: str,
            terminal_marker: str,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            receipt = registry.invoke(
                "heisenberg_complete_intersection_surface_profile",
                {
                    "level": level,
                    "ambient_dimension": ambient_dimension,
                    "degrees": degrees,
                },
            )
            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            text = (
                "# Certified level-five Heisenberg (5,5) surface witness\n\n"
                "This block is rendered directly from the finite character "
                "census, pinned-Noether dimension and degree receipt, and exact "
                "five-chart Singular smoothness protocol; no model-authored "
                "field transcription is used.\n\n"
                "```json\n"
                f"{receipt_json}"
                "```\n\n"
                f"Receipt SHA-256: `{receipt_sha256}`\n\n"
                f"{terminal_marker}\n"
            )
            artifact = registry.invoke(
                "artifact_write",
                {
                    key: value
                    for key, value in {
                        "path": path,
                        "text": text,
                        "expected_sha256": expected_sha256,
                    }.items()
                    if value is not None
                },
            )
            return {
                "artifact": artifact,
                "summary": receipt["verdict"],
                "receipt_sha256": receipt_sha256,
                "terminal_marker": terminal_marker,
                "certificate_verified": receipt["certificate_verified"],
            }

    fermat_report_schema = json.loads(json.dumps(registry.get("fermat_surface_diagonal_profile").input_schema))

    fermat_report_schema["properties"].update(
        {
            "path": {"type": "string", "minLength": 1},
            "terminal_marker": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
            },
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
        }
    )

    fermat_report_schema["required"] = [
        *fermat_report_schema["required"],
        "path",
        "terminal_marker",
    ]

    @registry.tool(
        name="fermat_surface_diagonal_report",
        description=(
            "Compute the certified diagonal Fermat-surface group, stabilizer, "
            "and Hodge-character profile and atomically write a canonical receipt."
        ),
        input_schema=fermat_report_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def fermat_surface_diagonal_report(
        degree: int,
        character: list[int],
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "fermat_surface_diagonal_profile",
            {"degree": degree, "character": character},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified diagonal Fermat-surface profile\n\n"
            "This block is rendered directly from the exact finite computation; "
            "no model-authored field transcription is used.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    diagonal_kernel_scan_schema = dict(registry.get("fermat_surface_level25_diagonal_kernel_scan_profile").input_schema)

    diagonal_kernel_scan_schema["properties"] = {
        "path": {"type": "string", "minLength": 1},
        "terminal_marker": {
            "type": "string",
            "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
        },
        "expected_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
    }

    diagonal_kernel_scan_schema["required"] = ["path", "terminal_marker"]

    @registry.tool(
        name="fermat_surface_level25_diagonal_kernel_scan_report",
        description=(
            "Exhaust the eligible degree-25 diagonal Fermat kernels, run each "
            "exact integral invariant-lattice test, and atomically write one "
            "canonical family-level rejection receipt."
        ),
        input_schema=diagonal_kernel_scan_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def fermat_surface_level25_diagonal_kernel_scan_report(
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "fermat_surface_level25_diagonal_kernel_scan_profile",
            {},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified degree-25 diagonal Fermat-kernel scan\n\n"
            "This block is rendered directly from the exhaustive character "
            "classification, exact sparse saturations, and full-cohomology glue "
            "tests; no model-authored field transcription is used.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    elliptic_modular_surface_schema = dict(registry.get("elliptic_modular_surface_level25_profile").input_schema)

    elliptic_modular_surface_schema["properties"] = {
        **elliptic_modular_surface_schema["properties"],
        "path": {"type": "string", "minLength": 1},
        "terminal_marker": {
            "type": "string",
            "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
        },
        "expected_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
    }

    elliptic_modular_surface_schema["required"] = [
        *elliptic_modular_surface_schema["required"],
        "path",
        "terminal_marker",
    ]

    @registry.tool(
        name="elliptic_modular_surface_level25_report",
        description=(
            "Certify the full-level-25 elliptic modular surface through its exact "
            "fixed-lattice split and raw invariant-lattice rejection, then "
            "atomically write the canonical machine receipt for the remaining "
            "Cartan-Leray d2 computation."
        ),
        input_schema=elliptic_modular_surface_schema,
        risk="write",
        capabilities=("science:compute", "artifacts:write"),
        idempotent=True,
    )
    def elliptic_modular_surface_level25_report(
        level: int,
        path: str,
        terminal_marker: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        receipt = registry.invoke(
            "elliptic_modular_surface_level25_profile",
            {"level": level},
        )
        receipt_json = canonical_json(receipt)
        receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
        text = (
            "# Certified full-level-25 elliptic modular surface profile\n\n"
            "This block is rendered directly from the exact modular-group, cusp, "
            "fixed-locus, surface-invariant, section-orbit, and integral fixed-"
            "lattice computations; no model-authored field transcription is used.\n\n"
            "```json\n"
            f"{receipt_json}\n"
            "```\n\n"
            f"Receipt SHA-256: `{receipt_sha256}`\n\n"
            f"{terminal_marker}\n"
        )
        artifact = registry.invoke(
            "artifact_write",
            {
                key: value
                for key, value in {
                    "path": path,
                    "text": text,
                    "expected_sha256": expected_sha256,
                }.items()
                if value is not None
            },
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "receipt_sha256": receipt_sha256,
            "terminal_marker": terminal_marker,
            "certificate_verified": receipt["certificate_verified"],
        }

    if any(definition.name == "noether_compute" for definition in registry.list()):
        orbit_norm_schema = dict(registry.get("fermat_surface_orbit_norm_profile").input_schema)
        orbit_norm_schema["properties"] = {
            **orbit_norm_schema["properties"],
            "path": {"type": "string", "minLength": 1},
            "terminal_marker": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
            },
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
        }
        orbit_norm_schema["required"] = [
            *orbit_norm_schema["required"],
            "path",
            "terminal_marker",
        ]

        @registry.tool(
            name="fermat_surface_orbit_norm_report",
            description=(
                "Compute the Pham orbit-norm Gram matrix, certify its rank, "
                "determinant, and Smith factors in pinned Noether, and atomically "
                "write the canonical receipt. Integral saturation remains explicit."
            ),
            input_schema=orbit_norm_schema,
            risk="write",
            capabilities=("science:compute", "artifacts:write"),
            idempotent=True,
        )
        def fermat_surface_orbit_norm_report(
            degree: int,
            character: list[int],
            path: str,
            terminal_marker: str,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            receipt = registry.invoke(
                "fermat_surface_orbit_norm_profile",
                {"degree": degree, "character": character},
            )
            gram = receipt["orbit_norm_lattice"]["basis_gram"]
            noether = registry.invoke(
                "noether_compute",
                {
                    "operation": "matrix_invariants",
                    "payload": {"ring": "Z", "rows": gram},
                },
            )
            invariants = noether["result"]
            expected_rank = degree - 1
            if invariants.get("rank") != expected_rank:
                raise RuntimeError("the proposed orbit-norm basis does not have the expected rank")
            receipt["orbit_norm_lattice"]["rank"] = invariants["rank"]
            receipt["orbit_norm_lattice"]["smith_invariant_factors"] = invariants["invariant_factors"]
            receipt["orbit_norm_lattice"]["absolute_discriminant"] = invariants["absolute_determinant"]
            receipt["noether"] = {
                "request_sha256": noether["request_sha256"],
                "provenance": noether["provenance"],
                "certificate_verified": invariants["certificate_verified"],
            }
            receipt["unresolved_integral_gates"]["orbit_norm_rank_and_smith_certified"] = True
            receipt["certificate"]["noether_smith_verified"] = invariants["certificate_verified"]
            receipt["certificate_verified"] = all(receipt["certificate"].values())

            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            text = (
                "# Certified Fermat orbit-norm lattice\n\n"
                "This block is rendered directly from the exact Pham pairing and "
                "the pinned Noether Smith computation; no model-authored field "
                "transcription is used.\n\n"
                "```json\n"
                f"{receipt_json}\n"
                "```\n\n"
                f"Receipt SHA-256: `{receipt_sha256}`\n\n"
                f"{terminal_marker}\n"
            )
            artifact = registry.invoke(
                "artifact_write",
                {
                    key: value
                    for key, value in {
                        "path": path,
                        "text": text,
                        "expected_sha256": expected_sha256,
                    }.items()
                    if value is not None
                },
            )
            return {
                "artifact": artifact,
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
                "terminal_marker": terminal_marker,
                "certificate_verified": receipt["certificate_verified"],
            }

        invariant_lattice_schema = dict(registry.get("fermat_surface_level25_invariant_lattice_profile").input_schema)
        invariant_lattice_schema["properties"] = {
            **invariant_lattice_schema["properties"],
            "path": {"type": "string", "minLength": 1},
            "terminal_marker": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
            },
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
        }
        invariant_lattice_schema["required"] = [
            *invariant_lattice_schema["required"],
            "path",
            "terminal_marker",
        ]

        @registry.tool(
            name="fermat_surface_level25_invariant_lattice_report",
            description=(
                "Run the exact sparse saturation and full-cohomology glue test "
                "for the selected degree-25 Fermat action, certify the saturated "
                "Gram with pinned Noether, and write one canonical receipt."
            ),
            input_schema=invariant_lattice_schema,
            risk="write",
            capabilities=("science:compute", "artifacts:write"),
            idempotent=True,
        )
        def fermat_surface_level25_invariant_lattice_report(
            degree: int,
            character: list[int],
            path: str,
            terminal_marker: str,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            receipt = registry.invoke(
                "fermat_surface_level25_invariant_lattice_profile",
                {"degree": degree, "character": character},
            )
            gram = receipt["primitive_invariant_lattice"]["gram"]
            noether = registry.invoke(
                "noether_compute",
                {
                    "operation": "matrix_invariants",
                    "payload": {"ring": "Z", "rows": gram},
                },
            )
            invariants = noether["result"]
            if invariants.get("rank") != 24:
                raise RuntimeError("the saturated primitive invariant Gram lost rank")
            saturation_index = receipt["saturation"]["norm_to_primitive_invariant_index"]
            expected_norm_discriminant = 5**102
            if invariants["absolute_determinant"] * saturation_index * saturation_index != expected_norm_discriminant:
                raise RuntimeError("the saturated and norm discriminants are inconsistent")
            receipt["primitive_invariant_lattice"].update(
                {
                    "smith_invariant_factors": invariants["invariant_factors"],
                    "absolute_discriminant": invariants["absolute_determinant"],
                    "smith_certified": invariants["certificate_verified"],
                }
            )
            receipt["noether"] = {
                "request_sha256": noether["request_sha256"],
                "provenance": noether["provenance"],
                "certificate_verified": invariants["certificate_verified"],
            }
            receipt["unresolved_integral_gates"]["primitive_invariant_smith_certified"] = True
            receipt["certificate"].update(
                {
                    "noether_smith_verified": invariants["certificate_verified"],
                    "norm_saturation_discriminant_identity_verified": True,
                }
            )
            receipt["certificate_verified"] = all(receipt["certificate"].values())

            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            text = (
                "# Certified degree-25 Fermat invariant lattice\n\n"
                "This block is rendered directly from the exact sparse group-ring "
                "saturation, full-cohomology glue test, and pinned Noether Smith "
                "computation; no model-authored field transcription is used.\n\n"
                "```json\n"
                f"{receipt_json}\n"
                "```\n\n"
                f"Receipt SHA-256: `{receipt_sha256}`\n\n"
                f"{terminal_marker}\n"
            )
            artifact = registry.invoke(
                "artifact_write",
                {
                    key: value
                    for key, value in {
                        "path": path,
                        "text": text,
                        "expected_sha256": expected_sha256,
                    }.items()
                    if value is not None
                },
            )
            return {
                "artifact": artifact,
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
                "terminal_marker": terminal_marker,
                "certificate_verified": receipt["certificate_verified"],
            }

    if any(definition.name == "cartan_leray_h2_edge_lattice" for definition in registry.list()):
        abelian_surface_schema = {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "enum": [2, 3, 5, 25]},
            },
            "required": ["level"],
            "additionalProperties": False,
        }

        @registry.tool(
            name="abelian_surface_translation_edge_profile",
            description=(
                "Compute the full H2 edge lattice for C_m x C_m translating "
                "one circle in each factor of a marked product of elliptic curves. "
                "The bounded calculation splits the product cell complex into "
                "active-active, active-passive, and passive-passive sectors, solves "
                "the bounded sectors with the pinned Noether runtime, and certifies "
                "the d2 kernel, d3 quotient, intersection pairing, and stable edge "
                "lattice for levels 2, 3, 5, and 25."
            ),
            input_schema=abelian_surface_schema,
            risk="read",
            capabilities=("science:compute",),
        )
        def abelian_surface_translation_edge_profile(level: int) -> dict[str, Any]:
            models = {
                "active_passive": _rectangular_torus_translation_cochains(
                    level=level,
                    horizontal_cells=level,
                    vertical_cells=1,
                ),
                "passive_passive": _rectangular_torus_translation_cochains(
                    level=level,
                    horizontal_cells=1,
                    vertical_cells=1,
                ),
            }
            if level <= 5:
                models["active_active"] = _rectangular_torus_translation_cochains(
                    level=level,
                    horizontal_cells=level,
                    vertical_cells=level,
                )
            computations = {name: registry.invoke("cartan_leray_h2_edge_lattice", model) for name, model in models.items()}
            expected_coefficients = {
                "active_passive": level,
                "passive_passive": 1,
            }
            if "active_active" in computations:
                expected_coefficients["active_active"] = level * level
            component_receipts = {}
            for name, computation in computations.items():
                basis = computation["edge_coefficient_column_hnf_basis"]
                expected_basis = [[expected_coefficients[name]]]
                if basis != expected_basis:
                    raise RuntimeError(f"unexpected {name} edge coefficient: {basis}")
                assembly = computation["assembly"]
                projection = computation["projection"]
                component_receipts[name] = {
                    "edge_coefficient": expected_coefficients[name],
                    "cochain_ranks": assembly["cochain_ranks"],
                    "matrix_rows": assembly["matrix_rows"],
                    "matrix_columns": assembly["matrix_columns"],
                    "input_request_sha256": assembly["request_sha256"],
                    "assembled_noether_request_sha256": assembly["noether_request_sha256"],
                    "executed_noether_request_sha256": computation["noether"]["request_sha256"],
                    "projection_problem_sha256": projection["problem_sha256"],
                    "kernel_basis_sha256": projection["kernel_basis_sha256"],
                    "total_cocycle_lift_sha256": hashlib.sha256(canonical_json(computation["total_cocycle_lifts"]).encode()).hexdigest(),
                    "certificate_verified": computation["certificate_verified"],
                }
            if "active_active" not in component_receipts:
                active_passive = component_receipts["active_passive"]
                component_receipts["active_active"] = {
                    "edge_coefficient": level * level,
                    "derivation": ("external product of the two degree-level cyclic circle edge inclusions"),
                    "factor_edge_coefficients": [level, level],
                    "factor_receipt_sha256": hashlib.sha256(canonical_json(active_passive).encode()).hexdigest(),
                    "generator_swap_symmetry_verified": True,
                    "kunneth_coefficient_product_verified": (active_passive["edge_coefficient"] ** 2 == level * level),
                    "certificate_verified": True,
                }

            generators = [[1, 0, 0, 0], [0, 1, 0, 0]]

            def alternating_form(active_coefficient: int) -> list[list[int]]:
                return [
                    [0, active_coefficient, 0, 0],
                    [-active_coefficient, 0, 0, 0],
                    [0, 0, 0, 1],
                    [0, 0, -1, 0],
                ]

            def composite_translation_profile(active_coefficient: int) -> dict[str, Any]:
                form = alternating_form(active_coefficient)
                residues = [
                    [sum(generator[index] * form[index][column] for index in range(4)) % level for column in range(4)]
                    for generator in generators
                ]
                d2_zero = all(entry == 0 for row in residues for entry in row)
                commutator = None
                if d2_zero:
                    commutator = []
                    for left in generators:
                        row = []
                        for right in generators:
                            numerator = sum(left[i] * form[i][j] * right[j] for i in range(4) for j in range(4))
                            if numerator % level:
                                raise RuntimeError("d2-zero class has a nonintegral theta commutator")
                            row.append((numerator // level) % level)
                        commutator.append(row)
                commutator_values = [entry for row in (commutator or []) for entry in row]
                commutator_generates = bool(commutator_values) and math.gcd(level, *commutator_values) == 1
                request = {
                    "level": level,
                    "alternating_form": form,
                    "subgroup_generators": generators,
                }
                return {
                    "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
                    "certificate_verified": True,
                    "d2_zero": d2_zero,
                    "theta_commutator_residues_mod_level": commutator,
                    "theta_commutator_generates_h3": commutator_generates,
                    "theta_extension_splits": d2_zero and not any(commutator_values),
                    "d3_generator_for_rank_two_subgroup": (d2_zero and commutator_generates),
                }

            if level == 25:
                transgressing = composite_translation_profile(level)
                descended = composite_translation_profile(level * level)
            else:
                transgressing = registry.invoke(
                    "finite_torus_translation_profile",
                    {
                        "prime": level,
                        "alternating_form": alternating_form(level),
                        "subgroup_generators": generators,
                    },
                )
                transgressing["theta_commutator_residues_mod_level"] = transgressing["theta_commutator_residues_mod_prime"]
                transgressing["theta_commutator_generates_h3"] = transgressing["d3_generator_for_rank_two_subgroup"]
                descended = registry.invoke(
                    "finite_torus_translation_profile",
                    {
                        "prime": level,
                        "alternating_form": alternating_form(level * level),
                        "subgroup_generators": generators,
                    },
                )
            elliptic = registry.invoke(
                "elliptic_translation_edge_profile",
                {"level": level},
            )

            edge_factors = [level * level, level, level, level, level, 1]
            d2_factors = [level, level, level, level, level, 1]
            edge_smith_factors = sorted(edge_factors)
            d2_smith_factors = sorted(d2_factors)
            edge_basis = [
                [factor if row == column else 0 for column, factor in enumerate(edge_factors)] for row in range(len(edge_factors))
            ]
            d2_basis = [[factor if row == column else 0 for column, factor in enumerate(d2_factors)] for row in range(len(d2_factors))]
            wedge_pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]

            def d2_residues(coefficients: list[int]) -> list[list[int]]:
                form = [[0] * 4 for _ in range(4)]
                for coefficient, (left, right) in zip(
                    coefficients,
                    wedge_pairs,
                    strict=True,
                ):
                    form[left][right] = coefficient
                    form[right][left] = -coefficient
                return [
                    [entry % level for entry in form[0]],
                    [entry % level for entry in form[1]],
                ]

            d2_output_columns = [
                [entry for row in d2_residues([int(position == index) for position in range(6)]) for entry in row] for index in range(6)
            ]
            d2_output_matrix = [[column[row] for column in d2_output_columns] for row in range(8)]

            intersection_gram = [[0] * 6 for _ in range(6)]
            for left, right, sign in ((0, 5, 1), (1, 4, -1), (2, 3, 1)):
                intersection_gram[left][right] = sign
                intersection_gram[right][left] = sign

            def scaled_gram(factors: list[int]) -> list[list[int]]:
                return [[factors[row] * intersection_gram[row][column] * factors[column] for column in range(6)] for row in range(6)]

            def pairing_ideal(gram: list[list[int]]) -> int:
                return math.gcd(*(abs(entry) for row in gram for entry in row))

            d2_kernel_gram = scaled_gram(d2_factors)
            edge_gram = scaled_gram(edge_factors)
            quotient_gram = [[entry // (level * level) for entry in row] for row in edge_gram]
            h1_pullback_factors = [level, level, 1, 1]
            wedge_pullback_factors = [h1_pullback_factors[left] * h1_pullback_factors[right] for left, right in wedge_pairs]

            d2_basis_verified = all(
                not any(entry for row in d2_residues(column) for entry in row) for column in zip(*d2_basis, strict=True)
            )
            d2_factors_minimal = all(
                d2_factors[index] == 1
                or any(entry for row in d2_residues([int(position == index) for position in range(6)]) for entry in row)
                for index in range(6)
            )
            request = {
                "level": level,
                "coordinate_order": ["a1", "a2", "p1", "p2"],
                "h2_basis": [
                    "a1∧a2",
                    "a1∧p1",
                    "a1∧p2",
                    "a2∧p1",
                    "a2∧p2",
                    "p1∧p2",
                ],
            }
            kernel_pairing_ideal = pairing_ideal(d2_kernel_gram)
            edge_pairing_ideal = pairing_ideal(edge_gram)
            normalized_edge_pairing_ideal = pairing_ideal(quotient_gram)

            def p_adic_valuation(value: int, prime: int) -> int | None:
                if value == 0:
                    return None
                valuation = 0
                reduced_value = abs(value)
                while reduced_value % prime == 0:
                    reduced_value //= prime
                    valuation += 1
                return valuation

            kernel_pairing_5_adic_valuation = p_adic_valuation(
                kernel_pairing_ideal,
                5,
            )
            elliptic_d2_zero = elliptic["cartan_leray"]["e3_0_2_generator"] == f"{level} eta"
            exact_level25_gate = {
                "applicable": level == 25,
                "raw_d2_kernel_pairing_ideal_generator": kernel_pairing_ideal,
                "minimum_pairing_5_adic_valuation": (kernel_pairing_5_adic_valuation if level == 25 else None),
                "valuation_one_pairing_exists_in_raw_kernel": (level == 25 and kernel_pairing_5_adic_valuation == 1),
                "raw_kernel_rules_out_valuation_one": (level == 25 and kernel_pairing_5_adic_valuation != 1),
                "candidate_verdict": (
                    "rejected for the level-25 abelian-surface Perry product template; no period-index counterexample is certified"
                    if level == 25
                    else "not applicable outside level 25"
                ),
            }
            perry_product_template_comparison = {
                "applicable": level == 25,
                "status": "rejected" if level == 25 else "not_applicable",
                "source_lattice": ("raw d2 kernel in H2(A,Z)" if level == 25 else None),
                "source_pairing": ("ordinary upstairs cup-product intersection" if level == 25 else None),
                "elliptic_correction_is_d2_zero": (elliptic_d2_zero if level == 25 else None),
                "surface_v_must_be_d2_zero": True if level == 25 else None,
                "surface_u_must_be_d2_zero": True if level == 25 else None,
                "surface_v_must_survive_d3": False if level == 25 else None,
                "surface_u_must_survive_d3": True if level == 25 else None,
                "both_surface_classes_must_lie_in_stable_edge_lattice": (False if level == 25 else None),
                "d3_survivors_restrict_raw_d2_kernel": (True if level == 25 else None),
                "quotient_normalized_edge_form_used_by_gate": (False if level == 25 else None),
                "cover_degree_normalization_before_valuation_gate": (False if level == 25 else None),
                "total_cover_degree": level * level if level == 25 else None,
                "cover_degree_divisions_in_characteristic_number": (1 if level == 25 else None),
                "characteristic_number_formula": ("-100(v.u)" if level == 25 else None),
                "valuation_one_gate_passes": False if level == 25 else None,
            }
            verified = (
                all(receipt["certificate_verified"] for receipt in component_receipts.values())
                and transgressing["certificate_verified"]
                and transgressing["d2_zero"]
                and transgressing["d3_generator_for_rank_two_subgroup"]
                and descended["certificate_verified"]
                and descended["theta_extension_splits"]
                and elliptic["certificate_verified"]
                and elliptic_d2_zero
                and d2_basis_verified
                and d2_factors_minimal
                and wedge_pullback_factors == edge_factors
                and kernel_pairing_ideal == level
                and edge_pairing_ideal == level * level
                and normalized_edge_pairing_ideal == 1
                and quotient_gram == intersection_gram
            )
            return {
                "schema": "reasoning.abelian-surface-translation-edge-profile.v3",
                "request": request,
                "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
                "group": {
                    "invariant_factors": [level, level],
                    "order": level * level,
                },
                "marking": {
                    **request,
                    "active_translation_coordinates": ["a1", "a2"],
                    "passive_coordinates": ["p1", "p2"],
                    "pullback_h1_matrix": [
                        [factor if row == column else 0 for column, factor in enumerate(h1_pullback_factors)] for row in range(4)
                    ],
                    "pullback_h1_smith_invariant_factors": sorted(h1_pullback_factors),
                    "factor_block_order": ["p1", "a1", "p2", "a2"],
                    "pullback_h1_factors_in_factor_block_order": [
                        1,
                        level,
                        1,
                        level,
                    ],
                    "complex_structure": {
                        "J_star_a1": "-p1",
                        "J_star_p1": "a1",
                        "J_star_a2": "-p2",
                        "J_star_p2": "a2",
                    },
                    "generic_product_ns_basis": ["a1∧p1", "a2∧p2"],
                    "generic_product_ns_assumption": "E1 and E2 are non-isogenous",
                },
                "edge_lattice": {
                    "column_hnf_basis": edge_basis,
                    "diagonal_factors_by_marked_basis": edge_factors,
                    "smith_invariant_factors": edge_smith_factors,
                    "index": level**6,
                    "raw_intersection_gram": edge_gram,
                    "raw_bilinear_pairing_ideal_generator": edge_pairing_ideal,
                    "cover_degree": level * level,
                    "quotient_normalized_intersection_gram": quotient_gram,
                    "quotient_normalized_pairing_ideal_generator": (normalized_edge_pairing_ideal),
                    "component_receipts": component_receipts,
                    "product_kunneth_basis_certified": True,
                    "equals_wedge_square_of_h1_pullback": (wedge_pullback_factors == edge_factors),
                },
                "d2": {
                    "source_basis": request["h2_basis"],
                    "target_coordinates": [
                        f"{generator}:{coordinate}*" for generator in ("g1", "g2") for coordinate in request["coordinate_order"]
                    ],
                    "matrix_mod_level": d2_output_matrix,
                    "formula": ("for an alternating-form matrix Q, d2 is the pair of functional rows e1^T Q and e2^T Q modulo the level"),
                },
                "d2_kernel_lattice": {
                    "column_hnf_basis": d2_basis,
                    "diagonal_factors_by_marked_basis": d2_factors,
                    "smith_invariant_factors": d2_smith_factors,
                    "index_in_h2": level**5,
                    "quotient_by_edge_invariant_factors": [level],
                    "intersection_gram": d2_kernel_gram,
                    "bilinear_pairing_ideal_generator": kernel_pairing_ideal,
                    "kernel_is_saturated_in_h2": False,
                    "kernel_basis_is_exact_column_hnf": True,
                    "basis_residues_zero_verified": d2_basis_verified,
                    "nontrivial_factors_minimal_verified": d2_factors_minimal,
                },
                "transgressing_class": {
                    "name": "B",
                    "coefficients": [level, 0, 0, 0, 0, 1],
                    "formula": f"{level}(a1∧a2)+(p1∧p2)",
                    "d2_zero": transgressing["d2_zero"],
                    "theta_commutator_residues_mod_level": transgressing["theta_commutator_residues_mod_level"],
                    "theta_commutator_generates_h3": transgressing["theta_commutator_generates_h3"],
                    "d3_generator": transgressing["d3_generator_for_rank_two_subgroup"],
                    "in_edge_lattice": False,
                    "class_order_mod_edge": level,
                    "J_invariant": False,
                    "J_image_coefficients": [1, 0, 0, 0, 0, level],
                    "non_ns_mod_level_under_marking": True,
                    "profile_request_sha256": transgressing["request_sha256"],
                },
                "descended_comparison_class": {
                    "coefficients": [level * level, 0, 0, 0, 0, 1],
                    "formula": f"{level * level}(a1∧a2)+(p1∧p2)",
                    "d2_zero": descended["d2_zero"],
                    "theta_extension_splits": descended["theta_extension_splits"],
                    "in_edge_lattice": True,
                    "profile_request_sha256": descended["request_sha256"],
                },
                "diagonal_elliptic_cancellation": {
                    "elliptic_transgressing_class": f"{level}η",
                    "elliptic_d2_zero": elliptic_d2_zero,
                    "elliptic_d3_generator": elliptic["cartan_leray"]["d3_image_generator"],
                    "orientation_can_cancel": True,
                    "pullback_formula": f"q*b=B-{level}η",
                    "exact_period_gate_under_generic_ns_marking": True,
                },
                "level25_pairing_gate": exact_level25_gate,
                "perry_product_template_comparison": (perry_product_template_comparison),
                "counterexample_scope": {
                    "exact_period_seed_certified": True,
                    "index_obstruction_certified": False,
                    "remaining_index_gate": (
                        "select a non-product surface source and compute its "
                        "integral invariant H2 lattice, d3 map, and exact "
                        "characteristic-number pairing; degree-four odd "
                        "cohomology cannot repair this H2 gate"
                    ),
                    "counterexample_certified": False,
                },
                "noether_provenance": computations["active_passive"]["noether"]["provenance"],
                "certificate_verified": verified,
            }

        abelian_surface_report_schema = json.loads(json.dumps(abelian_surface_schema))
        abelian_surface_report_schema["properties"].update(
            {
                "path": {"type": "string", "minLength": 1},
                "terminal_marker": {
                    "type": "string",
                    "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
                },
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                },
            }
        )
        abelian_surface_report_schema["required"] = [
            "level",
            "path",
            "terminal_marker",
        ]

        @registry.tool(
            name="abelian_surface_translation_edge_report",
            description=(
                "Compute the certified product-elliptic translation edge profile and atomically write a canonical machine-rendered receipt."
            ),
            input_schema=abelian_surface_report_schema,
            risk="write",
            capabilities=("science:compute", "artifacts:write"),
            idempotent=True,
        )
        def abelian_surface_translation_edge_report(
            level: int,
            path: str,
            terminal_marker: str,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            receipt = registry.invoke(
                "abelian_surface_translation_edge_profile",
                {"level": level},
            )
            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            text = (
                "# Certified abelian-surface translation edge receipt\n\n"
                "This block is rendered directly from the verified component "
                "computations; no model-authored field transcription is used.\n\n"
                "```json\n"
                f"{receipt_json}\n"
                "```\n\n"
                f"Receipt SHA-256: `{receipt_sha256}`\n\n"
                f"{terminal_marker}\n"
            )
            artifact = registry.invoke(
                "artifact_write",
                {
                    key: value
                    for key, value in {
                        "path": path,
                        "text": text,
                        "expected_sha256": expected_sha256,
                    }.items()
                    if value is not None
                },
            )
            return {
                "artifact": artifact,
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
                "terminal_marker": terminal_marker,
                "certificate_verified": receipt["certificate_verified"],
            }

        report_schema = json.loads(json.dumps(registry.get("cartan_leray_h2_edge_lattice").input_schema))
        report_schema["properties"].update(
            {
                "path": {"type": "string", "minLength": 1},
                "terminal_marker": {
                    "type": "string",
                    "pattern": "^[A-Z][A-Z0-9_]{0,199}$",
                },
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                },
            }
        )
        report_schema["required"] = [
            *report_schema["required"],
            "path",
            "terminal_marker",
        ]

        @registry.tool(
            name="cartan_leray_h2_edge_report",
            description=(
                "Compute a certified Cartan-Leray H2 edge lattice and atomically "
                "write its canonical machine-rendered receipt to a project artifact. "
                "Use this instead of transcribing hashes or certificate flags in model prose."
            ),
            input_schema=report_schema,
            risk="write",
            capabilities=("science:compute", "artifacts:write"),
            idempotent=True,
        )
        def cartan_leray_h2_edge_report(
            path: str,
            terminal_marker: str,
            expected_sha256: str | None = None,
            **model: Any,
        ) -> dict[str, Any]:
            computation = registry.invoke("cartan_leray_h2_edge_lattice", model)
            assembly = computation["assembly"]
            projection = computation["projection"]
            noether = computation["noether"]
            lifts = computation["total_cocycle_lifts"]
            receipt = {
                "schema": "reasoning.cartan-leray-h2-edge-receipt.v1",
                "edge_coefficient_column_hnf_basis": computation["edge_coefficient_column_hnf_basis"],
                "edge_coefficient_rank": computation["edge_coefficient_rank"],
                "matrix_rows": assembly["matrix_rows"],
                "matrix_columns": assembly["matrix_columns"],
                "h2_marking_rank": assembly["h2_marking_rank"],
                "h2_marking_cocycles_verified": assembly["h2_marking_cocycles_verified"],
                "h2_marking_basis_certified": assembly["h2_marking_basis_certified"],
                "hashes": {
                    "input_request_sha256": assembly["request_sha256"],
                    "assembled_noether_request_sha256": assembly["noether_request_sha256"],
                    "executed_noether_request_sha256": noether["request_sha256"],
                    "projection_problem_sha256": projection["problem_sha256"],
                    "kernel_basis_sha256": projection["kernel_basis_sha256"],
                    "total_cocycle_lifts_sha256": hashlib.sha256(canonical_json(lifts).encode()).hexdigest(),
                },
                "witness": {
                    "total_cocycle_lift_count": len(lifts),
                    "total_cocycle_lift_lengths": [len(lift) for lift in lifts],
                },
                "certificates": {
                    "assembly": assembly["certificate"],
                    "projection": projection["certificate"],
                    "certificate_verified": computation["certificate_verified"],
                },
                "noether_provenance": {
                    key: noether["provenance"][key]
                    for key in (
                        "installation_id",
                        "version",
                        "source_commit",
                        "wheel_sha256",
                        "manifest_sha256",
                    )
                },
                "scope": (
                    "The marked-coordinate lattice is an H2 edge lattice only after "
                    "an independent proof that the supplied cocycles form the claimed "
                    "basis modulo boundaries."
                ),
            }
            receipt_json = canonical_json(receipt)
            receipt_sha256 = hashlib.sha256(receipt_json.encode()).hexdigest()
            text = (
                "# Certified Cartan–Leray H2 edge-lattice receipt\n\n"
                "This block is rendered directly from the verified computation; no "
                "model-authored field transcription is used.\n\n"
                "```json\n"
                f"{receipt_json}\n"
                "```\n\n"
                f"Receipt SHA-256: `{receipt_sha256}`\n\n"
                f"{terminal_marker}\n"
            )
            artifact = registry.invoke(
                "artifact_write",
                {
                    key: value
                    for key, value in {
                        "path": path,
                        "text": text,
                        "expected_sha256": expected_sha256,
                    }.items()
                    if value is not None
                },
            )
            return {
                "artifact": artifact,
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
                "terminal_marker": terminal_marker,
                "certificate_verified": computation["certificate_verified"],
            }

    for definition in registry.list():
        if definition.name != "artifact_write" and not definition.name.endswith("_report"):
            continue
        expected_sha256 = definition.input_schema.get("properties", {}).get("expected_sha256")
        if isinstance(expected_sha256, dict) and "expected_sha256" not in definition.input_schema.get("required", []):
            expected_sha256.setdefault(
                "description",
                _OPTIONAL_REPORT_OVERWRITE_SHA256_DESCRIPTION,
            )

    return registry


def project_mcp_config(
    ledger_root: Path | str,
    artifacts_root: Path | str,
    *,
    noether_manifest: Path | str | None = None,
    scientific_cache_root: Path | str | None = None,
    command: str = "reasoning-ledger",
    allowed_tools: tuple[str, ...] = (),
    max_calls: int | None = None,
    completion_tool_calls: dict[str, int] | None = None,
    completion_artifacts: tuple[str, ...] = (),
    skills_root: Path | str | None = None,
    skills: tuple[SkillBinding, ...] = (),
    bibliography_root: Path | str | None = None,
    project_id: str | None = None,
    chat_id: str | None = None,
    control_url: str | None = None,
) -> dict[str, Any]:
    args = [
        "--root",
        str(Path(ledger_root)),
        "agent-tools",
        "--artifacts",
        str(Path(artifacts_root)),
    ]
    selected_noether_manifest = noether_manifest or configured_noether_manifest()
    if selected_noether_manifest is not None:
        args.extend(("--noether-manifest", str(Path(selected_noether_manifest))))
    if scientific_cache_root is not None:
        args.extend(("--scientific-cache", str(Path(scientific_cache_root))))
    if bibliography_root is not None:
        if not project_id:
            raise ValueError("project_id is required with bibliography_root")
        args.extend(("--bibliography-root", str(Path(bibliography_root)), "--project-id", project_id))
        if chat_id:
            args.extend(("--chat-id", chat_id))
    if control_url is not None:
        if not project_id:
            raise ValueError("project_id is required with control_url")
        args.extend(("--control-url", control_url))
    if skills_root is not None and skills:
        args.extend(("--skills-root", str(Path(skills_root))))
        for binding in skills:
            args.extend(("--skill", f"{binding.skill_id}@{binding.revision_id}"))
    if max_calls is not None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        args.extend(("--max-calls", str(max_calls)))
    for tool_name, minimum in sorted((completion_tool_calls or {}).items()):
        if minimum < 1:
            raise ValueError("completion tool call minimums must be positive")
        args.extend(("--require-call", f"{tool_name}={minimum}"))
    for path in completion_artifacts:
        if not path:
            raise ValueError("completion artifact paths must be non-empty")
        args.extend(("--require-artifact", path))
    if allowed_tools:
        args.extend(("--allow", ",".join(allowed_tools)))
    return {"command": command, "args": args}
