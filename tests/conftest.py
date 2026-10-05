import pytest

from backend import fleet


@pytest.fixture(autouse=True)
def _own_fleet_store(tmp_path, monkeypatch):
    """Every test gets an empty fleet inventory, so vehicles entered in data/ops.db never cap a test plan."""
    monkeypatch.setattr(fleet, "DB_PATH", tmp_path / "ops.db")
