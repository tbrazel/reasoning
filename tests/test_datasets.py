import json

import pytest

from reasoning_ledger.agents import DatasetError, DatasetStore


def _cases(label="first"):
    return [
        {
            "case_id": "case-1",
            "name": "Case 1",
            "inputs": {"text": label},
            "expected": {"decision": "allow"},
        }
    ]


def test_dataset_store_creates_immutable_revisions_and_detects_stale_saves(tmp_path):
    store = DatasetStore(tmp_path / "datasets")
    first = store.create("Moderation corpus", description="Versioned cases", cases=_cases())

    assert first["dataset_id"] == "moderation-corpus"
    assert store.list()[0]["revision_id"] == first["revision_id"]
    assert store.get(first["dataset_id"])["cases"][0]["inputs"]["text"] == "first"

    changed = {**first, "cases": _cases("second")}
    second = store.save(changed, expected_revision_id=first["revision_id"])
    assert second["parent_revision_id"] == first["revision_id"]
    assert store.get(first["dataset_id"], first["revision_id"])["cases"][0]["inputs"]["text"] == "first"
    assert store.get(first["dataset_id"])["cases"][0]["inputs"]["text"] == "second"

    with pytest.raises(DatasetError, match="changed since"):
        store.save(changed, expected_revision_id=first["revision_id"])

    revision_files = list((tmp_path / "datasets" / first["dataset_id"] / "revisions").glob("*.json"))
    assert len(revision_files) == 2
    assert all(json.loads(path.read_text())["dataset_id"] == first["dataset_id"] for path in revision_files)


def test_dataset_store_validates_ids_inputs_and_finite_json(tmp_path):
    store = DatasetStore(tmp_path / "datasets")
    with pytest.raises(DatasetError, match="duplicate dataset case id"):
        store.create("Duplicates", cases=[*_cases(), *_cases()])
    with pytest.raises(DatasetError, match="inputs must be an object"):
        store.create("Bad inputs", cases=[{"case_id": "case-1", "name": "Case", "inputs": "bad"}])
    with pytest.raises(DatasetError, match="finite JSON"):
        store.create("Infinity", cases=[{"case_id": "case-1", "name": "Case", "inputs": {"x": float("inf")}}])
