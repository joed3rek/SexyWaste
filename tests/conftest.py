import pytest

from backend import resources


@pytest.fixture(autouse=True)
def _own_resource_store(tmp_path, monkeypatch):
    """Every test gets empty resource inventories, so vehicles or staff entered in data/ops.db never cap a test plan."""
    monkeypatch.setattr(resources, "DB_PATH", tmp_path / "ops.db")
