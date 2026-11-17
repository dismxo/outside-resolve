# -*- coding: utf-8 -*-
"""
Outside Resolve - entry point.

Wires together config, bus, dispatcher, driver (HID thread) and GUI.
Run with:  python main.py   (--minimized starts in the tray, used at Windows startup)

The HID driver runs on its own thread. Volume key presses are sent from the main
thread through the bus.volumeDelta signal.

A single-instance lock (a local named pipe) keeps two copies from fighting over the
keyboard. Starting a second copy brings up the first one's window.
"""

import os
import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from editorkbd import __app_name__
from editorkbd.bus import Bus
from editorkbd.config import Config
from editorkbd.dispatcher import Dispatcher
from editorkbd.driver import Driver
from editorkbd.gui import MainWindow, make_icon
from editorkbd.macros import ActionRunner
from editorkbd.volume import VolumeKeys

_SINGLE_INSTANCE_NAME = "OutsideResolve-single-instance"


def _ask_running_copy_to_show():
    sock = QLocalSocket()
    sock.connectToServer(_SINGLE_INSTANCE_NAME)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show")
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)  # live in the tray

    # Single-instance guard: if another copy is listening, hand over to it.
    if _ask_running_copy_to_show():
        return 0
    lock = QLocalServer()
    if not lock.listen(_SINGLE_INSTANCE_NAME):
        return 0

    config = Config()
    bus = Bus()
    volume = VolumeKeys()
    runner = ActionRunner(on_error=lambda msg: bus.status.emit("Macro error: " + msg))
    dispatcher = Dispatcher(config, bus, runner)
    driver = Driver(dispatcher, bus, config)

    window = MainWindow(config, bus, dispatcher, driver, volume)
    lock.newConnection.connect(window.show_window)
    if "--minimized" in sys.argv or config.get_setting("start_minimized", False):
        window.hide()
    else:
        window.show()

    driver.start()
    rc = app.exec()

    driver.stop()
    driver.wait(2000)
    runner.stop()
    lock.close()
    if driver.isRunning():      # destroying a live QThread would crash on the way out
        os._exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
