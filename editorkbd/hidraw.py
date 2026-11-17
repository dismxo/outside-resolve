# -*- coding: utf-8 -*-
"""
Direct Win32 access to the keyboard's HID control interface.

This replaces hidapi on purpose. hidapi asks each interface for its USB product
string during both enumerate() and open(). This keyboard never answers that
request: every call hangs for exactly 5 s and blocks all other control traffic
while it waits. So hidapi took ~15 s to enumerate and 5+ s to open, and a handshake
running during such a stall fails. Measured on the device:
    hid.enumerate(): 15.07 s      PnP list + open here: < 1 ms      handshake: 40 ms

Nothing here requests a string descriptor. Finding the interface only uses the PnP
list and the cached HID descriptor, so it does no USB I/O and is cheap to poll.

Two handles, so input never waits on a stalled control request:
  * a synchronous one for feature reports (the HID class driver times these out at 5 s)
  * an overlapped one for reading input reports and writing output reports
"""

import ctypes
from ctypes import wintypes

from . import protocol

_hid = ctypes.WinDLL("hid.dll", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_cfg = ctypes.WinDLL("cfgmgr32.dll")

_GENERIC_RW = 0x80000000 | 0x40000000
_FILE_SHARE_RW = 0x00000001 | 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_ERROR_IO_PENDING = 997
_WAIT_TIMEOUT = 258
_WRITE_TIMEOUT_MS = 1000
_CANCEL_WAIT_MS = 1000
_HIDP_STATUS_SUCCESS = 0x00110000


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("NumberFields", wintypes.USHORT * 10)]


_GUID_DEVINTERFACE_HID = _GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))

_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.ResetEvent.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
_k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
_k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
                                     ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
_k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
for _name in ("HidD_SetFeature", "HidD_GetFeature"):
    getattr(_hid, _name).argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
    getattr(_hid, _name).restype = wintypes.BOOLEAN
_hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
_hid.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
_hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
_hid.HidP_GetCaps.restype = ctypes.c_long


def _last_error(what):
    err = ctypes.get_last_error()
    return OSError(err, "%s failed: %s" % (what, ctypes.FormatError(err).strip()), None, err)


def _open(path, access, flags=0):
    h = _k32.CreateFileW(path, access, _FILE_SHARE_RW, None, _OPEN_EXISTING, flags, None)
    if h in (None, _INVALID_HANDLE):
        raise _last_error("open")
    return h


def _caps(handle):
    prep = ctypes.c_void_p()
    if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(prep)):
        raise _last_error("HidD_GetPreparsedData")
    try:
        caps = _HIDP_CAPS()
        if _hid.HidP_GetCaps(prep, ctypes.byref(caps)) != _HIDP_STATUS_SUCCESS:
            raise OSError("HidP_GetCaps failed")
        return caps
    finally:
        _hid.HidD_FreePreparsedData(prep)


def _hid_paths():
    size = wintypes.ULONG(0)
    guid = ctypes.byref(_GUID_DEVINTERFACE_HID)
    if _cfg.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), guid, None, 0) != 0:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if _cfg.CM_Get_Device_Interface_ListW(guid, None, buf, size.value, 0) != 0:
        return []
    return [p for p in ctypes.wstring_at(buf, size.value).split("\0") if p]


def keyboard_paths():
    """HID interface paths of any known Blackmagic keyboard that is plugged in."""
    vid = "vid_%04x" % protocol.VID
    pids = ["pid_%04x" % p for p in protocol.KNOWN_PIDS]
    out = []
    for path in _hid_paths():
        low = path.lower()
        if vid in low and any(p in low for p in pids):
            out.append(path)
    return out


def find_control_path():
    """Path of the vendor/control interface (usage page >= 0xff00), or None."""
    for path in keyboard_paths():
        try:
            h = _open(path, 0)  # no access rights: query only, works even if it's held open
        except OSError:
            continue
        try:
            if _caps(h).UsagePage >= 0xFF00:
                return path
        except OSError:
            pass
        finally:
            _k32.CloseHandle(h)
    return None


