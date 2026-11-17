# -*- coding: utf-8 -*-
"""
HID driver thread: find the keyboard, authenticate, keep it alive and pass input
reports to the dispatcher. It waits on a blocking read with a short timeout, so it
uses next to no CPU while idle.

Device access goes through hidraw rather than hidapi (hidraw.py explains why).
Anything that can stall on the USB control pipe (keep-alive handshake, LED and
jog-mode writes) runs on a separate control thread, so a stall never holds up key
and dial input.

If the keyboard is missing (unplugged, or held by Resolve) or a read or handshake
fails, the driver reports it and retries. A failed keep-alive is retried on the
device that is already open.

Resolve: the process check is cached for ~0.4 s, but an uncached check runs right
before every write to the device (first handshake and keep-alive), so the driver
never races Resolve's own authentication.

Every phase is logged to %APPDATA%/OutsideResolve/driver.log and mirrored to
.last_status / .is_connected, which the GUI polls.
"""

import os
import struct
import threading
import time
import traceback

from PySide6.QtCore import QThread

from . import auth, foreground, protocol
from .config import config_dir

try:
    from . import hidraw
except Exception:  # pragma: no cover - non-Windows
    hidraw = None

_MAX_LOG_BYTES = 1024 * 1024
_RETRY_MIN = 0.3         # seconds before reconnecting after an error; doubles up to _RETRY_MAX
_RETRY_MAX = 2.0
_KEEPALIVE_RETRY = 2.0   # seconds between attempts after a failed keep-alive
_CONTROL_JOIN = 25.0     # longest a handshake can stall (4 calls x 5 s) plus some margin


def _log_path():
    return os.path.join(config_dir(), "driver.log")


def _rotate_log():
    path = _log_path()
    try:
        if os.path.getsize(path) > _MAX_LOG_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


