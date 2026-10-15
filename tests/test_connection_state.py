from pathlib import Path

from reasoning_ledger.connection_state import ConnectionStateStore


def test_connection_state_is_durable_private_and_clearable(tmp_path: Path):
    path = tmp_path / "settings" / "connection-state.json"
    store = ConnectionStateStore(path)

    state = store.update(
        "openrouter",
        validation_status="valid",
        validation_detail="Verified",
    )

    assert state["validation_status"] == "valid"
    assert ConnectionStateStore(path).get("openrouter") == state
    assert path.stat().st_mode & 0o777 == 0o600

    store.clear("openrouter")
    assert store.get("openrouter") == {}

