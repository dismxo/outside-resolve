# -*- coding: utf-8 -*-
"""Run with:  python -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from editorkbd import config as config_mod
from editorkbd import dispatcher as dispatcher_mod
from editorkbd import protocol

F1 = 0x1F
F5 = 0x23
ALT = 0x99  # any non F-row vendor code


class FakeConfig:
    def __init__(self, fkeys=True, mappings=None):
        self.values = {"fkeys_as_function": fkeys}
        self._mappings = mappings or {}

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def settings(self):
        return dict(self.values)

    def mappings(self):
        return dict(self._mappings)


class FKeyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for name, tag in (("key_down", "down"), ("key_up", "up")):
            p = mock.patch.object(
                dispatcher_mod.macros, name,
                side_effect=lambda k, tag=tag: self.calls.append((tag, k)))
            p.start()
            self.addCleanup(p.stop)
        self.runner = mock.Mock()
        self.make()

    def make(self, **kw):
        self.d = dispatcher_mod.Dispatcher(FakeConfig(**kw), mock.Mock(), self.runner)

    def test_press_and_release_follow_the_physical_key(self):
        self.d.handle_keys({F5})
        self.assertEqual(self.calls, [("down", "f5")])
        self.d.handle_keys(set())
        self.assertEqual(self.calls, [("down", "f5"), ("up", "f5")])

    def test_two_keys_overlap_like_a_real_keyboard(self):
        self.d.handle_keys({F1})
        self.d.handle_keys({F1, F5})
        self.d.handle_keys({F5})
        self.d.handle_keys(set())
        self.assertEqual(self.calls, [
            ("down", "f1"), ("down", "f5"), ("up", "f1"), ("up", "f5")])

    def test_a_held_key_is_released_when_the_link_drops(self):
        self.d.handle_keys({F5})
        self.d.release_all()
        self.assertEqual(self.calls, [("down", "f5"), ("up", "f5")])
        self.d.handle_keys({F5})  # still physically held after reconnect = fresh press
        self.assertEqual(self.calls[-1], ("down", "f5"))

    def test_setting_off_sends_nothing(self):
        self.make(fkeys=False)
        self.d.handle_keys({F5})
        self.d.handle_keys(set())
        self.assertEqual(self.calls, [])

    def test_a_user_macro_on_the_key_wins(self):
        action = {"type": "hotkey", "value": "ctrl+c"}
        self.make(mappings={F5: action})
        self.d.handle_keys({F5})
        self.d.handle_keys(set())
        self.assertEqual(self.calls, [])
        self.runner.run.assert_called_once_with(action)

    def test_capture_swallows_the_key(self):
        self.d.set_capture(True)
        self.d.handle_keys({F5})
        self.d.handle_keys(set())
        self.assertEqual(self.calls, [])

    def test_whole_row_maps_to_f1_f12(self):
        self.assertEqual(
            [protocol.FKEY_CODES[0x1F + i] for i in range(12)],
            ["f%d" % (i + 1) for i in range(12)])


class ConfigMigrationTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            cfg = config_mod.Config(path)
            with open(path, encoding="utf-8") as f:
                return cfg, json.load(f)

    def v6(self, **settings):
        return {
            "version": 6, "active_profile": "Default", "settings": settings,
            "profiles": {"Default": {"mappings": {"23": {"type": "hotkey", "value": "NumLock"}}}},
        }

    def test_v6_turns_fkeys_on_once_and_keeps_everything_else(self):
        cfg, saved = self.load(self.v6(
            fkeys_as_function=False, zoom_speed=9, pause_when_resolve_running=False))
        self.assertTrue(cfg.get_setting("fkeys_as_function"))
        self.assertEqual(cfg.get_setting("zoom_speed"), 9)
        self.assertTrue(cfg.get_setting("pause_when_resolve_running"))
        self.assertIn(23, cfg.mappings())
        self.assertEqual(saved["version"], config_mod.CONFIG_VERSION)

    def test_turning_settings_off_after_migration_sticks(self):
        data = self.v6(fkeys_as_function=False, pause_when_resolve_running=False)
        data["version"] = config_mod.CONFIG_VERSION
        cfg, _ = self.load(data)
        self.assertFalse(cfg.get_setting("fkeys_as_function"))
        self.assertFalse(cfg.get_setting("pause_when_resolve_running"))


if __name__ == "__main__":
    unittest.main()
