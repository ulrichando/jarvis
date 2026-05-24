"""PluginContext provider-registration hooks must route into _provider_registry.

Hermes-shaped backend plugins register a provider via
``ctx.register_<cap>_provider(provider)``. In the voice agent those hooks were
no-op stubs; this guards that they now land the provider in the generic
``tools._provider_registry`` under the right capability kind, so the consuming
registry tool (image_generate / video_generate / web_extract / browser_*) can
resolve it. Regression guard for the 2026-05-22 Hermes-plugin port.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class _FakeProvider:
    def __init__(self, name):
        self.name = name

    def is_available(self):
        return True


def _ctx():
    from tools.plugin_system import PluginContext, PluginManager, PluginManifest

    mgr = PluginManager()
    return PluginContext(PluginManifest(name="t"), mgr)


def test_register_web_search_provider_lands_in_registry():
    from tools import _provider_registry as pr

    pr.reset_providers("web")
    _ctx().register_web_search_provider(_FakeProvider("exa"))
    assert pr.get_provider("web", "exa") is not None
    pr.reset_providers("web")


def test_register_video_gen_provider_lands_in_registry():
    from tools import _provider_registry as pr

    pr.reset_providers("video")
    _ctx().register_video_gen_provider(_FakeProvider("fal"))
    assert pr.get_provider("video", "fal") is not None
    pr.reset_providers("video")


def test_register_image_and_browser_providers_land_in_registry():
    from tools import _provider_registry as pr

    pr.reset_providers("image")
    pr.reset_providers("browser")
    _ctx().register_image_gen_provider(_FakeProvider("codex"))
    _ctx().register_browser_provider(_FakeProvider("browserbase"))
    assert pr.get_provider("image", "codex") is not None
    assert pr.get_provider("browser", "browserbase") is not None


def test_provider_without_name_is_skipped_gracefully():
    """A provider lacking a usable name must not raise out of register()."""

    class NoName:
        def is_available(self):
            return True

    # Must not raise — logs a warning and returns.
    _ctx().register_web_search_provider(NoName())
