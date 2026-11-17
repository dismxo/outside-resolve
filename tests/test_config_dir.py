# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from editorkbd import config as config_mod


class ConfigDirTests(unittest.TestCase):
    def test_settings_from_the_old_folder_are_carried_over(self):
        with tempfile.TemporaryDirectory() as appdata:
            legacy = os.path.join(appdata, config_mod.LEGACY_NAME)
            os.makedirs(legacy)
            with open(os.path.join(legacy, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"version": config_mod.CONFIG_VERSION, "settings": {"zoom_speed": 9}}, f)
            with mock.patch.dict(os.environ, {"APPDATA": appdata}):
                cfg = config_mod.Config()
            self.assertEqual(cfg.get_setting("zoom_speed"), 9)
            self.assertTrue(os.path.isfile(
                os.path.join(appdata, config_mod.APP_NAME, "config.json")))

    def test_an_existing_new_folder_is_left_alone(self):
        with tempfile.TemporaryDirectory() as appdata:
            os.makedirs(os.path.join(appdata, config_mod.APP_NAME))
            legacy = os.path.join(appdata, config_mod.LEGACY_NAME)
            os.makedirs(legacy)
            with open(os.path.join(legacy, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"settings": {"zoom_speed": 9}}, f)
            with mock.patch.dict(os.environ, {"APPDATA": appdata}):
                cfg = config_mod.Config()
            self.assertEqual(cfg.get_setting("zoom_speed"), 6)


if __name__ == "__main__":
    unittest.main()
