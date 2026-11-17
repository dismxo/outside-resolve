# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from editorkbd.volume import VolumeKeys


class VolumeKeysTests(unittest.TestCase):
    def setUp(self):
        self.presses = []
        self.v = VolumeKeys(press=self.presses.append)

    def test_two_one_percent_ticks_make_one_key_press(self):
        self.v.add(0.01)
        self.assertEqual(self.presses, [])
        self.v.add(0.01)
        self.assertEqual(self.presses, [1])

    def test_a_big_tick_presses_several_times_and_keeps_the_rest(self):
        self.v.add(0.03)
        self.assertEqual(self.presses, [1])
        self.v.add(0.01)
        self.assertEqual(self.presses, [1, 1])

    def test_down_works_the_same_way(self):
        self.v.add(-0.02)
        self.assertEqual(self.presses, [-1])

    def test_reversing_direction_cancels_the_leftover(self):
        self.v.add(0.01)
        self.v.add(-0.01)
        self.v.add(-0.01)
        self.assertEqual(self.presses, [])

    def test_a_long_spin_loses_no_steps(self):
        for _ in range(1000):
            self.v.add(0.01)
        self.assertEqual(sum(self.presses), 500)


if __name__ == "__main__":
    unittest.main()
