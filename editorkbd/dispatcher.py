# -*- coding: utf-8 -*-
"""
Event dispatcher: turns decoded HID events into actions.

All three dial modes follow the spin speed: the dial's signed movement is
accumulated and turned into discrete steps once enough travel adds up. The per-mode
"speed" setting (1..11, default 6) scales this: lower is gentler, higher is faster,
about 30% per step.
    JOG  -> volume, 1% per step, sent as 2% key presses   (volume_speed)
    SCRL -> one wheel click per step  (scroll_speed)
    SHTL -> one zoom step per step     (zoom_speed)

Pressing the dial (knob) fires a fixed action by mode:
    JOG  -> mute / unmute toggle
    SCRL / SHTL -> media play / pause
The knob's keycode is learned once with the "Set knob press" button in the GUI.

Special keys (report 0x04): SHTL/JOG/SCRL switch mode (reserved); any other key
runs its mapped macro. Capture mode emits the next key and swallows it.

The F-row (without Fn) reports vendor codes, not real F-keys. With no macro on the
key it acts as a real F-key: down on press, up on release, so it holds, repeats and
combines with Alt/Ctrl/Shift like any other key.

Volume is emitted as bus.volumeDelta and applied on the main thread as Windows volume
keys (2% steps, see volume.py). Scroll and zoom use pynput inline; macros are queued.
"""

import threading

from . import macros, protocol

VOLUME_TICK = 0.01       # one volume step = 1% of full scale
VOLUME_BASE = 16000      # dial units per 1% at the baseline speed (6)
SCROLL_BASE = 12000      # dial units per click at the baseline speed (6)
ZOOM_BASE = 24000        # dial units per step at the baseline speed (6)
VOLUME_CAP = 3           # max volume steps (%) from a single report
SCROLL_CAP = 4           # max wheel clicks from a single report
ZOOM_CAP = 3             # max zoom steps from a single report

DIAL_BASELINE_SPEED = 6  # speed at which unit == BASE (the default)


def _unit(base, speed):
    """Map a 1..11 speed onto a 'dial units per step' value, centered on 6.
    speed 6 -> base; lower -> larger unit (gentler); higher -> smaller (faster)."""
    speed = max(1, int(speed))
    factor = 2.0 ** ((DIAL_BASELINE_SPEED - speed) * 0.4)
    return max(50, int(round(base * factor)))


class Dispatcher:
    def __init__(self, config, bus, runner):
        self.config = config
        self.bus = bus
        self.runner = runner

        self._lock = threading.RLock()
        self._mode = "JOG"
        self._capture = False
        self._held = set()
        self._fkeys_down = {}   # keycode -> F-key name currently held down
        self._acc = {"JOG": 0.0, "SCRL": 0.0, "SHTL": 0.0}

    # ---- state set by the GUI ---------------------------------------------
    def set_capture(self, on):
        with self._lock:
            self._capture = bool(on)

    def capture(self):
        with self._lock:
            return self._capture

    def mode(self):
        with self._lock:
            return self._mode

    def set_mode(self, mode):
        with self._lock:
            if mode == self._mode:
                return
            self._mode = mode
            for k in self._acc:
                self._acc[k] = 0.0
        self.bus.modeChanged.emit(mode)

    def _accumulate(self, key, value, unit, cap):
        with self._lock:
            self._acc[key] += value
            n = int(self._acc[key] / unit)
            self._acc[key] -= n * unit
        if n > cap:
            n = cap
        elif n < -cap:
            n = -cap
        return n

    # ---- events from the driver -------------------------------------------
    def handle_jog(self, mode_byte, value):
        if not value:
            return
        self.bus.dial.emit(value)
        mode = self.mode()
        s = self.config.settings()

        if mode == "JOG":
            n = self._accumulate("JOG", value, _unit(VOLUME_BASE, s.get("volume_speed", 6)), VOLUME_CAP)
            if n:
                inv = -1 if s.get("volume_invert") else 1
                self.bus.volumeDelta.emit(VOLUME_TICK * n * inv)

        elif mode == "SCRL":
            n = self._accumulate("SCRL", value, _unit(SCROLL_BASE, s.get("scroll_speed", 6)), SCROLL_CAP)
            if n:
                inv = 1 if s.get("scroll_invert") else -1   # right turn -> scroll down
                macros.scroll_vertical(n * inv)

        elif mode == "SHTL":
            n = self._accumulate("SHTL", value, _unit(ZOOM_BASE, s.get("zoom_speed", 6)), ZOOM_CAP)
            if n:
                inv = -1 if s.get("zoom_invert") else 1      # right turn -> zoom in
                macros.zoom(n * inv)

    def handle_keys(self, codes):
        with self._lock:
            new = codes - self._held
            gone = self._held - codes
            self._held = set(codes)
        for code in sorted(gone):
            self._on_key_up(code)
        for code in sorted(new):
            self._on_key_down(code)

    def release_all(self):
        """The link dropped: we'll never see these keys' release, so do it now."""
        with self._lock:
            self._held = set()
        for code in list(self._fkeys_down):
            self._on_key_up(code)

    def _on_key_up(self, code):
        self.bus.keyEvent.emit(code, False)
        fk = self._fkeys_down.pop(code, None)
        if fk:
            macros.key_up(fk)

    def _on_knob_press(self):
        if self.mode() == "JOG":
            macros.media_mute_toggle()
        else:
            macros.media_play_pause()

    def _on_key_down(self, code):
        self.bus.keyEvent.emit(code, True)

        if self.capture():
            self.bus.captured.emit(code)
            return  # swallow during capture

        if code in protocol.MODE_KEYS:
            self.set_mode(protocol.MODE_KEYS[code])
            return

        knob = self.config.get_setting("knob_code")
        if knob is not None and code == knob:
            self._on_knob_press()
            return

        action = self.config.mappings().get(code)
        if action:
            self.runner.run(action)
            label = action.get("label") or action.get("type", "")
            self.bus.macroTriggered.emit(code, label)
        elif self.config.get_setting("fkeys_as_function", True) and code in protocol.FKEY_CODES:
            fk = protocol.FKEY_CODES[code]
            self._fkeys_down[code] = fk
            macros.key_down(fk)
            self.bus.macroTriggered.emit(code, fk.upper())
