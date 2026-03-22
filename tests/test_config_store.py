"""Tests for system config persistence — CRUD, validation, partial updates."""

import os
import tempfile
import pytest

from src.storage.config_store import ConfigStore
from src.data.system_config import SystemConfig, SolarArray, Battery


# ── Fixtures ─────────────────────────────────────────────────────────


SAMPLE_CONFIG = {
    "location": {"lat": 37.68, "lon": -122.40, "city": "Brisbane, CA"},
    "baseline_territory": "T",
    "heat_source": "electric",
    "rate_plan": "EV2-A",
    "provider": "PCE",
    "pcia_vintage": 2016,
    "income_tier": 3,
    "nem_version": "NEM2",
    "true_up_month": 1,
    "arrays": [
        {
            "name": "Array 1",
            "panels": 8,
            "panel_watts": 385,
            "make": "Longi",
            "inverter": "Enphase IQ7A",
            "inverter_watts_ac": 366,
            "type": "micro",
            "orientation": "south",
        }
    ],
    "batteries": [
        {"type": "Powerwall 2", "kwh": 13.5, "kw": 5.0, "efficiency": 0.90, "status": "working"}
    ],
    "vehicles": [{"make": "Tesla", "charger": "L2"}],
    "psh_by_month": {"Jan": 3.2, "Feb": 4.0, "Mar": 5.0},
}


@pytest.fixture
def store(tmp_path):
    """Create a ConfigStore backed by a temp directory."""
    return ConfigStore(db_dir=str(tmp_path))


@pytest.fixture
def populated_store(store):
    """A store with one config already saved."""
    store.save("test-home", SAMPLE_CONFIG)
    return store


# ── ConfigStore CRUD ─────────────────────────────────────────────────


class TestConfigStoreSave:
    def test_save_returns_status(self, store):
        result = store.save("my-home", SAMPLE_CONFIG)
        assert result["config_id"] == "my-home"
        assert result["status"] == "saved"
        assert "created_at" in result

    def test_save_duplicate_raises(self, populated_store):
        with pytest.raises(ValueError, match="already exists"):
            populated_store.save("test-home", SAMPLE_CONFIG)

    def test_save_empty_id_raises(self, store):
        with pytest.raises(ValueError, match="non-empty string"):
            store.save("", SAMPLE_CONFIG)

    def test_save_non_dict_raises(self, store):
        with pytest.raises(ValueError, match="must be a dict"):
            store.save("bad", "not a dict")


class TestConfigStoreGet:
    def test_get_existing(self, populated_store):
        result = populated_store.get("test-home")
        assert result is not None
        assert result["config_id"] == "test-home"
        assert result["config"]["rate_plan"] == "EV2-A"
        assert result["config"]["provider"] == "PCE"
        assert len(result["config"]["arrays"]) == 1

    def test_get_missing_returns_none(self, store):
        assert store.get("nonexistent") is None


class TestConfigStoreUpdate:
    def test_partial_update_scalar(self, populated_store):
        result = populated_store.update("test-home", {"rate_plan": "E-ELEC"})
        assert result["status"] == "updated"
        assert result["config"]["rate_plan"] == "E-ELEC"
        # Other fields unchanged
        assert result["config"]["provider"] == "PCE"

    def test_partial_update_nested(self, populated_store):
        result = populated_store.update("test-home", {"location": {"city": "San Mateo, CA"}})
        # Deep merge: city updated but lat/lon preserved
        assert result["config"]["location"]["city"] == "San Mateo, CA"
        assert result["config"]["location"]["lat"] == 37.68

    def test_update_replaces_list(self, populated_store):
        new_batteries = [
            {"type": "Powerwall 2", "kwh": 13.5, "kw": 5.0},
            {"type": "Powerwall 2", "kwh": 13.5, "kw": 5.0},
        ]
        result = populated_store.update("test-home", {"batteries": new_batteries})
        assert len(result["config"]["batteries"]) == 2

    def test_update_missing_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.update("ghost", {"rate_plan": "E-ELEC"})


class TestConfigStoreDelete:
    def test_delete_existing(self, populated_store):
        result = populated_store.delete("test-home")
        assert result["status"] == "deleted"
        assert populated_store.get("test-home") is None

    def test_delete_missing_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.delete("ghost")


