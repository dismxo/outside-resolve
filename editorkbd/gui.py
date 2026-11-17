# -*- coding: utf-8 -*-
"""
PySide6 GUI: system-tray app with live mode indicator, profile management,
Capture mode (press a key -> assign an action), an editable mapping table,
tuning settings and a "start with Windows" toggle.
"""

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import autostart, protocol

MODE_ACTION = {"JOG": "Volume", "SCRL": "Scroll", "SHTL": "Zoom"}
ACTION_TYPES = [
    ("Hotkey (key combo)", "hotkey"),
    ("Type text", "text"),
    ("Launch app / file / URL", "launch"),
    ("Run shell command", "shell"),
    ("Media: play / pause", "playpause"),
    ("Mute / unmute toggle", "mute"),
]
TYPE_HINTS = {
    "hotkey": "e.g.  ctrl+c   ·   win+e   ·   alt+f4   ·   ctrl+shift+esc   ·   f5",
    "text": "The text to type out, e.g. an email address or a snippet.",
    "launch": r"e.g.  C:\Program Files\App\app.exe   ·   https://example.com   ·   C:\docs\file.pdf",
    "shell": "Runs via the shell, e.g.  wt -w 0 nt   ·   cmd /c start .",
    "playpause": "No value needed — sends the media Play/Pause key.",
    "mute": "No value needed — toggles system mute.",
}


