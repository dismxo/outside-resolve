# -*- coding: utf-8 -*-
"""
Macro execution + dial output helpers (keystroke / mouse synthesis via pynput).

Macros run on a single background worker thread (a serialized queue) so they never
stall the HID read loop and never overlap each other. The dial helpers
(scroll/zoom) are cheap and are called inline from the driver thread.

Action dict shape:
    {"type": "hotkey", "value": "ctrl+shift+t", "label": "Reopen tab"}
    {"type": "text",   "value": "your@email.com"}
    {"type": "launch", "value": "C:\\\\Program Files\\\\App\\\\app.exe"}   # or a URL / document
    {"type": "shell",  "value": "wt -w 0 nt"}                              # run via the shell
"""

import ctypes
import os
import queue
import subprocess
import threading
import time

try:
    from pynput.keyboard import Controller as _KbController, Key
    from pynput.mouse import Controller as _MouseController
    _AVAILABLE = True
except Exception as _e:  # pragma: no cover - dependency missing
    _AVAILABLE = False
    _IMPORT_ERROR = _e
    Key = None

_kb = _KbController() if _AVAILABLE else None
_mouse = _MouseController() if _AVAILABLE else None


# ---------------------------------------------------------------------------
# Hotkey parsing  ("ctrl+alt+del", "win+e", "f5", "ctrl+shift+escape", ...)
# ---------------------------------------------------------------------------
def _build_keymap():
    if not _AVAILABLE:
        return {}
    m = {
        "ctrl": Key.ctrl, "control": Key.ctrl,
        "lctrl": Key.ctrl_l, "rctrl": Key.ctrl_r,
        "alt": Key.alt, "lalt": Key.alt_l, "ralt": Key.alt_r, "altgr": Key.alt_gr,
        "shift": Key.shift, "lshift": Key.shift_l, "rshift": Key.shift_r,
        "win": Key.cmd, "super": Key.cmd, "cmd": Key.cmd, "meta": Key.cmd,
        "enter": Key.enter, "return": Key.enter,
        "tab": Key.tab, "esc": Key.esc, "escape": Key.esc,
        "space": Key.space, "spacebar": Key.space,
        "backspace": Key.backspace, "bksp": Key.backspace,
        "delete": Key.delete, "del": Key.delete, "insert": Key.insert, "ins": Key.insert,
        "home": Key.home, "end": Key.end,
        "pageup": Key.page_up, "pgup": Key.page_up,
        "pagedown": Key.page_down, "pgdn": Key.page_down,
        "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
        "capslock": Key.caps_lock, "numlock": Key.num_lock, "scrolllock": Key.scroll_lock,
        "printscreen": Key.print_screen, "prtsc": Key.print_screen,
        "pause": Key.pause, "menu": Key.menu, "apps": Key.menu,
        "volumeup": Key.media_volume_up, "volumedown": Key.media_volume_down,
        "mute": Key.media_volume_mute,
        "playpause": Key.media_play_pause, "play": Key.media_play_pause,
        "nexttrack": Key.media_next, "prevtrack": Key.media_previous,
    }
    for i in range(1, 25):
        name = f"f{i}"
        if hasattr(Key, name):
            m[name] = getattr(Key, name)
    return m


_KEYMAP = _build_keymap()


def _resolve_token(tok):
    """Named key ("ctrl", "f5", "pgup") or the literal character itself."""
    t = tok.strip()
    return _KEYMAP.get(t.lower(), t)


def send_hotkey(combo):
    if not _AVAILABLE or not combo:
        return
    tokens = [t for t in combo.replace(" ", "").split("+") if t]
    keys = [_resolve_token(t) for t in tokens]
    pressed = []
    try:
        for k in keys:
            _kb.press(k)
            pressed.append(k)
        time.sleep(0.01)
    finally:
        for k in reversed(pressed):
            try:
                _kb.release(k)
            except Exception:
                pass


def type_text(text):
    if _AVAILABLE and text:
        _kb.type(text)


def launch(target):
    """Open an app / document / URL with its default handler."""
    if not target:
        return
    try:
        os.startfile(target)  # Windows
    except AttributeError:
        subprocess.Popen(target, shell=True)
    except OSError:
        subprocess.Popen(target, shell=True)


def run_shell(command):
    if command:
        subprocess.Popen(command, shell=True)


# ---------------------------------------------------------------------------
# Dial output helpers (called inline from the driver thread)
# ---------------------------------------------------------------------------
def scroll_vertical(clicks):
    if _AVAILABLE and clicks:
        _mouse.scroll(0, clicks)


