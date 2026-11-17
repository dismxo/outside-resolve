# -*- coding: utf-8 -*-
"""
"Start with Windows" toggle via the per-user Run registry key
(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run). No admin rights needed.

Works both when frozen into an .exe (PyInstaller) and when run as a script
(launches with pythonw.exe so no console window pops up).
"""

import os
import sys

APP_NAME = "OutsideResolve"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

try:
    import winreg  # Windows only
except ImportError:  # pragma: no cover - non-Windows dev box
    winreg = None


def _launch_command():
    if getattr(sys, "frozen", False):
        # Packaged .exe
        return f'"{sys.executable}" --minimized'
    # Running from source: prefer pythonw.exe (no console)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{exe}" "{main_py}" --minimized'


def is_supported():
    return winreg is not None


def is_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable():
    if winreg is None:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        return True
    except OSError:
        return False


def disable():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def set_enabled(on):
    return enable() if on else disable()