class TestConfigStoreListAll:
    def test_list_empty(self, store):
        assert store.list_all() == []

    def test_list_multiple(self, store):
        store.save("config-a", SAMPLE_CONFIG)
        cfg_b = dict(SAMPLE_CONFIG)
        cfg_b["rate_plan"] = "E-ELEC"
        store.save("config-b", cfg_b)
        items = store.list_all()
        assert len(items) == 2
        ids = {item["config_id"] for item in items}
        assert ids == {"config-a", "config-b"}


# ── SystemConfig serialization ───────────────────────────────────────


class TestSystemConfigSerialization:
    def test_round_trip(self):
        cfg = SystemConfig.from_dict(SAMPLE_CONFIG)
        d = cfg.to_dict()
        cfg2 = SystemConfig.from_dict(d)
        assert cfg2.rate_plan == "EV2-A"
        assert len(cfg2.arrays) == 1
        assert isinstance(cfg2.arrays[0], SolarArray)
        assert cfg2.arrays[0].name == "Array 1"

    def test_from_dict_accepts_type_alias(self):
        """Reference config uses 'type' not 'inverter_type' for arrays."""
        cfg = SystemConfig.from_dict(SAMPLE_CONFIG)
        assert cfg.arrays[0].inverter_type == "micro"

    def test_from_dict_battery_type_alias(self):
        """Reference config uses 'type' not 'battery_type' for batteries."""
        cfg = SystemConfig.from_dict(SAMPLE_CONFIG)
        assert cfg.batteries[0].battery_type == "Powerwall 2"

    def test_from_dict_auto_computes_dc_ac_watts(self):
        cfg = SystemConfig.from_dict(SAMPLE_CONFIG)
        arr = cfg.arrays[0]
        assert arr.dc_watts == 8 * 385  # panels * panel_watts
        assert arr.ac_watts == 8 * 366  # panels * inverter_watts_ac (micro)

    def test_invalid_rate_plan_raises(self):
        bad = dict(SAMPLE_CONFIG, rate_plan="INVALID")
        with pytest.raises(ValueError, match="Invalid rate_plan"):
            SystemConfig.from_dict(bad)

    def test_invalid_nem_version_raises(self):
        bad = dict(SAMPLE_CONFIG, nem_version="NEM4")
        with pytest.raises(ValueError, match="Invalid nem_version"):
            SystemConfig.from_dict(bad)

    def test_invalid_income_tier_raises(self):
        bad = dict(SAMPLE_CONFIG, income_tier=5)
        with pytest.raises(ValueError, match="Invalid income_tier"):
            SystemConfig.from_dict(bad)

    def test_missing_array_name_raises(self):
        bad = dict(SAMPLE_CONFIG)
        bad["arrays"] = [{"panels": 8, "panel_watts": 385}]
        with pytest.raises(ValueError, match="Invalid array"):
            SystemConfig.from_dict(bad)

    def test_total_properties(self):
        cfg = SystemConfig.from_dict(SAMPLE_CONFIG)
        assert cfg.total_dc_watts == 3080
        assert cfg.total_ac_watts == 2928
        assert cfg.total_battery_kwh == 13.5


# ── Config integration with tools (unit-level) ──────────────────────


class TestConfigLoadHelper:
    """Test the _load_config pattern used by tools (without importing server.py
    which requires fastmcp)."""

    @staticmethod
    def _load_config(config_id: str) -> dict:
        """Replicate server._load_config using the module singleton."""
        from src.storage.config_store import get_store
        store = get_store()
        result = store.get(config_id)
        if result is None:
            raise ValueError(f"Config '{config_id}' not found")
        return result["config"]

    def test_load_existing(self, populated_store):
        import src.storage.config_store as mod
        old = mod._store
        mod._store = populated_store
        try:
            cfg = self._load_config("test-home")
            assert cfg["rate_plan"] == "EV2-A"
        finally:
            mod._store = old

    def test_load_missing_raises(self, store):
        import src.storage.config_store as mod
        old = mod._store
        mod._store = store
        try:
            with pytest.raises(ValueError, match="not found"):
                self._load_config("nonexistent")
        finally:
            mod._store = old