def scroll_horizontal(clicks):
    if _AVAILABLE and clicks:
        _mouse.scroll(clicks, 0)


def zoom(clicks):
    """Ctrl + wheel: zoom in/out in most apps."""
    if not _AVAILABLE or not clicks:
        return
    _kb.press(Key.ctrl)
    try:
        _mouse.scroll(0, clicks)
    finally:
        _kb.release(Key.ctrl)


def tap(key_name):
    if _AVAILABLE and key_name in _KEYMAP:
        k = _KEYMAP[key_name]
        _kb.press(k)
        _kb.release(k)


# ---------------------------------------------------------------------------
# Held keys (F-row). Windows only auto-repeats physical keyboards, so synthesized
# keys need their own repeat. Like a real keyboard, only the newest key repeats.
# ---------------------------------------------------------------------------
_repeat_lock = threading.Lock()
_repeat_token = None
_repeat_key = None


def _repeat_timing():
    """(delay, interval) in seconds, from the user's Windows keyboard settings."""
    delay, speed = ctypes.c_int(1), ctypes.c_uint(31)
    try:
        user32 = ctypes.windll.user32
        user32.SystemParametersInfoW(0x0016, 0, 0, ctypes.byref(delay))  # SPI_GETKEYBOARDDELAY
        user32.SystemParametersInfoW(0x000A, 0, 0, ctypes.byref(speed))  # SPI_GETKEYBOARDSPEED
    except Exception:
        pass
    # delay 0..3 = 250..1000 ms; speed 0..31 = about 2.5..30 repeats per second
    return 0.25 * (delay.value + 1), 1.0 / (2.5 + speed.value * 27.5 / 31)


def _repeat_loop(k, token):
    delay, interval = _repeat_timing()
    wait = delay
    while True:
        time.sleep(wait)
        with _repeat_lock:
            if _repeat_token is not token:
                return
            _kb.press(k)
        wait = interval


def key_down(key_name):
    global _repeat_token, _repeat_key
    if not _AVAILABLE or key_name not in _KEYMAP:
        return
    k = _KEYMAP[key_name]
    token = object()
    with _repeat_lock:
        _repeat_token = token
        _repeat_key = key_name
        _kb.press(k)
    threading.Thread(target=_repeat_loop, args=(k, token), name="key-repeat", daemon=True).start()


def key_up(key_name):
    global _repeat_token, _repeat_key
    if not _AVAILABLE or key_name not in _KEYMAP:
        return
    with _repeat_lock:
        if _repeat_key == key_name:
            _repeat_token = None
            _repeat_key = None
        _kb.release(_KEYMAP[key_name])


# ---------------------------------------------------------------------------
# Serialized macro runner
# ---------------------------------------------------------------------------
class ActionRunner:
    def __init__(self, on_error=None):
        self._q = queue.Queue()
        self._run = True
        self._on_error = on_error
        self._t = threading.Thread(target=self._loop, name="macro-runner", daemon=True)
        self._t.start()

    def run(self, action):
        if action:
            self._q.put(action)

    def _loop(self):
        while self._run:
            try:
                action = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._exec(action)
            except Exception as e:  # never let a bad macro kill the thread
                if self._on_error:
                    self._on_error(str(e))

    @staticmethod
    def _exec(action):
        t = (action.get("type") or "").lower()
        v = action.get("value", "")
        if t == "hotkey":
            send_hotkey(v)
        elif t == "text":
            type_text(v)
        elif t == "launch":
            launch(v)
        elif t == "shell":
            run_shell(v)
        elif t == "playpause":
            media_play_pause()
        elif t == "mute":
            media_mute_toggle()

    def stop(self):
        self._run = False


def available():
    return _AVAILABLE


def media_volume(steps):
    """Tap the media volume up/down key |steps| times (sign = direction)."""
    if not _AVAILABLE or not steps:
        return
    k = Key.media_volume_up if steps > 0 else Key.media_volume_down
    for _ in range(min(abs(int(steps)), 12)):
        _kb.press(k)
        _kb.release(k)


def media_play_pause():
    if _AVAILABLE:
        _kb.press(Key.media_play_pause)
        _kb.release(Key.media_play_pause)


def media_mute_toggle():
    if _AVAILABLE:
        _kb.press(Key.media_volume_mute)
        _kb.release(Key.media_volume_mute)
