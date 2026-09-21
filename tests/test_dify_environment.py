from __future__ import annotations

import unittest

from dify_plugin import DifyPluginEnv


class DifyEnvironmentTests(unittest.TestCase):
    def test_sdk_is_configured_to_read_utf8_dotenv_from_the_working_directory(self):
        self.assertEqual(DifyPluginEnv.model_config["env_file"], ".env")
        self.assertEqual(DifyPluginEnv.model_config["env_file_encoding"], "utf-8")
