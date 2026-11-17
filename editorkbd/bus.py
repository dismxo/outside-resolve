# -*- coding: utf-8 -*-
"""Tiny Qt signal bus between the driver/dispatcher (worker thread) and the GUI."""

from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    status = Signal(str)
    connected = Signal(bool)
    modeChanged = Signal(str)
    keyEvent = Signal(int, bool)
    captured = Signal(int)
    macroTriggered = Signal(int, str)
    dial = Signal(int)
    volumeDelta = Signal(float)     # applied on the main thread
