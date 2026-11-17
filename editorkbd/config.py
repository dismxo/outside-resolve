# -*- coding: utf-8 -*-
"""
Persistent configuration: profiles, per-key macro mappings and tuning settings.
Stored as JSON at %APPDATA%/OutsideResolve/config.json. Thread-safe.

Dial tuning is expressed as speeds from 1 to 11 (6 = default, higher = faster):
    volume_speed  - JOG  (volume)
    scroll_speed  - SCRL (mouse wheel)
    zoom_speed    - SHTL (Ctrl + wheel)
"""

import copy
import json
import os
import shutil
import threading

APP_NAME = "OutsideResolve"
LEGACY_NAME = "EditorKeyboard"   # folder used before the rename
CONFIG_VERSION = 8

DEFAULT_SETTINGS = {
    "volume_speed": 6,      # JOG: volume responsiveness (higher = faster)
    "volume_invert": False,
    "scroll_speed": 6,      # SCRL: scroll responsiveness (higher = faster)
    "scroll_invert": False,
    "zoom_speed": 6,        # SHTL: zoom responsiveness (higher = faster)
    "zoom_invert": False,
    "reauth_seconds": 0,    # 0 = auto (half the keyboard timeout)
    "start_minimized": False,
    "fkeys_as_function": True,  # F-row sends real F1..F12 (codes 0x1f-0x2a)
    "pause_when_resolve_active": True,  # release while Resolve is the focused window
    "pause_when_resolve_running": True, # stand down whenever Resolve is running (recommended)
    "pause_apps": ["resolve.exe"],     # process names that trigger the pause
    "knob_code": None,      # keycode the dial-press sends (set via the GUI)
}

# Reset these to defaults when migrating from before v6 (the speed scale changed).
_MIGRATE_KEYS = ("volume_speed", "scroll_speed", "zoom_speed")


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    if not os.path.isdir(d):
        legacy = os.path.join(base, LEGACY_NAME)
        if os.path.isfile(os.path.join(legacy, "config.json")):
            try:
                os.makedirs(d, exist_ok=True)
                shutil.copy2(os.path.join(legacy, "config.json"), os.path.join(d, "config.json"))
            except OSError:
                pass
    os.makedirs(d, exist_ok=True)
    return d


def config_path():
    return os.path.join(config_dir(), "config.json")


def default_config():
    return {
        "version": CONFIG_VERSION,
        "active_profile": "Default",
        "settings": dict(DEFAULT_SETTINGS),
        "profiles": {"Default": {"mappings": {}}},
    }


class Config:
    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._path = path or config_path()
        self.data = default_config()
        self.load()

    # ---- persistence -------------------------------------------------------
    def load(self):
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                loaded = None
            if not isinstance(loaded, dict):
                self.data = default_config()
                self.save()
                return self.data
            old_version = loaded.get("version", 1)
            self.data = self._merge_defaults(loaded)
            if old_version != CONFIG_VERSION:
                self.save()
        return self.data

    def save(self):
        with self._lock:
            tmp = self._path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, self._path)
            except OSError:
                pass

    @staticmethod
    def _merge_defaults(loaded):
        base = default_config()
        for k, v in loaded.items():
            if k != "settings":
                base[k] = v
        merged = dict(DEFAULT_SETTINGS)
        merged.update(loaded.get("settings", {}) or {})
        old_version = loaded.get("version", 1)
        if old_version < CONFIG_VERSION:                # drop dead keys
            merged = {k: merged.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS}
        if old_version < 6:                             # reset speeds
            for k in _MIGRATE_KEYS:
                merged[k] = DEFAULT_SETTINGS[k]
        if old_version < 7:                             # F-row as real F-keys is now the norm
            merged["fkeys_as_function"] = True
        if old_version < 8:                             # Resolve hides the control interface
            merged["pause_when_resolve_running"] = True  # while it runs: nothing to lose
        base["settings"] = merged
        base["version"] = CONFIG_VERSION
        if not base.get("profiles"):
            base["profiles"] = {"Default": {"mappings": {}}}
        if base.get("active_profile") not in base["profiles"]:
            base["active_profile"] = next(iter(base["profiles"]))
        return base

    # ---- settings ----------------------------------------------------------
    def settings(self):
        with self._lock:
            return dict(self.data["settings"])

    def get_setting(self, key, default=None):
        with self._lock:
            return self.data["settings"].get(key, default)

    def set_setting(self, key, value):
        with self._lock:
            self.data["settings"][key] = value
            self.save()

    # ---- profiles ----------------------------------------------------------
    def profile_names(self):
        with self._lock:
            return list(self.data["profiles"].keys())

    def active_profile(self):
        with self._lock:
            return self.data["active_profile"]

    def set_active_profile(self, name):
        with self._lock:
            if name in self.data["profiles"]:
                self.data["active_profile"] = name
                self.save()

    def add_profile(self, name):
        with self._lock:
            if name and name not in self.data["profiles"]:
                self.data["profiles"][name] = {"mappings": {}}
                self.save()

    def remove_profile(self, name):
        with self._lock:
            if name in self.data["profiles"] and len(self.data["profiles"]) > 1:
                del self.data["profiles"][name]
                if self.data["active_profile"] == name:
                    self.data["active_profile"] = next(iter(self.data["profiles"]))
                self.save()

    def rename_profile(self, old, new):
        with self._lock:
            if old in self.data["profiles"] and new and new not in self.data["profiles"]:
                self.data["profiles"][new] = self.data["profiles"].pop(old)
                if self.data["active_profile"] == old:
                    self.data["active_profile"] = new
                self.save()

    # ---- mappings (active profile) -----------------------------------------
    def mappings(self):
        with self._lock:
            prof = self.data["profiles"][self.data["active_profile"]]
            out = {}
            for k, v in prof.get("mappings", {}).items():
                try:
                    out[int(k)] = dict(v)
                except (ValueError, TypeError):
                    continue
            return out

    def set_mapping(self, code, action):
        with self._lock:
            prof = self.data["profiles"][self.data["active_profile"]]
            prof.setdefault("mappings", {})[str(int(code))] = dict(action)
            self.save()

    def clear_mapping(self, code):
        with self._lock:
            prof = self.data["profiles"][self.data["active_profile"]]
            prof.get("mappings", {}).pop(str(int(code)), None)
            self.save()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.data)
