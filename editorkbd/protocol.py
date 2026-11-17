# -*- coding: utf-8 -*-
"""
HID report layout for the full-size Blackmagic Editor Keyboard (PID 0xDA10).

Decoded from live captures on the device:

  * Vendor/control interface = usage_page 0xff00 (iface 4). Auth + dial + the
    special editing keys all live here. (usage_page 0x01 is the normal QWERTY
    keyboard, 0x0c is consumer-control; those work without us.)

  * Report 0x03 - DIAL / search wheel (13 bytes):
        byte 0    report id (0x03)
        byte 1    jog mode (we keep it at 0 = relative)
        byte 2..5 signed little-endian delta; >0 = clockwise/right, <0 = left.
                  Magnitude grows with spin speed. ~360 per slow notch.
        byte 6    0xff (echo of the jog-mode "kick"), rest padding.

  * Report 0x04 - SPECIAL KEYS (13 bytes):
        byte 0    report id (0x04)
        bytes 1.. up to six 16-bit little-endian keycodes currently held down
                  (offsets 1,3,5,7,9,11). The array compacts as keys release.
        Keycodes match the Speed Editor table (see KEY_NAMES).
"""

VID = 0x1EDB
PID_EDITOR_KEYBOARD = 0xDA10
PID_SPEED_EDITOR = 0xDA0E
KNOWN_PIDS = (PID_EDITOR_KEYBOARD, PID_SPEED_EDITOR)

# Report IDs (first byte of each input report)
REPORT_JOG = 0x03
REPORT_KEYS = 0x04
REPORT_BATTERY = 0x07

# The three dial-mode buttons (reported as ordinary keycodes in report 0x04).
MODE_SHTL = 0x1C
MODE_JOG = 0x1D
MODE_SCRL = 0x1E
MODE_KEYS = {MODE_SHTL: "SHTL", MODE_JOG: "JOG", MODE_SCRL: "SCRL"}

# Keycode -> human name. Names come from smunaut's Speed Editor mapping; the
# Editor Keyboard reuses the same codes for the keys it shares. Unknown codes
# get a generic name via key_name().
KEY_NAMES = {
    0x01: "SMART INSRT",
    0x02: "APPND",
    0x03: "RIPL O/WR",
    0x04: "CLOSE UP",
    0x05: "PLACE ON TOP",
    0x06: "SRC O/WR",
    0x07: "IN",
    0x08: "OUT",
    0x09: "TRIM IN",
    0x0A: "TRIM OUT",
    0x0B: "ROLL",
    0x0C: "SLIP SRC",
    0x0D: "SLIP DEST",
    0x0E: "TRANS DUR",
    0x0F: "CUT",
    0x10: "DIS",
    0x11: "SMTH CUT",
    0x1A: "SOURCE",
    0x1B: "TIMELINE",
    0x1C: "SHTL",
    0x1D: "JOG",
    0x1E: "SCRL",
    0x1F: "SYNC BIN",
    0x22: "TRANS",
    0x25: "VIDEO ONLY",
    0x26: "AUDIO ONLY",
    0x2B: "RIPL DEL",
    0x2C: "AUDIO LEVEL",
    0x2D: "FULL VIEW",
    0x2E: "SNAP",
    0x2F: "SPLIT",
    0x30: "LIVE O/WR",
    0x31: "ESC",
    0x33: "CAM1",
    0x34: "CAM2",
    0x35: "CAM3",
    0x36: "CAM4",
    0x37: "CAM5",
    0x38: "CAM6",
    0x39: "CAM7",
    0x3A: "CAM8",
    0x3B: "CAM9",
    0x3C: "STOP/PLAY",
}


FKEY_CODES = {
    0x1F: "f1", 0x20: "f2", 0x21: "f3", 0x22: "f4", 0x23: "f5", 0x24: "f6",
    0x25: "f7", 0x26: "f8", 0x27: "f9", 0x28: "f10", 0x29: "f11", 0x2A: "f12",
}
# On this keyboard the F-row sends the vendor codes above (decoded live), not real
# F-keys. Show them as F1..F12 in the UI; the dispatcher passes them through.
KEY_NAMES.update({c: v.upper() for c, v in FKEY_CODES.items()})


def key_name(code):
    """Human-friendly label for a keycode (falls back to KEY_0xNN)."""
    return KEY_NAMES.get(code, f"KEY_0x{code:02X}")


def parse_jog(report):
    """(mode_byte, signed_delta) from a report 0x03. Returns (0, 0) if too short."""
    if len(report) < 6:
        return 0, 0
    mode = report[1]
    value = int.from_bytes(report[2:6], "little", signed=True)
    return mode, value


def parse_keys(report):
    """Set of keycodes currently held, from a report 0x04."""
    codes = set()
    for i in range(1, len(report) - 1, 2):
        c = report[i] | (report[i + 1] << 8)
        if c:
            codes.add(c)
    return codes
