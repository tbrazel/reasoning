"""Immutable, tenant-scoped workflow DAG definitions for gamma topics.

Definition persistence does not dispatch jobs or make provider requests.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from .compute_catalog import canonical
from .compute_store import ComputeStore
from .store import Store, StoreError, _id, _row

IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
CLIENT = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
OUTPUTS = {"compute": {"logs", "artifacts"}, "llm": {"text"}, "approval": {"decision"}}


def _fields(value, expected: set[str], label: str):
    if not isinstance(value, dict) or set(value) != expected:
        raise StoreError(400, f"invalid {label} fields")


def _text(value, maximum: int, label: str, *, single_line: bool = False):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip() or "\0" in value:
        raise StoreError(400, f"invalid {label}")
    if single_line and any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise StoreError(400, f"invalid {label}")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise StoreError(400, f"invalid {label}") from exc
    return value


def definition(value: dict) -> dict:
    """Validate a bounded DAG; never evaluate templates, code, paths, or expressions."""
    _fields(value, {"format", "name", "nodes"}, "workflow definition")
    if type(value["format"]) is not int or value["format"] != 1:
        raise StoreError(400, "unsupported workflow definition format")
    name = _text(value["name"], 200, "workflow name", single_line=True)
    if not isinstance(value["nodes"], list) or not 1 <= len(value["nodes"]) <= 32:
        raise StoreError(400, "workflow requires 1 to 32 nodes")
    nodes = {}
    edges = 0
    for node in value["nodes"]:
        _fields(node, {"id", "kind", "depends_on", "inputs", "parameters"}, "workflow node")
        node_id, kind = node["id"], node["kind"]
        if not isinstance(node_id, str) or not IDENTIFIER.fullmatch(node_id) or node_id in nodes:
            raise StoreError(400, "invalid or duplicate workflow node identifier")
        if not isinstance(kind, str) or kind not in OUTPUTS:
            raise StoreError(400, "unsupported workflow node kind")
        dependencies = node["depends_on"]
        if (
            not isinstance(dependencies, list)
            or len(dependencies) > 31
            or any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item) for item in dependencies)
            or len(set(dependencies)) != len(dependencies)
            or node_id in dependencies
        ):
            raise StoreError(400, "invalid workflow dependencies")
        edges += len(dependencies)
        if edges > 128:
            raise StoreError(400, "workflow exceeds 128 dependency edges")
        parameters = node["parameters"]
        if kind == "compute":
            _fields(parameters, {"image_id", "language", "program", "resources"}, "compute node parameters")
            if not isinstance(parameters["image_id"], str) or not IDENTIFIER.fullmatch(parameters["image_id"]):
                raise StoreError(400, "invalid workflow image identifier")
            parameters = ComputeStore.parameters(
                parameters["image_id"], parameters["language"], parameters["program"], parameters["resources"]
            )
        elif kind == "llm":
            expected = {"model", "prompt", "max_completion_tokens"}
            if isinstance(parameters, dict) and "effort" in parameters:
                expected.add("effort")
            _fields(parameters, expected, "LLM node parameters")
            from .reasoning_effort import checked

            effort = checked(parameters.get("effort"))
            model = _text(parameters["model"], 200, "workflow model", single_line=True)
            prompt = _text(parameters["prompt"], 16000, "workflow prompt")
            maximum = parameters["max_completion_tokens"]
            if maximum is not None and (type(maximum) is not int or not 16 <= maximum <= 2**31 - 1):
                raise StoreError(400, "workflow completion limit must be null or 16 to 2147483647 tokens")
            parameters = {"model": model, "prompt": prompt, "max_completion_tokens": maximum}
            if effort:
                parameters["effort"] = effort
        else:
            _fields(parameters, {"message", "required_role"}, "approval node parameters")
            message = _text(parameters["message"], 2000, "approval message")
            if not isinstance(parameters["required_role"], str) or parameters["required_role"] not in {"member", "admin"}:
                raise StoreError(400, "approval role must be member or admin")
            parameters = {"message": message, "required_role": parameters["required_role"]}
        inputs = node["inputs"]
        if not isinstance(inputs, dict) or len(inputs) > 32:
            raise StoreError(400, "invalid workflow inputs")
        normalized_inputs = {}
        for alias, binding in inputs.items():
            if not isinstance(alias, str) or not IDENTIFIER.fullmatch(alias):
                raise StoreError(400, "invalid workflow input alias")
            _fields(binding, {"node", "output"}, "workflow input binding")
            if not isinstance(binding["node"], str) or binding["node"] not in dependencies or not isinstance(binding["output"], str):
                raise StoreError(400, "workflow input must reference a direct dependency")
            normalized_inputs[alias] = {"node": binding["node"], "output": binding["output"]}
        nodes[node_id] = {
            "id": node_id,
            "kind": kind,
            "depends_on": sorted(dependencies),
            "inputs": normalized_inputs,
            "parameters": parameters,
        }
    for node in nodes.values():
        if any(parent not in nodes for parent in node["depends_on"]):
            raise StoreError(400, "workflow dependency does not exist")
        for binding in node["inputs"].values():
            if binding["output"] not in OUTPUTS[nodes[binding["node"]]["kind"]]:
                raise StoreError(400, "workflow input references an unsupported dependency output")
    result = {"format": 1, "name": name, "nodes": [nodes[key] for key in sorted(nodes)]}
    node_order(result)
    # Bound encoded bytes, not code points; canonical JSON also rejects non-finite numbers.
    if len(canonical(result).encode("utf-8")) > 32768:
        raise StoreError(400, "workflow definition exceeds 32 KiB")
    return result


def node_order(value: dict) -> list[str]:
    remaining = {node["id"]: set(node["depends_on"]) for node in value["nodes"]}
    order = []
    while remaining:
        ready = sorted(key for key, parents in remaining.items() if not parents)
        if not ready:
            raise StoreError(400, "workflow dependencies contain a cycle")
        order.extend(ready)
        for key in ready:
            del remaining[key]
        for parents in remaining.values():
            parents.difference_update(ready)
    return order


def read_definition(source: str, expected_digest: str) -> dict:
    """Reject inconsistent stored definitions before inspection or execution."""
    try:
        if not isinstance(source, str) or len(source.encode("utf-8")) > 32768:
            raise ValueError("invalid definition size")
        value = definition(json.loads(source))
        if canonical(value) != source or "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest() != expected_digest:
            raise ValueError("invalid definition digest")
        return value
    except (ValueError, TypeError, StoreError, RecursionError) as exc:
        raise ValueError("stored workflow definition integrity check failed") from exc


class WorkflowStore:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _topic(db, organization_id, topic_id):
        row = db.execute("SELECT kind FROM topics WHERE organization_id=? AND id=?", (organization_id, topic_id)).fetchone()
        if row is None:
            raise StoreError(404, "not found")
        if row["kind"] != "gamma":
            raise StoreError(400, "workflows require a gamma topic")

    @staticmethod
    def _workflow(db, organization_id, workflow_id):
        return _row(db.execute("SELECT * FROM workflows WHERE organization_id=? AND id=?", (organization_id, workflow_id)).fetchone())

    @staticmethod
    def _version(db, organization_id, workflow_id, version):
        row = _row(
            db.execute(
                "SELECT v.*,w.topic_id,r.client_id FROM workflow_versions v JOIN workflows w "
                "ON w.organization_id=v.organization_id AND w.id=v.workflow_id "
                "JOIN workflow_writes r ON r.organization_id=v.organization_id AND r.workflow_id=v.workflow_id AND r.version=v.version "
                "WHERE v.organization_id=? AND v.workflow_id=? AND v.version=?",
                (organization_id, workflow_id, version),
            ).fetchone()
        )
        try:
            value = read_definition(row.pop("definition_json"), row["definition_digest"])
        except ValueError as exc:
            raise StoreError(409, "stored workflow definition integrity check failed") from exc
        return {**row, "definition": value, "node_order": node_order(value)}

    def _write(self, db, user_id, organization_id, client_id, request, workflow_id=None):
        if not isinstance(client_id, str) or not CLIENT.fullmatch(client_id):
            raise StoreError(400, "workflow writes require a stable 16 to 128 character request identifier")
        encoded = canonical(request)
        replay = db.execute(
            "SELECT * FROM workflow_writes WHERE organization_id=? AND requested_by=? AND client_id=?",
            (organization_id, user_id, client_id),
        ).fetchone()
        if replay:
            if replay["request_json"] != encoded:
                raise StoreError(409, "workflow request identifier was used with different input")
            return self._version(db, organization_id, replay["workflow_id"], replay["version"])
        now = int(time.time())
        if (
            db.execute(
                "SELECT COUNT(*) FROM workflow_writes WHERE organization_id=? AND requested_by=? AND created_at>?",
                (organization_id, user_id, now - 3600),
            ).fetchone()[0]
            >= 60
        ):
            raise StoreError(429, "workflow definition write limit reached")
        if workflow_id is None:
            topic_id = request["topic_id"]
            if (
                db.execute("SELECT COUNT(*) FROM workflows WHERE organization_id=? AND topic_id=?", (organization_id, topic_id)).fetchone()[
                    0
                ]
                >= 100
            ):
                raise StoreError(409, "gamma topic has reached its 100 workflow limit")
            workflow_id, version = _id(), 1
            db.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?)", (workflow_id, organization_id, topic_id, user_id, now, version))
        else:
            workflow = self._workflow(db, organization_id, workflow_id)
            if workflow["head_version"] != request["expected_version"]:
                raise StoreError(409, "workflow changed; load its current version before editing")
            version = workflow["head_version"] + 1
            if version > 128:
                raise StoreError(409, "workflow has reached its 128 version limit")
            db.execute("UPDATE workflows SET head_version=? WHERE organization_id=? AND id=?", (version, organization_id, workflow_id))
        source = canonical(request["definition"])
        db.execute(
            "INSERT INTO workflow_versions VALUES(?,?,?,?,?,?,?)",
            (organization_id, workflow_id, version, source, "sha256:" + hashlib.sha256(source.encode()).hexdigest(), user_id, now),
        )
        db.execute(
            "INSERT INTO workflow_writes VALUES(?,?,?,?,?,?,?)", (organization_id, user_id, client_id, encoded, workflow_id, version, now)
        )
        self.store._audit(db, organization_id, user_id, "workflow.version.create", f"{workflow_id}:{version}")
        return self._version(db, organization_id, workflow_id, version)

    def create(self, user_id, organization_id, topic_id, client_id, value):
        with self.store.transaction(write=True) as db:
            self.store._topic_access(db, user_id, organization_id, topic_id, write=True)
            self._topic(db, organization_id, topic_id)
            return self._write(
                db, user_id, organization_id, client_id, {"operation": "create", "topic_id": topic_id, "definition": definition(value)}
            )

    def revise(self, user_id, organization_id, workflow_id, client_id, expected_version, value):
        with self.store.transaction(write=True) as db:
            workflow = self._workflow(db, organization_id, workflow_id)
            self.store._topic_access(db, user_id, organization_id, workflow["topic_id"], write=True)
            self._topic(db, organization_id, workflow["topic_id"])
            if type(expected_version) is not int or not 1 <= expected_version <= 128:
                raise StoreError(400, "invalid expected workflow version")
            return self._write(
                db,
                user_id,
                organization_id,
                client_id,
                {"operation": "revise", "workflow_id": workflow_id, "expected_version": expected_version, "definition": definition(value)},
                workflow_id,
            )

    def get(self, user_id, organization_id, workflow_id, version=None):
        if version is not None and (type(version) is not int or not 1 <= version <= 128):
            raise StoreError(400, "invalid workflow version")
        with self.store.transaction() as db:
            workflow = self._workflow(db, organization_id, workflow_id)
            self.store._topic_access(db, user_id, organization_id, workflow["topic_id"])
            return self._version(db, organization_id, workflow_id, workflow["head_version"] if version is None else version)

    def list(self, user_id, organization_id, topic_id, after="", limit=50):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError(400, "invalid workflow page limit")
        if not isinstance(after, str) or (after and not re.fullmatch(r"[a-f0-9]{32}", after)):
            raise StoreError(400, "invalid workflow page cursor")
        with self.store.transaction() as db:
            self.store._topic_access(db, user_id, organization_id, topic_id)
            self._topic(db, organization_id, topic_id)
            rows = db.execute(
                "SELECT w.id,w.topic_id,w.head_version AS version,v.definition_digest,v.created_by,v.created_at,"
                "json_extract(v.definition_json,'$.name') AS name FROM workflows w JOIN workflow_versions v "
                "ON v.organization_id=w.organization_id AND v.workflow_id=w.id AND v.version=w.head_version "
                "WHERE w.organization_id=? AND w.topic_id=? AND w.id>? ORDER BY w.id LIMIT ?",
                (organization_id, topic_id, after, limit),
            )
            return [dict(row) for row in rows]

    def versions(self, user_id, organization_id, workflow_id, before=129, limit=50):
        if type(limit) is not int or not 1 <= limit <= 50 or type(before) is not int or not 1 <= before <= 129:
            raise StoreError(400, "invalid workflow version page")
        with self.store.transaction() as db:
            workflow = self._workflow(db, organization_id, workflow_id)
            self.store._topic_access(db, user_id, organization_id, workflow["topic_id"])
            return [
                dict(row)
                for row in db.execute(
                    "SELECT version,definition_digest,created_by,created_at FROM workflow_versions "
                    "WHERE organization_id=? AND workflow_id=? AND version<? ORDER BY version DESC LIMIT ?",
                    (organization_id, workflow_id, before, limit),
                )
            ]
