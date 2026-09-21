from __future__ import annotations

from pathlib import Path
import unittest

import yaml
from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIRECTORY = PROJECT_ROOT / "_assets"


def _load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


class PluginAssetTests(unittest.TestCase):
    def test_manifest_icon_is_registered_by_its_asset_filename(self):
        manifest = _load_yaml("manifest.yaml")
        icon = manifest["icon"]
        self.assertEqual(Path(icon).name, icon)
        self.assertTrue((ASSET_DIRECTORY / icon).is_file())

    def test_provider_icon_is_registered_by_its_asset_filename(self):
        provider = _load_yaml("provider/autobangumi_readonly.yaml")
        icon = provider["identity"]["icon"]
        self.assertEqual(Path(icon).name, icon)
        self.assertTrue((ASSET_DIRECTORY / icon).is_file())

    def test_sdk_registers_the_manifest_icon_under_the_same_filename(self):
        registration = PluginRegistration(DifyPluginEnv(INSTALL_METHOD="local"))
        registered_names = {asset.filename for asset in registration.files}
        self.assertEqual(registration.configuration.icon, "autobangumi-readonly.svg")
        self.assertIn(registration.configuration.icon, registered_names)

    def test_sdk_registers_all_read_only_tools(self):
        registration = PluginRegistration(DifyPluginEnv(INSTALL_METHOD="local"))
        tools = registration.tools_configuration[0].tools
        self.assertEqual(
            {tool.identity.name for tool in tools},
            {
                "get_ab_status",
                "list_ab_subscriptions",
                "get_ab_rss_records",
                "list_ab_bangumi_rules",
                "search_mikan_anime",
                "list_mikan_rss_candidates",
                "inspect_mikan_rss_candidate",
                "prepare_ab_subscription",
                "subscribe_ab_rss",
                "search_mikan_rss",
            },
        )


if __name__ == "__main__":
    unittest.main()
