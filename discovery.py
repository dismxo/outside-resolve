#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discovery.py - raw HID probe for the Blackmagic Editor Keyboard (and Speed Editor).

The QWERTY block of the keyboard is a normal USB keyboard, but the search dial and
the editing keys stay silent until the host completes Blackmagic's
challenge/response handshake. This tool was used to map the full-size keyboard's
reports. It:

  1. lists every Blackmagic (VID 0x1edb) HID interface,
  2. opens the most likely control interface,
  3. runs the handshake and prints every report exchanged,
  4. streams raw input reports as hex, marking the bytes that changed, so you can
     see what turning the dial or pressing a key sends.

Close DaVinci Resolve first: it holds the control interface while it runs.

Usage:
    python discovery.py                # auto-pick the control interface
    python discovery.py --list         # list interfaces and exit
    python discovery.py --index N      # use the Nth listed interface
    python discovery.py --path PATH    # use a specific hidapi path
    python discovery.py --no-jogmode   # don't send the jog-mode report

Needs the "hidapi" package (pip install hidapi). Expect enumeration to take a few
seconds: the keyboard never answers string-descriptor requests, and hidapi waits
for each one to time out. The app itself avoids this (see editorkbd/hidraw.py).
"""

import argparse
import struct
import sys
import time

from editorkbd.auth import bmd_kbd_auth

try:
    import hid
except ImportError:
    sys.exit("Missing dependency. Install it with:\n    pip install hidapi")

# The "hidapi" package exposes hid.device(); the unrelated "hid" package exposes
# hid.Device() and won't work here.
if not hasattr(hid, "device"):
    sys.exit(
        "Wrong 'hid' module is installed.\n"
        "  pip uninstall -y hid\n"
        "  pip install hidapi\n"
    )

BMD_VID = 0x1EDB             # Blackmagic Design USB vendor id
SPEED_EDITOR_PID = 0xDA0E    # the small Speed Editor


# ---- Small helpers ---------------------------------------------------------
def hx(data):
    """bytes/list -> 'aa bb cc' hex string."""
    return " ".join(f"{b:02x}" for b in bytes(data))


_READ_TIMEOUT_OK = True


def read_report(dev, max_len=64, timeout_ms=1000):
    """Read one input report, tolerant of small hidapi binding differences."""
    global _READ_TIMEOUT_OK
    if _READ_TIMEOUT_OK:
        try:
            return dev.read(max_len, timeout_ms)
        except TypeError:
            _READ_TIMEOUT_OK = False
            try:
                dev.set_nonblocking(True)
            except Exception:
                pass
    data = dev.read(max_len)
    if not data:
        time.sleep(0.003)
    return data


# ---- Enumeration -----------------------------------------------------------
def find_bmd_interfaces():
    devs = [d for d in hid.enumerate() if d.get("vendor_id") == BMD_VID]
    devs.sort(key=lambda d: (d.get("product_id", 0),
                             d.get("interface_number", -1),
                             d.get("usage_page", 0),
                             d.get("usage", 0)))
    return devs


def print_interfaces(devs):
    if not devs:
        print("No Blackmagic (VID 0x1edb) HID interface found.")
        print("  * Is the keyboard connected over USB?")
        print("  * Is DaVinci Resolve closed? It holds the device while it runs.")
        return
    print(f"Found {len(devs)} Blackmagic HID interface(s):\n")
    for i, d in enumerate(devs):
        prod = (d.get("product_string") or "").strip()
        manu = (d.get("manufacturer_string") or "").strip()
        print(f"  [{i}] PID=0x{d.get('product_id', 0):04x}  "
              f"usage_page=0x{d.get('usage_page', 0):04x} "
              f"usage=0x{d.get('usage', 0):02x}  "
              f"iface={d.get('interface_number')}")
        print(f"      product={prod!r}  manufacturer={manu!r}")
        if d.get("product_id") == SPEED_EDITOR_PID:
            print("      (this PID is the small Speed Editor)")
        try:
            print(f"      path={d['path'].decode('ascii', 'replace')}")
        except Exception:
            print(f"      path={d.get('path')}")
    print()


def candidate_order(devs):
    """Order interfaces by how likely they carry auth + dial + special keys.

    The dial/keys/auth live on the vendor-defined collection (usage_page
    >= 0xff00), NOT on the standard boot-keyboard collection (0x01/0x06),
    which Windows grabs for itself anyway.
    """
    vendor = [d for d in devs if d.get("usage_page", 0) >= 0xFF00]
    nonkbd = [d for d in devs
              if d not in vendor
              and not (d.get("usage_page") == 0x01 and d.get("usage") in (0x06, 0x02))]
    rest = [d for d in devs if d not in vendor and d not in nonkbd]
    return vendor + nonkbd + rest


# ---- Auth handshake (verbose) ----------------------------------------------
def authenticate(dev, verbose=True):
    """Run the BMD auth handshake. Returns the re-auth timeout in seconds."""
    def gfr():
        raw = dev.get_feature_report(6, 10)
        return bytes(raw) if raw else b""

    def log(*a):
        if verbose:
            print(*a)

    log("  -> [reset]      send_feature: 06 00 00 00 00 00 00 00 00 00")
    dev.send_feature_report(b"\x06" + b"\x00" * 9)

    d = gfr()
    log(f"  <- [challenge]  {hx(d)}")
    if len(d) < 10:
        raise RuntimeError(f"short challenge ({len(d)} bytes) - auth not on this interface")
    if d[0:2] != b"\x06\x00":
        log("     [!] header is not 06 00 - probably the wrong interface (continuing)")
    challenge = int.from_bytes(d[2:10], "little")
    log(f"     challenge = 0x{challenge:016x}")

    log("  -> [our chall]  send_feature: 06 01 00..00  (we do not auth the kbd)")
    dev.send_feature_report(b"\x06\x01" + b"\x00" * 8)

    d = gfr()
    log(f"  <- [kbd resp ]  {hx(d)}")
    if d[0:2] != b"\x06\x02":
        log("     [!] header is not 06 02 (continuing)")

    response = bmd_kbd_auth(challenge)
    log(f"     response  = 0x{response:016x}")
    log("  -> [our resp ]  send_feature: 06 03 <response LE64>")
    dev.send_feature_report(b"\x06\x03" + response.to_bytes(8, "little"))

    d = gfr()
    log(f"  <- [status   ]  {hx(d)}")
    if d[0:2] != b"\x06\x04":
        log("     [!] header is not 06 04 - auth may have failed")
    timeout = int.from_bytes(d[2:4], "little")
    log(f"     AUTH OK. Re-auth timeout reported = {timeout} s")
    return timeout


# ---- Best-effort "kick" to make the dial start reporting -------------------
def kick_jog_mode(dev):
    """On the Speed Editor, output Report ID 3 sets the jog mode and makes the
    dial start sending reports. The full-size keyboard accepts the same report; if a
    device rejects it, pressing JOG or SCRL on the keyboard has the same effect."""
    try:
        # report id 3, mode 0 (relative), le32 = 0, trailing byte 0xff
        dev.write(struct.pack("<BBIB", 3, 0, 0, 0xFF))
        print("  -> sent Speed-Editor jog-mode kick (output report 03).")
        print("     If the big keyboard ignores it, just press JOG/SCRL on the device.")
    except Exception as e:
        print(f"  (jog-mode kick not accepted: {e} - ignore it, press JOG/SCRL instead)")


# ---- Stream loop -----------------------------------------------------------
def _reauth_interval(timeout):
    if timeout and timeout > 0:
        return max(30, int(timeout * 0.5))
    return 250


def stream(dev, reauth_timeout, send_kick=True):
    if send_kick:
        kick_jog_mode(dev)

    print("\n" + "=" * 74)
    print("STREAMING raw input reports.   (press Ctrl+C to stop)")
    print("Do one thing at a time and note what you did:")
    print("  1) press SHTL, then JOG, then SCRL   (the three dial-mode buttons)")
    print("  2) in each mode, turn the dial one notch right, then one notch left")
    print("  3) press each special editing key once")
    print("Bytes shown in [..] changed vs the previous report with the same id.")
    print("The first byte of every line is the HID report ID.")
    print("=" * 74 + "\n")

    last_by_id = {}
    deadline = time.time() + _reauth_interval(reauth_timeout)

    try:
        while True:
            data = read_report(dev, 64, 1000)
            if data:
                data = bytes(data)
                rid = data[0]
                prev = last_by_id.get(rid)
                parts = []
                for j, b in enumerate(data):
                    if prev is not None and j < len(prev) and prev[j] != b:
                        parts.append(f"[{b:02x}]")
                    else:
                        parts.append(f"{b:02x}")
                ts = time.strftime("%H:%M:%S")
                print(f"{ts}  id=0x{rid:02x} len={len(data):2d}  " + " ".join(parts))
                last_by_id[rid] = data

            if reauth_timeout and time.time() >= deadline:
                print("  [keep-alive] re-authenticating ...")
                try:
                    reauth_timeout = authenticate(dev, verbose=False) or reauth_timeout
                except Exception as e:
                    print(f"  [keep-alive] re-auth failed: {e}")
                deadline = time.time() + _reauth_interval(reauth_timeout)
    except KeyboardInterrupt:
        print("\nStopped.")


# ---- Main ------------------------------------------------------------------
def open_and_auth(dev_info, verbose=True):
    """Open a single interface and authenticate. Returns (dev, timeout) or None."""
    dev = hid.device()
    try:
        dev.open_path(dev_info["path"])
    except Exception as e_path:
        try:
            dev.open(dev_info["vendor_id"], dev_info["product_id"])
        except Exception as e_vp:
            print(f"  open failed (path: {e_path}; vid/pid: {e_vp})")
            return None
    try:
        timeout = authenticate(dev, verbose=verbose)
        return dev, timeout
    except Exception as e:
        print(f"  auth failed on this interface: {e}")
        try:
            dev.close()
        except Exception:
            pass
        return None


def main():
    ap = argparse.ArgumentParser(description="Blackmagic Editor Keyboard HID discovery")
    ap.add_argument("--list", action="store_true", help="list BMD interfaces and exit")
    ap.add_argument("--index", type=int, help="force the Nth listed BMD interface")
    ap.add_argument("--path", help="force a specific hidapi device path")
    ap.add_argument("--no-jogmode", action="store_true", help="don't send the jog-mode kick")
    args = ap.parse_args()

    devs = find_bmd_interfaces()
    print_interfaces(devs)
    if args.list or not devs:
        return

    # Decide which interface(s) to try.
    forced = None
    if args.path:
        target = args.path.encode() if isinstance(args.path, str) else args.path
        forced = next((d for d in devs if d.get("path") == target), None)
        if forced is None:
            print(f"--path {args.path} not found among BMD interfaces.")
            return
    elif args.index is not None:
        if not (0 <= args.index < len(devs)):
            print(f"--index {args.index} out of range (0..{len(devs) - 1}).")
            return
        forced = devs[args.index]

    candidates = [forced] if forced else candidate_order(devs)

    dev = timeout = None
    for d in candidates:
        print(f"\n--- trying interface: PID=0x{d.get('product_id', 0):04x} "
              f"usage_page=0x{d.get('usage_page', 0):04x} "
              f"iface={d.get('interface_number')} ---")
        result = open_and_auth(d, verbose=True)
        if result:
            dev, timeout = result
            print("    => this interface authenticated. Using it.")
            break

    if dev is None:
        print("\nCould not authenticate on any Blackmagic interface.")
        print("Things to check:")
        print("  * DaVinci Resolve must be fully closed.")
        print("  * Try forcing an interface:  python discovery.py --index N")
        return

    stream(dev, timeout, send_kick=not args.no_jogmode)
    try:
        dev.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
