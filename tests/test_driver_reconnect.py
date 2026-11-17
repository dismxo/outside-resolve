# -*- coding: utf-8 -*-
"""The keyboard disappearing mid-session (unplug, sleep): input stops cleanly and the
driver reconnects by itself. Uses fakes, no hardware."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from editorkbd import driver as driver_mod

GOOD_HANDSHAKE_TIMEOUT = 600
UNPLUGGED = OSError(1167, "device not connected")


class FakeDevice:
    def __init__(self, reads):
        self.reads = list(reads)
        self.closed = False

    def read(self, _timeout):
        item = self.reads.pop(0) if self.reads else b""
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, _data):
        pass

    def close(self):
        self.closed = True


class ReconnectTests(unittest.TestCase):
    def run_driver(self, devices, stop_after_connects):
        bus = mock.Mock()
        config = mock.Mock()
        config.get_setting.side_effect = lambda k, d=None: {"pause_apps": []}.get(k, d)
        self.dispatcher = mock.Mock()
        self.dispatcher.mode.return_value = "JOG"
        drv = driver_mod.Driver(self.dispatcher, bus, config)
        self.connects = 0

        def authenticate(_dev):
            self.connects += 1
            if self.connects >= stop_after_connects:
                drv.stop()
            return GOOD_HANDSHAKE_TIMEOUT

        hidraw = mock.Mock()
        hidraw.find_control_path.return_value = "path"
        hidraw.HidDevice.side_effect = devices
        with mock.patch.object(driver_mod, "hidraw", hidraw), \
                mock.patch.object(driver_mod.auth, "authenticate", authenticate), \
                mock.patch.object(driver_mod, "_log_path", lambda: os.devnull):
            drv._supervise()
        return drv

    def test_unplug_releases_keys_closes_the_device_and_reconnects(self):
        first = FakeDevice([b"", UNPLUGGED])
        second = FakeDevice([])
        self.run_driver([first, second], stop_after_connects=2)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(self.connects, 2)
        self.assertGreaterEqual(self.dispatcher.release_all.call_count, 1)

    def test_a_failed_reconnect_keeps_trying(self):
        good = FakeDevice([])
        self.run_driver([OSError(2, "gone"), OSError(2, "gone"), good], stop_after_connects=1)
        self.assertEqual(self.connects, 1)


if __name__ == "__main__":
    unittest.main()
