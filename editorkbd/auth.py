# -*- coding: utf-8 -*-
"""
Blackmagic keyboard authentication.

The challenge/response function (bmd_kbd_auth) and the Report ID 6 handshake are
ported from Sylvain Munaut's reverse engineering of the Speed Editor:
    https://github.com/smunaut/blackmagic-misc  (bmd.py, Apache-2.0)

The full-size Editor Keyboard (USB PID 0xDA10) uses the same scheme and reports a
600 s re-auth timeout. The response has to be bit-exact, otherwise the dial and the
editing keys stay silent, so the tables below must not change.
"""


def rol8(v):
    return ((v << 56) | (v >> 8)) & 0xFFFFFFFFFFFFFFFF


def rol8n(v, n):
    for _ in range(n):
        v = rol8(v)
    return v


def bmd_kbd_auth(challenge):
    AUTH_EVEN_TBL = [
        0x3AE1206F97C10BC8, 0x2A9AB32BEBF244C6,
        0x20A6F8B8DF9ADF0A, 0xAF80ECE52CFC1719,
        0xEC2EE2F7414FD151, 0xB055ADFD73344A15,
        0xA63D2E3059001187, 0x751BF623F42E0DDE,
    ]
    AUTH_ODD_TBL = [
        0x3E22B34F502E7FDE, 0x24656B981875AB1C,
        0xA17F3456DF7BF8C3, 0x6DF72E1941AEF698,
        0x72226F011E66AB94, 0x3831A3C606296B42,
        0xFD7FF81881332C89, 0x61A3F6474FF236C6,
    ]
    MASK = 0xA79A63F585D37BF0

    n = challenge & 7
    v = rol8n(challenge, n)

    if (v & 1) == ((0x78 >> n) & 1):
        k = AUTH_EVEN_TBL[n]
    else:
        v = v ^ rol8(v)
        k = AUTH_ODD_TBL[n]

    return v ^ (rol8(v) & MASK) ^ k


def _gfr(dev):
    """get_feature_report(6, 10) normalised to bytes across hid bindings."""
    raw = dev.get_feature_report(6, 10)
    return bytes(raw) if raw else b""


def authenticate(dev):
    """Run the full handshake on an open hid device.

    Returns the re-auth timeout in seconds (e.g. 600). Raises RuntimeError if the
    keyboard rejects any step (usually means wrong interface or Resolve is open).
    """
    # 1) reset the auth state machine
    dev.send_feature_report(b"\x06" + b"\x00" * 9)

    # 2) read the keyboard challenge
    d = _gfr(dev)
    if len(d) < 10 or d[0:2] != b"\x06\x00":
        raise RuntimeError(f"bad challenge report: {d.hex()!r}")
    challenge = int.from_bytes(d[2:10], "little")

    # 3) send our (don't-care) challenge to the keyboard
    dev.send_feature_report(b"\x06\x01" + b"\x00" * 8)

    # 4) read the keyboard response (ignored)
    d = _gfr(dev)
    if d[0:2] != b"\x06\x02":
        raise RuntimeError(f"bad kbd response report: {d.hex()!r}")

    # 5) compute + send our response
    response = bmd_kbd_auth(challenge)
    dev.send_feature_report(b"\x06\x03" + response.to_bytes(8, "little"))

    # 6) read status -> contains the re-auth timeout (LE16) in bytes 2..4
    d = _gfr(dev)
    if d[0:2] != b"\x06\x04":
        raise RuntimeError(f"bad status report: {d.hex()!r}")
    timeout = int.from_bytes(d[2:4], "little")
    return timeout or 600