class Driver(QThread):
    def __init__(self, dispatcher, bus, config):
        super().__init__()
        self.dispatcher = dispatcher
        self.bus = bus
        self.config = config
        self._running = True
        self._link_up = False
        # Polled by the GUI (signal-independent fallback)
        self.last_status = "Starting..."
        self.is_connected = False
        self._last_logged = None
        self._last_unhandled = None
        self._pause_checked_at = 0.0
        self._pause_cached = False

    def stop(self):
        self._running = False

    # ---- logging / status -------------------------------------------------
    def _log(self, msg):
        self.last_status = msg
        if msg != self._last_logged:           # collapse consecutive duplicates
            self._last_logged = msg
            line = time.strftime("%Y-%m-%d %H:%M:%S  ") + msg
            try:
                with open(_log_path(), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
            try:
                print(line, flush=True)
            except Exception:
                pass
        try:
            self.bus.status.emit(msg)
        except Exception:
            pass

    def _set_connected(self, ok):
        self.is_connected = ok
        try:
            self.bus.connected.emit(ok)
        except Exception:
            pass

    # ---- main loop --------------------------------------------------------
    def run(self):
        _rotate_log()
        self._log("Driver thread started. Log: %s" % _log_path())
        try:
            self._supervise()
        except Exception:
            self._log("FATAL driver exception:\n" + traceback.format_exc())
        finally:
            self._log("Driver thread exiting.")

    def _supervise(self):
        if hidraw is None:
            self._log("Windows only: can't talk to the keyboard on this system.")
            return
        retry = _RETRY_MIN
        while self._running:
            if self._should_pause():
                self._set_connected(False)
                self._log("Paused - DaVinci Resolve is active; keyboard works in Resolve.")
                self._sleep(0.5)
                continue
            dev = None
            try:
                path = hidraw.find_control_path()
                if not path:
                    self._set_connected(False)
                    if hidraw.keyboard_paths():
                        self._log("Keyboard is plugged in but its control interface isn't "
                                  "available (Resolve may be holding it). Waiting...")
                    else:
                        self._log("Keyboard not found. Waiting for it to be plugged in...")
                    self._sleep(0.4)
                    continue

                self._log("Connecting...")
                dev = hidraw.HidDevice(path)

                # Last-moment fresh check: if Resolve appeared while we were
                # enumerating/opening, back off BEFORE touching the handshake.
                if self._should_pause(fresh=True):
                    self._log("Resolve appeared mid-connect - standing down "
                              "before the handshake.")
                    continue  # finally-block closes the device

                timeout = auth.authenticate(dev)
                self._kick_jog_mode(dev)
                self._set_connected(True)
                self._log("Connected & authenticated OK. Dial + special keys are "
                          "live. (re-auth every ~%ss)" % self._interval(timeout))
                retry = _RETRY_MIN
                self._read_loop(dev, timeout)

            except Exception as e:
                self._set_connected(False)
                self._log("Driver error, retrying: %s" % e)
                self._sleep(retry)
                retry = min(retry * 2, _RETRY_MAX)
            finally:
                self.dispatcher.release_all()  # never leave a key stuck down
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass

    # ---- helpers ----------------------------------------------------------
    def _kick_jog_mode(self, dev):
        # Output report 3: set jog mode 0 (relative) so the dial reports deltas.
        try:
            dev.write(struct.pack("<BBIB", 3, 0, 0, 0xFF))
        except Exception as e:
            self._log("Jog-mode kick not accepted (ignored): %s" % e)

    # Mode LEDs (best-effort): Speed-Editor jog-LED report is Output Report ID 4
    # with an 8-bit bitfield JOG=1, SHTL=2, SCRL=4. The big keyboard likely
    # matches; a rejected write is harmless.
    _MODE_LED_BITS = {"JOG": 0x01, "SHTL": 0x02, "SCRL": 0x04}

    def _update_leds(self, dev, mode=None):
        if mode is None:
            mode = self.dispatcher.mode()
        try:
            dev.write(struct.pack("<BB", 4, self._MODE_LED_BITS.get(mode, 0)))
        except Exception:
            pass

    def _log_unhandled(self, data):
        # Help identify new reports (e.g. the dial press) without spamming.
        h = data.hex()
        if h == self._last_unhandled:
            return
        self._last_unhandled = h
        try:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + "unhandled report: "
                        + " ".join("%02x" % b for b in data) + "\n")
        except Exception:
            pass

    def _should_pause(self, fresh=False):
        # Cached every ~0.4 s so the (slightly heavier) process scan stays light.
        # fresh=True bypasses the cache: used immediately before any write to the
        # device (auth handshake, keep-alive), where a stale "not paused" answer
        # could race Resolve's own handshake and corrupt the keyboard's state.
        now = time.monotonic()
        if not fresh and now - self._pause_checked_at < 0.4:
            return self._pause_cached
        self._pause_checked_at = now
        apps = [str(a).lower() for a in (self.config.get_setting("pause_apps", ["resolve.exe"]) or [])]
        paused = False
        if apps:
            # Resolve owns the keyboard whenever it RUNS, so stand down on process
            # presence (fast, avoids the auth tug-of-war). Focus mode is a fallback.
            if self.config.get_setting("pause_when_resolve_running", True) and foreground.is_any_running(apps):
                paused = True
            elif self.config.get_setting("pause_when_resolve_active", True):
                fg = foreground.foreground_process_name()
                paused = bool(fg) and fg in apps
        self._pause_cached = paused
        return paused

    def _read_loop(self, dev, timeout):
        self._link_up = True
        control = threading.Thread(target=self._control_loop, args=(dev, timeout),
                                   name="hid-control", daemon=True)
        control.start()
        try:
            self._pump_input(dev)
        finally:
            self._link_up = False
            # The control thread must finish before the caller closes the device.
            # When quitting, don't wait on a stall; the process is going away anyway.
            control.join(_CONTROL_JOIN if self._running else 0.5)

    def _pump_input(self, dev):
        while self._running and self._link_up:
            if self._should_pause():
                self._log("Pausing - DaVinci Resolve came into focus.")
                return
            data = dev.read(200)  # block up to 200 ms -> low CPU
            if data:
                rid = data[0]
                if rid == protocol.REPORT_JOG:
                    mode_byte, value = protocol.parse_jog(data)
                    if value:
                        self.dispatcher.handle_jog(mode_byte, value)
                elif rid == protocol.REPORT_KEYS:
                    self.dispatcher.handle_keys(protocol.parse_keys(data))
                else:
                    self._log_unhandled(data)

    def _control_loop(self, dev, timeout):
        reauth_at = time.monotonic() + self._interval(timeout)
        led_mode = None
        while self._running and self._link_up:
            m = self.dispatcher.mode()      # keep the mode LED in sync
            if m != led_mode:
                self._update_leds(dev, m)
                led_mode = m

            if time.monotonic() >= reauth_at:
                # Fresh (uncached) check right before writing to the device:
                # never run the keep-alive handshake once Resolve exists.
                if self._should_pause(fresh=True):
                    self._log("Pausing - Resolve detected just before keep-alive.")
                    self._link_up = False
                    return
                try:
                    timeout = auth.authenticate(dev) or timeout
                except Exception as e:
                    self._log("Keep-alive failed, retrying: %s" % e)
                    reauth_at = time.monotonic() + _KEEPALIVE_RETRY
                else:
                    self._kick_jog_mode(dev)
                    self._update_leds(dev, led_mode)
                    self._log("Re-authenticated (keep-alive).")
                    reauth_at = time.monotonic() + self._interval(timeout)
            time.sleep(0.05)

    def _interval(self, timeout):
        manual = int(self.config.get_setting("reauth_seconds", 0) or 0)
        if manual > 0:
            return manual
        return max(30, int((timeout or 600) * 0.5))

    def _sleep(self, seconds):
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(0.05)
