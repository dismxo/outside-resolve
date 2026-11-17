# -*- coding: utf-8 -*-
"""
Return the executable name of the foreground (active) window's process, so the
driver can step aside while DaVinci Resolve is in focus. Windows-only; on other
platforms it safely returns "" (never raises).
"""

import ctypes

try:
    from ctypes import wintypes
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _OK = True
except Exception:  # pragma: no cover - non-Windows
    _OK = False

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def foreground_process_name():
    """Lower-case basename of the foreground window's exe, e.g. 'resolve.exe'."""
    if not _OK:
        return ""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buf))
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            return ""
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        return ""


def is_any_running(names):
    """True if any running process's exe basename is in `names`. Windows-only;
    returns False elsewhere or on any error (so the caller falls back safely)."""
    if not _OK or not names:
        return False
    wanted = set(str(n).lower() for n in names)
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]
    try:
        _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        _kernel32.Process32First.restype = wintypes.BOOL
        _kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        _kernel32.Process32Next.restype = wintypes.BOOL
        _kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        invalid = ctypes.c_void_p(-1).value
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == invalid:
            return False
        try:
            e = PROCESSENTRY32()
            e.dwSize = ctypes.sizeof(PROCESSENTRY32)
            ok = _kernel32.Process32First(snap, ctypes.byref(e))
            while ok:
                name = bytes(e.szExeFile).split(b"\x00", 1)[0].decode("ascii", "ignore").lower()
                if name in wanted:
                    return True
                ok = _kernel32.Process32Next(snap, ctypes.byref(e))
        finally:
            _kernel32.CloseHandle(snap)
    except Exception:
        return False
    return False
