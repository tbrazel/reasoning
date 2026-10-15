"""Deployment partitioning retains every case once and rejects invalid scopes.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import deployment_partition, parse_deployment_shard, pytest_collection_modifyitems


@pytest.mark.parametrize("value", ["", "0/3", "4/3", "1/0", "1/17", "1", "1/3/4", "one/three", "1.5/3"])
def test_invalid_shards_are_refused(value):
    with pytest.raises(pytest.UsageError):
        parse_deployment_shard(value)


def test_shards_have_stable_disjoint_complete_coverage():
    assert parse_deployment_shard(None) is None
    assert parse_deployment_shard("1/3") == (0, 3)
    ids = [f"tests/test_public_bots.py::fixture_case[{index}]" for index in range(500)]
    partitions = [{node for node in ids if deployment_partition(node, 3) == shard} for shard in range(3)]
    assert set.union(*partitions) == set(ids)
    assert sum(map(len, partitions)) == len(ids)
    assert all(partitions)
    for index in range(3):
        options = {"--deployment": True, "--science": False, "--skip-sqlite-params": False,
                   "--deployment-shard": f"{index + 1}/3"}
        deselected = []
        config = SimpleNamespace(getoption=options.get,
            hook=SimpleNamespace(pytest_deselected=lambda items, target=deselected: target.extend(items)))
        items = [SimpleNamespace(nodeid=node, path=Path("tests/test_public_bots.py")) for node in ids]
        pytest_collection_modifyitems(config, items)
        assert {item.nodeid for item in items} == partitions[index]
        assert len(items) + len(deselected) == len(ids)
