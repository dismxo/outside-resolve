# -*- coding: utf-8 -*-
"""
Master volume through the Windows volume keys, which is what brings up the Windows
volume bar (setting the level through Core Audio changes it without showing the bar).
Each key press is a 2% step, so the dial's 1% ticks are collected until they add up.
"""

from . import macros

KEY_STEP = 0.02


class VolumeKeys:
    def __init__(self, press=macros.media_volume):
        self._press = press
        self._pending = 0.0

    def add(self, delta):
        self._pending += delta
        steps = int(round(self._pending / KEY_STEP, 6))
        if steps:
            self._pending -= steps * KEY_STEP
            self._press(steps)