def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QColor("#1f2430"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setBrush(QColor("#3d8bfd"))
    p.drawEllipse(16, 16, 32, 32)
    p.setBrush(QColor("#eaf2ff"))
    p.drawEllipse(29, 9, 6, 6)  # the dial "notch"
    p.end()
    return QIcon(pm)


class AssignDialog(QDialog):
    """Create/edit a macro action for a captured keycode."""

    def __init__(self, code, action=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assign action")
        self.setMinimumWidth(440)
        action = action or {}

        title = QLabel(f"<b>{protocol.key_name(code)}</b>  (code 0x{code:02X})")
        self.type_combo = QComboBox()
        for label, _ in ACTION_TYPES:
            self.type_combo.addItem(label)
        cur_type = action.get("type", "hotkey")
        for i, (_, t) in enumerate(ACTION_TYPES):
            if t == cur_type:
                self.type_combo.setCurrentIndex(i)
                break

        self.value_edit = QLineEdit(action.get("value", ""))
        self.label_edit = QLineEdit(action.get("label", ""))
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#7a8699;")

        form = QFormLayout()
        form.addRow("Action:", self.type_combo)
        form.addRow("Value:", self.value_edit)
        form.addRow("", self.hint)
        form.addRow("Label (optional):", self.label_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.type_combo.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

    def _update_hint(self):
        t = self.current_type()
        self.hint.setText(TYPE_HINTS.get(t, ""))
        self.value_edit.setEnabled(t not in ("playpause", "mute"))

    def current_type(self):
        return ACTION_TYPES[self.type_combo.currentIndex()][1]

    def action(self):
        return {
            "type": self.current_type(),
            "value": self.value_edit.text(),
            "label": self.label_edit.text().strip(),
        }


class MainWindow(QMainWindow):
    def __init__(self, config, bus, dispatcher, driver, volume=None):
        super().__init__()
        self.config = config
        self.bus = bus
        self.dispatcher = dispatcher
        self.driver = driver
        self.volume = volume
        self._loading = False
        self._capturing = False
        self._capture_for = None
        self._force_quit = False

        self.setWindowTitle("Outside Resolve")
        self.setWindowIcon(make_icon())
        self.resize(660, 880)

        self._build_ui()
        self._build_tray()
        self._connect_bus()

        self._reload_profiles()
        self.refresh_table()
        self._load_settings()
        self._update_mode(self.dispatcher.mode())

        # Poll the driver's plain attributes as a fallback, independent of
        # cross-thread Qt signals, so the status always reflects reality.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_driver)
        self._poll.start(800)

    # ---- UI construction ---------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)

        # Status + mode indicator
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color:#c0392b;")
        self.status_text = QLabel("Starting…")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text, 1)
        root.addLayout(status_row)

        self.mode_chips = {}
        chips_row = QHBoxLayout()
        for mode in ("SHTL", "JOG", "SCRL"):
            chip = QLabel(f"{mode}\n{MODE_ACTION[mode]}")
            chip.setAlignment(Qt.AlignCenter)
            chip.setFixedHeight(48)
            chip.setFont(QFont("", 9, QFont.Bold))
            self.mode_chips[mode] = chip
            chips_row.addWidget(chip, 1)
        root.addLayout(chips_row)
        hint = QLabel("Press SHTL / JOG / SCRL on the keyboard to switch what the dial does.")
        hint.setStyleSheet("color:#7a8699;")
        root.addWidget(hint)

        # Profiles
        prof_box = QGroupBox("Profile")
        prof_l = QHBoxLayout(prof_box)
        self.profile_combo = QComboBox()
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        btn_new = QPushButton("New")
        btn_ren = QPushButton("Rename")
        btn_del = QPushButton("Delete")
        btn_new.clicked.connect(self._new_profile)
        btn_ren.clicked.connect(self._rename_profile)
        btn_del.clicked.connect(self._delete_profile)
        prof_l.addWidget(self.profile_combo, 1)
        prof_l.addWidget(btn_new)
        prof_l.addWidget(btn_ren)
        prof_l.addWidget(btn_del)
        root.addWidget(prof_box)

        # Mappings
        map_box = QGroupBox("Key mappings (this profile)")
        map_l = QVBoxLayout(map_box)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Key", "Code", "Action", "Value", "Label"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 80)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.doubleClicked.connect(lambda *_: self._edit_selected())
        map_l.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.capture_btn = QPushButton("◉  Capture a key…")
        self.capture_btn.setToolTip("Then press a special key on the keyboard to assign an action.")
        self.capture_btn.clicked.connect(self._toggle_capture)
        btn_edit = QPushButton("Edit")
        btn_clear = QPushButton("Delete")
        btn_edit.clicked.connect(self._edit_selected)
        btn_clear.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.capture_btn, 1)
        btn_row.addWidget(btn_edit)
        btn_row.addWidget(btn_clear)
        map_l.addLayout(btn_row)

        self.capture_status = QLabel("")
        self.capture_status.setStyleSheet("color:#3d8bfd;")
        map_l.addWidget(self.capture_status)
        root.addWidget(map_box, 1)

        # Settings
        set_box = QGroupBox("Dial tuning && options")
        form = QFormLayout(set_box)
        self.sp_volspeed = QSpinBox()
        self.sp_volspeed.setRange(1, 11)
        self.sp_volspeed.valueChanged.connect(lambda v: self._save_setting("volume_speed", v))
        self.cb_vol_inv = QCheckBox("invert")
        self.cb_vol_inv.toggled.connect(lambda v: self._save_setting("volume_invert", v))
        vol_row = QHBoxLayout()
        vol_row.addWidget(self.sp_volspeed, 1)
        vol_row.addWidget(self.cb_vol_inv)
        vol_w = QWidget()
        vol_w.setLayout(vol_row)
        form.addRow("Volume speed (JOG · 6 = default):", vol_w)

        self.sp_scrollspeed = QSpinBox()
        self.sp_scrollspeed.setRange(1, 11)
        self.sp_scrollspeed.valueChanged.connect(lambda v: self._save_setting("scroll_speed", v))
        self.cb_scroll_inv = QCheckBox("invert")
        self.cb_scroll_inv.toggled.connect(lambda v: self._save_setting("scroll_invert", v))
        scroll_row = QHBoxLayout()
        scroll_row.addWidget(self.sp_scrollspeed, 1)
        scroll_row.addWidget(self.cb_scroll_inv)
        scroll_w = QWidget()
        scroll_w.setLayout(scroll_row)
        form.addRow("Scroll speed (SCRL · 6 = default):", scroll_w)

        self.sp_zoomspeed = QSpinBox()
        self.sp_zoomspeed.setRange(1, 11)
        self.sp_zoomspeed.valueChanged.connect(lambda v: self._save_setting("zoom_speed", v))
        self.cb_zoom_inv = QCheckBox("invert")
        self.cb_zoom_inv.toggled.connect(lambda v: self._save_setting("zoom_invert", v))
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(self.sp_zoomspeed, 1)
        zoom_row.addWidget(self.cb_zoom_inv)
        zoom_w = QWidget()
        zoom_w.setLayout(zoom_row)
        form.addRow("Zoom speed (SHTL · 6 = default):", zoom_w)

        self.sp_reauth = QSpinBox()
        self.sp_reauth.setRange(0, 590)
        self.sp_reauth.setSuffix(" s")
        self.sp_reauth.setSpecialValueText("auto")
        self.sp_reauth.valueChanged.connect(lambda v: self._save_setting("reauth_seconds", v))
        form.addRow("Re-auth interval (0 = auto):", self.sp_reauth)

        self.knob_btn = QPushButton("Set knob press (press the dial)…")
        self.knob_btn.clicked.connect(self._capture_knob)
        self.knob_label = QLabel("")
        self.knob_label.setStyleSheet("color:#7a8699;")
        knob_row = QHBoxLayout()
        knob_row.addWidget(self.knob_btn, 1)
        knob_row.addWidget(self.knob_label)
        knob_w = QWidget()
        knob_w.setLayout(knob_row)
        form.addRow("Knob press → JOG: mute · SCRL/SHTL: play/pause", knob_w)

        self.cb_pause_run = QCheckBox("Stand down whenever DaVinci Resolve is running (recommended)")
        self.cb_pause_run.toggled.connect(lambda v: self._save_setting("pause_when_resolve_running", v))
        form.addRow(self.cb_pause_run)
        self.cb_pause = QCheckBox("Also pause only while Resolve is the focused window")
        self.cb_pause.toggled.connect(lambda v: self._save_setting("pause_when_resolve_active", v))
        form.addRow(self.cb_pause)

        self.cb_fkeys = QCheckBox("Make the F-row act as normal F1–F12 keys")
        self.cb_fkeys.toggled.connect(lambda v: self._save_setting("fkeys_as_function", v))
        form.addRow(self.cb_fkeys)

        self.cb_autostart = QCheckBox("Start automatically when Windows starts")
        self.cb_autostart.toggled.connect(self._toggle_autostart)
        form.addRow(self.cb_autostart)

        root.addWidget(set_box)
        self.setCentralWidget(central)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("Outside Resolve")
        menu = QMenu()
        act_open = QAction("Open", self)
        act_open.triggered.connect(self._show_window)
        menu.addAction(act_open)
        self.tray_mode_action = QAction("Mode: —", self)
        self.tray_mode_action.setEnabled(False)
        menu.addAction(self.tray_mode_action)
        menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)
        self.tray_menu = menu  # setContextMenu doesn't keep it alive
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    # ---- bus wiring --------------------------------------------------------
    def _connect_bus(self):
        self.bus.status.connect(self._on_status)
        self.bus.connected.connect(self._on_connected)
        self.bus.modeChanged.connect(self._update_mode)
        self.bus.keyEvent.connect(self._on_key_event)
        self.bus.captured.connect(self._on_captured)
        self.bus.macroTriggered.connect(self._on_macro)
        self.bus.volumeDelta.connect(self._apply_volume)

    @Slot(float)
    def _apply_volume(self, delta):
        self.volume.add(delta)  # Windows shows its own volume bar for these

    @Slot(str)
    def _on_status(self, text):
        self.status_text.setText(text)

    @Slot(bool)
    def _on_connected(self, ok):
        self.status_dot.setStyleSheet("color:#27ae60;" if ok else "color:#c0392b;")

    def _poll_driver(self):
        text = getattr(self.driver, "last_status", None)
        if text and text != self.status_text.text():
            self.status_text.setText(text)
        ok = bool(getattr(self.driver, "is_connected", False))
        self.status_dot.setStyleSheet("color:#27ae60;" if ok else "color:#c0392b;")

    @Slot(str)
    def _update_mode(self, mode):
        for m, chip in self.mode_chips.items():
            if m == mode:
                chip.setStyleSheet(
                    "background:#3d8bfd; color:white; border-radius:8px;")
            else:
                chip.setStyleSheet(
                    "background:#262b36; color:#9aa6b8; border-radius:8px;")
        self.tray_mode_action.setText(f"Mode: {mode} → {MODE_ACTION.get(mode, '')}")
        self.tray.setToolTip(f"Outside Resolve — {mode}: {MODE_ACTION.get(mode, '')}")

    @Slot(int, bool)
    def _on_key_event(self, code, down):
        if down and not self._capturing:
            self.capture_status.setText(
                f"Last key: {protocol.key_name(code)} (0x{code:02X})")

    @Slot(int, str)
    def _on_macro(self, code, label):
        self.capture_status.setText(f"Ran: {label or protocol.key_name(code)}")

    # ---- capture -----------------------------------------------------------
    def _toggle_capture(self):
        if self._capturing:
            self._end_capture()
        else:
            self._capturing = True
            self._capture_for = "macro"
            self.dispatcher.set_capture(True)
            self.capture_btn.setText("⏺  Press a key… (click to cancel)")
            self.capture_status.setText("Waiting for a special key press on the keyboard…")

    def _end_capture(self):
        self._capturing = False
        self._capture_for = None
        self.dispatcher.set_capture(False)
        self.capture_btn.setText("◉  Capture a key…")
        self.knob_btn.setText("Set knob press (press the dial)…")

    def _capture_knob(self):
        if self._capturing:
            self._end_capture()
            return
        self._capturing = True
        self._capture_for = "knob"
        self.dispatcher.set_capture(True)
        self.knob_btn.setText("Press the dial now… (click to cancel)")
        self.capture_status.setText("Press the dial (knob) to bind it…")

    @Slot(int)
    def _on_captured(self, code):
        if not self._capturing:
            return
        target = self._capture_for
        self._end_capture()
        if target == "knob":
            self.config.set_setting("knob_code", int(code))
            self.knob_label.setText("%s (0x%02X)" % (protocol.key_name(code), code))
            self.capture_status.setText("Knob press bound to %s." % protocol.key_name(code))
            return
        if code in protocol.MODE_KEYS:
            QMessageBox.information(
                self, "Reserved key",
                f"{protocol.MODE_KEYS[code]} is reserved for switching the dial mode "
                "and can't be mapped to a macro.")
            return
        existing = self.config.mappings().get(code)
        dlg = AssignDialog(code, existing, self)
        if dlg.exec() == QDialog.Accepted:
            self.config.set_mapping(code, dlg.action())
            self.refresh_table()
            self.capture_status.setText(
                f"Mapped {protocol.key_name(code)} (0x{code:02X}).")

    # ---- mapping table -----------------------------------------------------
    def refresh_table(self):
        maps = self.config.mappings()
        self.table.setRowCount(0)
        for code in sorted(maps):
            act = maps[code]
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(protocol.key_name(code))
            name_item.setData(Qt.UserRole, code)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(f"0x{code:02X}"))
            self.table.setItem(row, 2, QTableWidgetItem(act.get("type", "")))
            self.table.setItem(row, 3, QTableWidgetItem(act.get("value", "")))
            self.table.setItem(row, 4, QTableWidgetItem(act.get("label", "")))

    def _selected_code(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _edit_selected(self):
        code = self._selected_code()
        if code is None:
            return
        existing = self.config.mappings().get(code)
        dlg = AssignDialog(code, existing, self)
        if dlg.exec() == QDialog.Accepted:
            self.config.set_mapping(code, dlg.action())
            self.refresh_table()

    def _delete_selected(self):
        code = self._selected_code()
        if code is None:
            return
        self.config.clear_mapping(code)
        self.refresh_table()

    # ---- profiles ----------------------------------------------------------
    def _reload_profiles(self):
        self._loading = True
        self.profile_combo.clear()
        self.profile_combo.addItems(self.config.profile_names())
        self.profile_combo.setCurrentText(self.config.active_profile())
        self._loading = False

    def _on_profile_changed(self, name):
        if self._loading or not name:
            return
        self.config.set_active_profile(name)
        self.refresh_table()

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New profile", "Name:")
        if ok and name.strip():
            self.config.add_profile(name.strip())
            self._reload_profiles()
            self.profile_combo.setCurrentText(name.strip())

    def _rename_profile(self):
        old = self.config.active_profile()
        name, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=old)
        if ok and name.strip():
            self.config.rename_profile(old, name.strip())
            self._reload_profiles()

    def _delete_profile(self):
        if len(self.config.profile_names()) <= 1:
            QMessageBox.information(self, "Profiles", "You need at least one profile.")
            return
        name = self.config.active_profile()
        if QMessageBox.question(self, "Delete profile", f"Delete profile '{name}'?") == QMessageBox.Yes:
            self.config.remove_profile(name)
            self._reload_profiles()
            self.refresh_table()

    # ---- settings ----------------------------------------------------------
    def _load_settings(self):
        self._loading = True
        s = self.config.settings()
        self.sp_volspeed.setValue(int(s.get("volume_speed", 6)))
        self.cb_vol_inv.setChecked(bool(s.get("volume_invert", False)))
        self.sp_scrollspeed.setValue(int(s.get("scroll_speed", 6)))
        self.cb_scroll_inv.setChecked(bool(s.get("scroll_invert", False)))
        self.sp_zoomspeed.setValue(int(s.get("zoom_speed", 6)))
        self.cb_zoom_inv.setChecked(bool(s.get("zoom_invert", False)))
        self.sp_reauth.setValue(int(s.get("reauth_seconds", 0)))
        self.cb_autostart.setChecked(autostart.is_enabled())
        self.cb_autostart.setEnabled(autostart.is_supported())
        self.cb_pause.setChecked(bool(s.get("pause_when_resolve_active", True)))
        self.cb_pause_run.setChecked(bool(s.get("pause_when_resolve_running", True)))
        self.cb_fkeys.setChecked(bool(s.get("fkeys_as_function", True)))
        kc = s.get("knob_code")
        self.knob_label.setText("%s (0x%02X)" % (protocol.key_name(int(kc)), int(kc)) if kc is not None else "not set")
        self._loading = False

    def _save_setting(self, key, value):
        if self._loading:
            return
        self.config.set_setting(key, value)

    def _toggle_autostart(self, on):
        if self._loading:
            return
        autostart.set_enabled(on)
        self._loading = True
        self.cb_autostart.setChecked(autostart.is_enabled())
        self._loading = False

    # ---- window / tray lifecycle ------------------------------------------
    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_window()

    def show_window(self):
        self._show_window()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self._force_quit:
            event.accept()
            return
        event.ignore()
        self.hide()
        if self.tray.supportsMessages():
            self.tray.showMessage(
                "Outside Resolve",
                "Still running in the tray. Right-click the tray icon to quit.",
                make_icon(), 3000)

    def _quit(self):
        self._force_quit = True
        try:
            self.driver.stop()
            self.driver.wait(2000)
        except Exception:
            pass
        self.tray.hide()
        QApplication.quit()
