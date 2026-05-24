import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_all_bundled_plugins_load_without_error():
    from tools.plugin_system import discover_plugins
    m = discover_plugins(force=True)
    broken = [(p["key"], p["error"]) for p in m.list_plugins() if not p["enabled"]]
    assert not broken, f"plugins failed to load: {broken}"


def test_expected_plugin_families_present():
    from tools.plugin_system import discover_plugins
    keys = {p["key"] for p in discover_plugins(force=True).list_plugins()}
    # at least one leaf from each mirrored family + the dashboard flats
    families = {k.split("/")[0] for k in keys}
    for fam in ["context_engine", "model-providers", "platforms", "observability",
                "disk-cleanup", "teams_pipeline", "example-dashboard", "achievements", "kanban"]:
        assert fam in families or fam in keys, f"missing family: {fam} (have {sorted(families)})"