class HidDevice:
    """Same call shapes auth.py and the driver expect, on the two handles above."""

    def __init__(self, path):
        self._ctl = _open(path, _GENERIC_RW)
        self._io = None
        self._read_event = None
        self._write_event = None
        self._reading = False
        try:
            self._io = _open(path, _GENERIC_RW, _FILE_FLAG_OVERLAPPED)
            caps = _caps(self._ctl)
            self._in_len = caps.InputReportByteLength
            self._out_len = caps.OutputReportByteLength
            self._feat_len = caps.FeatureReportByteLength
            self._read_event = _k32.CreateEventW(None, True, False, None)
            self._write_event = _k32.CreateEventW(None, True, False, None)
            self._read_ov = _OVERLAPPED(hEvent=self._read_event)
            self._read_buf = ctypes.create_string_buffer(self._in_len)
        except Exception:
            self.close()
            raise

    # ---- feature reports (control pipe, may stall up to 5 s) -----------------
    def send_feature_report(self, data):
        buf = ctypes.create_string_buffer(bytes(data).ljust(self._feat_len, b"\0"), self._feat_len)
        if not _hid.HidD_SetFeature(self._ctl, buf, self._feat_len):
            raise _last_error("set feature report")

    def get_feature_report(self, report_id, length):
        buf = ctypes.create_string_buffer(length)
        buf[0] = bytes([report_id])
        if not _hid.HidD_GetFeature(self._ctl, buf, length):
            raise _last_error("get feature report")
        return buf.raw

    # ---- input reports -------------------------------------------------------
    def read(self, timeout_ms):
        """One input report, or b"" if none arrived within timeout_ms."""
        if not self._reading:
            _k32.ResetEvent(self._read_event)
            ok = _k32.ReadFile(self._io, self._read_buf, self._in_len, None,
                               ctypes.byref(self._read_ov))
            if not ok and ctypes.get_last_error() != _ERROR_IO_PENDING:
                raise _last_error("read")
            self._reading = True
        if _k32.WaitForSingleObject(self._read_event, timeout_ms) == _WAIT_TIMEOUT:
            return b""
        self._reading = False
        got = wintypes.DWORD(0)
        if not _k32.GetOverlappedResult(self._io, ctypes.byref(self._read_ov),
                                        ctypes.byref(got), False):
            raise _last_error("read")
        return self._read_buf.raw[:got.value]

    # ---- output reports ------------------------------------------------------
    def write(self, data):
        buf = ctypes.create_string_buffer(bytes(data).ljust(self._out_len, b"\0"), self._out_len)
        ov = _OVERLAPPED(hEvent=self._write_event)
        _k32.ResetEvent(self._write_event)
        ok = _k32.WriteFile(self._io, buf, self._out_len, None, ctypes.byref(ov))
        if not ok and ctypes.get_last_error() != _ERROR_IO_PENDING:
            raise _last_error("write")
        if _k32.WaitForSingleObject(self._write_event, _WRITE_TIMEOUT_MS) == _WAIT_TIMEOUT:
            _k32.CancelIoEx(self._io, ctypes.byref(ov))
            _k32.WaitForSingleObject(self._write_event, _CANCEL_WAIT_MS)  # ov/buf must outlive the I/O
            raise TimeoutError("write timed out")
        got = wintypes.DWORD(0)
        if not _k32.GetOverlappedResult(self._io, ctypes.byref(ov), ctypes.byref(got), False):
            raise _last_error("write")

    def close(self):
        if self._reading:
            _k32.CancelIoEx(self._io, ctypes.byref(self._read_ov))
            _k32.WaitForSingleObject(self._read_event, _CANCEL_WAIT_MS)
            self._reading = False
        for attr in ("_io", "_ctl", "_read_event", "_write_event"):
            h = getattr(self, attr)
            if h:
                _k32.CloseHandle(h)
                setattr(self, attr, None)
