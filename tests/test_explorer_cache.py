import json
from pathlib import Path

from reasoning_ledger.explorer_cache import EXPLORER_INDEX_VERSION, ExplorerIndexCache


def test_explorer_cache_round_trip_is_versioned_and_owner_readable(tmp_path: Path):
    path = tmp_path / "settings" / "explorer-index.json"
    cache = ExplorerIndexCache(path)

    written = cache.write([{"id": "alpha", "name": "Alpha"}])

    assert written["version"] == EXPLORER_INDEX_VERSION
    assert cache.load() == written
    assert path.stat().st_mode & 0o777 == 0o600


def test_explorer_cache_ignores_missing_corrupt_and_future_files(tmp_path: Path):
    path = tmp_path / "settings" / "explorer-index.json"
    cache = ExplorerIndexCache(path)
    assert cache.load() is None

    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert cache.load() is None

    path.write_text(
        json.dumps({"version": 999, "generated_at": "later", "projects": []}),
        encoding="utf-8",
    )
    assert cache.load() is None

