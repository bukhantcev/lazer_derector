from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .artnet import ArtNetSender
from .calibration import CalibrationModel
from .dmx_fixture import DmxFixture
from .models import CalibrationSample, FixtureConfig, PlanObject, Project
from .plan_view import PlanView
from .project import load_project, save_project
from .vectorworks_import import read_dxf


DEFAULT_CALIBRATION_POINTS = [
    ("center", 0, 0),
    ("left_1m", -1000, 0),
    ("right_1m", 1000, 0),
    ("front_1m", 0, -1000),
    ("back_1m", 0, 1000),
]


def make_cross_calibration_points(radius_mm: int, pan: int, tilt: int) -> list[CalibrationSample]:
    return [
        CalibrationSample("center", 0, 0, pan, tilt),
        CalibrationSample(f"left_{radius_mm}mm", -radius_mm, 0, pan, tilt),
        CalibrationSample(f"right_{radius_mm}mm", radius_mm, 0, pan, tilt),
        CalibrationSample(f"front_{radius_mm}mm", 0, -radius_mm, pan, tilt),
        CalibrationSample(f"back_{radius_mm}mm", 0, radius_mm, pan, tilt),
    ]


class CalibrationWizardDialog(QDialog):
    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.samples = self._initial_samples()
        self.current_index = 0
        self.saved_indexes: set[int] = set()
        self._syncing = False

        self.setWindowTitle("Calibration Wizard")
        self.resize(760, 560)
        self._build_ui()
        self._populate_points()
        self._select_index(0)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        header = QLabel("Point-by-point calibration")
        header.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(header)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        left = QVBoxLayout()
        self.point_list = QListWidget()
        self.point_list.currentRowChanged.connect(self._select_index)
        left.addWidget(QLabel("Calibration points"))
        left.addWidget(self.point_list, 1)
        point_buttons = QHBoxLayout()
        add_btn = QPushButton("Add point")
        remove_btn = QPushButton("Remove")
        add_btn.clicked.connect(self._add_point)
        remove_btn.clicked.connect(self._remove_point)
        point_buttons.addWidget(add_btn)
        point_buttons.addWidget(remove_btn)
        left.addLayout(point_buttons)
        preset_box = QGroupBox("Quick point set")
        preset_form = QFormLayout(preset_box)
        self.radius_spin = self._spin(100, 10000, 1000)
        reset_cross = QPushButton("Reset to cross around zero")
        reset_cross.clicked.connect(self._reset_to_cross)
        preset_form.addRow("Radius, mm", self.radius_spin)
        preset_form.addRow(reset_cross)
        left.addWidget(preset_box)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        body.addLayout(right, 2)

        self.step_label = QLabel()
        self.step_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.coord_label = QLabel()
        self.coord_label.setStyleSheet("color: #657084;")
        right.addWidget(self.step_label)
        right.addWidget(self.coord_label)

        values_box = QGroupBox("Current DMX target")
        values_form = QFormLayout(values_box)
        maximum = 65535 if self.main_window.project.fixture.pan_tilt_16bit else 255
        self.pan_spin = self._spin(0, maximum, self.main_window.current_pan)
        self.tilt_spin = self._spin(0, maximum, self.main_window.current_tilt)
        self.pan_spin.valueChanged.connect(self._manual_values_changed)
        self.tilt_spin.valueChanged.connect(self._manual_values_changed)
        values_form.addRow("Pan", self.pan_spin)
        values_form.addRow("Tilt", self.tilt_spin)
        right.addWidget(values_box)

        jog_box = QGroupBox("Aim the head")
        jog_layout = QVBoxLayout(jog_box)
        step_row = QHBoxLayout()
        self.step_spin = self._spin(1, 10000, self.main_window.jog_step_spin.value())
        step_row.addWidget(QLabel("Jog step"))
        step_row.addWidget(self.step_spin)
        jog_layout.addLayout(step_row)
        grid = QGridLayout()
        tilt_plus = QPushButton("Tilt +")
        tilt_minus = QPushButton("Tilt -")
        pan_minus = QPushButton("Pan -")
        pan_plus = QPushButton("Pan +")
        for button in [tilt_plus, tilt_minus, pan_minus, pan_plus]:
            button.setMinimumHeight(54)
            button.setStyleSheet("font-size: 16px; font-weight: 600;")
        tilt_plus.clicked.connect(lambda: self._jog(0, 1))
        tilt_minus.clicked.connect(lambda: self._jog(0, -1))
        pan_minus.clicked.connect(lambda: self._jog(-1, 0))
        pan_plus.clicked.connect(lambda: self._jog(1, 0))
        grid.addWidget(tilt_plus, 0, 1)
        grid.addWidget(pan_minus, 1, 0)
        grid.addWidget(pan_plus, 1, 2)
        grid.addWidget(tilt_minus, 2, 1)
        jog_layout.addLayout(grid)
        right.addWidget(jog_box)

        laser_row = QHBoxLayout()
        laser_on = QPushButton("Laser ON")
        laser_off = QPushButton("Laser OFF")
        emergency = QPushButton("EMERGENCY OFF")
        emergency.setStyleSheet("font-weight: 700; color: white; background: #b00020;")
        laser_on.clicked.connect(lambda: self.main_window.set_laser(True))
        laser_off.clicked.connect(lambda: self.main_window.set_laser(False))
        emergency.clicked.connect(self.main_window.emergency_laser_off)
        laser_row.addWidget(laser_on)
        laser_row.addWidget(laser_off)
        laser_row.addWidget(emergency)
        right.addLayout(laser_row)

        save_row = QHBoxLayout()
        prev_btn = QPushButton("Previous")
        save_btn = QPushButton("Save point")
        save_next_btn = QPushButton("Save & Next")
        finish_btn = QPushButton("Finish")
        prev_btn.clicked.connect(self._previous)
        save_btn.clicked.connect(self._save_current)
        save_next_btn.clicked.connect(self._save_and_next)
        finish_btn.clicked.connect(self._finish)
        save_btn.setMinimumHeight(42)
        save_next_btn.setMinimumHeight(42)
        save_next_btn.setStyleSheet("font-weight: 700;")
        finish_btn.setMinimumHeight(42)
        save_row.addWidget(prev_btn)
        save_row.addWidget(save_btn)
        save_row.addWidget(save_next_btn)
        save_row.addWidget(finish_btn)
        right.addLayout(save_row)

        root.addWidget(QLabel("Workflow: select a point, turn Laser ON, aim with pan/tilt, then Save & Next. Press Esc in the main window or EMERGENCY OFF here to blackout laser."))

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(max(minimum, min(maximum, value)))
        return spin

    def _initial_samples(self) -> list[CalibrationSample]:
        existing = self.main_window.project.calibration
        legacy_names = {"front_left", "front_right", "back_left", "back_right"}
        if not existing or any(sample.name in legacy_names or abs(sample.x_mm) > 3000 or abs(sample.y_mm) > 3000 for sample in existing):
            return make_cross_calibration_points(1000, self.main_window.current_pan, self.main_window.current_tilt)
        return [CalibrationSample(s.name, s.x_mm, s.y_mm, s.pan, s.tilt) for s in existing]

    def _populate_points(self) -> None:
        self.point_list.blockSignals(True)
        self.point_list.clear()
        for index, sample in enumerate(self.samples):
            mark = "[saved]" if index in self.saved_indexes else "[ ]"
            self.point_list.addItem(f"{mark} {index + 1}. {sample.name}  X={sample.x_mm:g}  Y={sample.y_mm:g}")
        self.point_list.blockSignals(False)
        self.point_list.setCurrentRow(min(self.current_index, max(0, len(self.samples) - 1)))

    def _select_index(self, index: int) -> None:
        if index < 0 or index >= len(self.samples):
            return
        self.current_index = index
        sample = self.samples[index]
        self.step_label.setText(f"Step {index + 1} of {len(self.samples)}: {sample.name}")
        self.coord_label.setText(f"Real point: X={sample.x_mm:g} mm, Y={sample.y_mm:g} mm")
        self.main_window.plan_view.show_target(sample.x_mm, sample.y_mm)
        self._syncing = True
        self.pan_spin.setValue(self.main_window.current_pan)
        self.tilt_spin.setValue(self.main_window.current_tilt)
        self._syncing = False

    def _manual_values_changed(self) -> None:
        if self._syncing:
            return
        self.main_window.set_pan_tilt_values(self.pan_spin.value(), self.tilt_spin.value())

    def _jog(self, pan_dir: int, tilt_dir: int) -> None:
        step = self.step_spin.value()
        self.main_window.set_pan_tilt_values(
            self.main_window.current_pan + pan_dir * step,
            self.main_window.current_tilt + tilt_dir * step,
        )
        self._syncing = True
        self.pan_spin.setValue(self.main_window.current_pan)
        self.tilt_spin.setValue(self.main_window.current_tilt)
        self._syncing = False

    def _save_current(self) -> None:
        sample = self.samples[self.current_index]
        sample.pan = self.main_window.current_pan
        sample.tilt = self.main_window.current_tilt
        self.saved_indexes.add(self.current_index)
        self._apply_samples()
        self._populate_points()
        self.point_list.setCurrentRow(self.current_index)
        self.main_window.statusBar().showMessage(f"Saved calibration point: {sample.name}", 2500)

    def _save_and_next(self) -> None:
        self._save_current()
        if self.current_index < len(self.samples) - 1:
            self.point_list.setCurrentRow(self.current_index + 1)
        else:
            self._finish()

    def _previous(self) -> None:
        if self.current_index > 0:
            self.point_list.setCurrentRow(self.current_index - 1)

    def _add_point(self) -> None:
        name, ok = QInputDialog.getText(self, "Calibration point", "Name:")
        if not ok or not name.strip():
            return
        x, ok = QInputDialog.getDouble(self, "Calibration point", "X, mm:", 0, -1000000, 1000000, 1)
        if not ok:
            return
        y, ok = QInputDialog.getDouble(self, "Calibration point", "Y, mm:", 0, -1000000, 1000000, 1)
        if not ok:
            return
        self.samples.append(CalibrationSample(name.strip(), x, y, self.main_window.current_pan, self.main_window.current_tilt))
        self.current_index = len(self.samples) - 1
        self._populate_points()

    def _reset_to_cross(self) -> None:
        radius = self.radius_spin.value()
        self.samples = make_cross_calibration_points(radius, self.main_window.current_pan, self.main_window.current_tilt)
        self.saved_indexes.clear()
        self.current_index = 0
        self._apply_samples()
        self._populate_points()
        self._select_index(0)

    def _remove_point(self) -> None:
        if len(self.samples) <= 1:
            QMessageBox.information(self, "Calibration", "Keep at least one calibration point.")
            return
        del self.samples[self.current_index]
        self.saved_indexes = {i if i < self.current_index else i - 1 for i in self.saved_indexes if i != self.current_index}
        self.current_index = min(self.current_index, len(self.samples) - 1)
        self._apply_samples()
        self._populate_points()

    def _apply_samples(self) -> None:
        self.main_window.project.calibration = [CalibrationSample(s.name, s.x_mm, s.y_mm, s.pan, s.tilt) for s in self.samples]
        self.main_window.calibration.set_samples(self.main_window.project.calibration)
        self.main_window._populate_calibration()

    def _finish(self) -> None:
        self._apply_samples()
        self.main_window.emergency_laser_off()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Laser Director")
        self.resize(1440, 900)
        self.project = Project(name="Untitled")
        self.project_file: Path | None = None
        self.fixture = DmxFixture(self.project.fixture)
        self.artnet = ArtNetSender(self.project.fixture.artnet_ip, self.project.fixture.artnet_port, self.project.fixture.universe)
        self.calibration = CalibrationModel(self.project.calibration)
        self.current_pan = 32768
        self.current_tilt = 32768
        self.point_queue: list[tuple[float, float]] = []
        self.point_index = 0
        self._building_ui = False

        self.plan_view = PlanView()
        self.plan_view.pointClicked.connect(self.goto_point)
        self.plan_view.objectSelected.connect(self.select_object_in_list)
        self.object_list = QListWidget()
        self.object_list.itemSelectionChanged.connect(self._object_list_changed)

        self._build_actions()
        self._build_layout()
        self._bind_shortcuts()
        self._refresh_all()

    def closeEvent(self, event) -> None:
        self.emergency_laser_off()
        self.artnet.close()
        super().closeEvent(event)

    def _build_actions(self) -> None:
        self.toolbar = QToolBar("Project", self)
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        actions = [
            ("New", self.new_project),
            ("Open Project", self.open_project),
            ("Save Project", self.save_project),
            ("Load DXF", self.load_dxf),
            ("Load PDF", self.load_pdf),
            ("Calibration Wizard", self.open_calibration_wizard),
        ]
        for title, slot in actions:
            action = QAction(title, self)
            action.triggered.connect(slot)
            self.toolbar.addAction(action)

    def _build_layout(self) -> None:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Objects / points"))
        left_layout.addWidget(self.object_list)
        center_buttons = QHBoxLayout()
        btn_center = QPushButton("Show selected center")
        btn_center.clicked.connect(self.show_selected_center)
        btn_corners = QPushButton("Show selected corners")
        btn_corners.clicked.connect(self.show_selected_corners)
        center_buttons.addWidget(btn_center)
        center_buttons.addWidget(btn_corners)
        left_layout.addLayout(center_buttons)
        nav = QHBoxLayout()
        prev_btn = QPushButton("Previous point")
        prev_btn.clicked.connect(self.previous_point)
        next_btn = QPushButton("Next point")
        next_btn.clicked.connect(self.next_point)
        nav.addWidget(prev_btn)
        nav.addWidget(next_btn)
        left_layout.addLayout(nav)

        right = self._make_control_panel()
        self.setCentralWidget(self.plan_view)
        self.objects_dock = self._make_floating_dock("Objects", left, 320, 620)
        self.controls_dock = self._make_floating_dock("Controls", right, 380, 760)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.objects_dock.toggleViewAction())
        self.toolbar.addAction(self.controls_dock.toggleViewAction())

    def _make_floating_dock(self, title: str, widget: QWidget, width: int, height: int) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.setFloating(True)
        dock.resize(width, height)
        dock.hide()
        return dock

    def _make_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        artnet_box = QGroupBox("Art-Net / DMX")
        form = QFormLayout(artnet_box)
        self.ip_edit = QLineEdit()
        self.port_spin = self._spin(1, 65535, 6454)
        self.universe_spin = self._spin(0, 32767, 0)
        self.address_spin = self._spin(1, 512, 1)
        self.pan_channel_spin = self._spin(1, 512, 1)
        self.tilt_channel_spin = self._spin(1, 512, 3)
        self.laser_channel_spin = self._spin(1, 512, 5)
        self.mode_16bit = QCheckBox("16-bit pan/tilt")
        self.mode_16bit.setChecked(True)
        for widget in [self.ip_edit, self.port_spin, self.universe_spin, self.address_spin, self.pan_channel_spin, self.tilt_channel_spin, self.laser_channel_spin, self.mode_16bit]:
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self.apply_fixture_config)
            elif hasattr(widget, "textChanged"):
                widget.textChanged.connect(self.apply_fixture_config)
            else:
                widget.toggled.connect(self.apply_fixture_config)
        form.addRow("Controller IP", self.ip_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("Universe", self.universe_spin)
        form.addRow("DMX address", self.address_spin)
        form.addRow("Pan channel", self.pan_channel_spin)
        form.addRow("Tilt channel", self.tilt_channel_spin)
        form.addRow("Laser channel", self.laser_channel_spin)
        form.addRow(self.mode_16bit)
        layout.addWidget(artnet_box)

        range_box = QGroupBox("Pan / Tilt")
        range_form = QFormLayout(range_box)
        self.pan_spin = self._spin(0, 65535, self.current_pan)
        self.tilt_spin = self._spin(0, 65535, self.current_tilt)
        self.pan_min_spin = self._spin(0, 65535, 0)
        self.pan_max_spin = self._spin(0, 65535, 65535)
        self.tilt_min_spin = self._spin(0, 65535, 0)
        self.tilt_max_spin = self._spin(0, 65535, 65535)
        self.jog_step_spin = self._spin(1, 10000, 512)
        self.pan_spin.valueChanged.connect(self._manual_pan_tilt_changed)
        self.tilt_spin.valueChanged.connect(self._manual_pan_tilt_changed)
        for spin in [self.pan_min_spin, self.pan_max_spin, self.tilt_min_spin, self.tilt_max_spin]:
            spin.valueChanged.connect(self.apply_fixture_config)
        range_form.addRow("Pan value", self.pan_spin)
        range_form.addRow("Tilt value", self.tilt_spin)
        range_form.addRow("Pan min", self.pan_min_spin)
        range_form.addRow("Pan max", self.pan_max_spin)
        range_form.addRow("Tilt min", self.tilt_min_spin)
        range_form.addRow("Tilt max", self.tilt_max_spin)
        range_form.addRow("Jog step", self.jog_step_spin)
        jog = QGridLayout()
        up = QPushButton("Tilt +")
        down = QPushButton("Tilt -")
        left = QPushButton("Pan -")
        right = QPushButton("Pan +")
        up.clicked.connect(lambda: self.jog(0, 1))
        down.clicked.connect(lambda: self.jog(0, -1))
        left.clicked.connect(lambda: self.jog(-1, 0))
        right.clicked.connect(lambda: self.jog(1, 0))
        jog.addWidget(up, 0, 1)
        jog.addWidget(left, 1, 0)
        jog.addWidget(right, 1, 2)
        jog.addWidget(down, 2, 1)
        range_form.addRow(jog)
        layout.addWidget(range_box)

        laser_box = QGroupBox("Laser")
        laser_layout = QGridLayout(laser_box)
        laser_on = QPushButton("Laser ON")
        laser_off = QPushButton("Laser OFF")
        flash = QPushButton("Flash point 1 sec")
        emergency = QPushButton("EMERGENCY OFF")
        wizard = QPushButton("CALIBRATION WIZARD")
        emergency.setStyleSheet("font-weight: 700; color: white; background: #b00020;")
        wizard.setStyleSheet("font-weight: 700;")
        laser_on.clicked.connect(lambda: self.set_laser(True))
        laser_off.clicked.connect(lambda: self.set_laser(False))
        flash.clicked.connect(self.flash_point)
        emergency.clicked.connect(self.emergency_laser_off)
        wizard.clicked.connect(self.open_calibration_wizard)
        laser_layout.addWidget(laser_on, 0, 0)
        laser_layout.addWidget(laser_off, 0, 1)
        laser_layout.addWidget(flash, 1, 0, 1, 2)
        laser_layout.addWidget(wizard, 2, 0, 1, 2)
        laser_layout.addWidget(emergency, 3, 0, 1, 2)
        layout.addWidget(laser_box)

        layout.addStretch(1)
        return panel

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Space"), self, activated=self.flash_point)
        QShortcut(QKeySequence("Esc"), self, activated=self.emergency_laser_off)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def new_project(self) -> None:
        self.project = Project(name="Untitled")
        self.project_file = None
        self.plan_view.clear_plan()
        self._refresh_all()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Laser Director project (*.json)")
        if not path:
            return
        self.project_file = Path(path)
        self.project = load_project(path)
        self._refresh_all()
        plan_path = self.project.resolve_plan_path(self.project_file)
        if plan_path and plan_path.exists():
            if self.project.plan_type == "dxf":
                self.plan_view.load_dxf_objects(self.project.objects)
            elif self.project.plan_type == "pdf":
                self.plan_view.load_pdf(plan_path)
                if self.project.pdf_scale.real_distance_mm:
                    self.plan_view.restore_pdf_scale(self.project.pdf_scale)

    def save_project(self) -> None:
        if not self.project_file:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", "laser_project.json", "Laser Director project (*.json)")
            if not path:
                return
            self.project_file = Path(path)
        self._sync_project_from_ui()
        save_project(self.project, self.project_file)

    def load_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Vectorworks DXF", "", "DXF files (*.dxf)")
        if not path:
            return
        try:
            objects = read_dxf(path)
        except Exception as exc:
            QMessageBox.critical(self, "DXF error", str(exc))
            return
        self.project.objects = objects
        self.project.plan_path = path
        self.project.plan_type = "dxf"
        self.plan_view.load_dxf_objects(objects)
        self._populate_objects(objects)

    def load_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load PDF plan", "", "PDF files (*.pdf)")
        if not path:
            return
        try:
            self.plan_view.load_pdf(path)
        except Exception as exc:
            QMessageBox.critical(self, "PDF error", str(exc))
            return
        self.project.plan_path = path
        self.project.plan_type = "pdf"
        QMessageBox.information(self, "PDF scale", "Shift-click two points on the PDF, then enter their real distance in millimeters.")

    def apply_fixture_config(self) -> None:
        if self._building_ui:
            return
        self._sync_project_from_ui()
        maximum = 65535 if self.project.fixture.pan_tilt_16bit else 255
        self.pan_spin.setRange(0, maximum)
        self.tilt_spin.setRange(0, maximum)
        self.fixture.update_config(self.project.fixture)
        self.artnet.configure(self.project.fixture.artnet_ip, self.project.fixture.artnet_port, self.project.fixture.universe)
        self.send_current_dmx()

    def _manual_pan_tilt_changed(self) -> None:
        if self._building_ui:
            return
        self.current_pan = self.pan_spin.value()
        self.current_tilt = self.tilt_spin.value()
        self.fixture.set_pan_tilt(self.current_pan, self.current_tilt)
        self.send_current_dmx()

    def jog(self, pan_dir: int, tilt_dir: int) -> None:
        step = self.jog_step_spin.value()
        self.set_pan_tilt_values(self.pan_spin.value() + pan_dir * step, self.tilt_spin.value() + tilt_dir * step)

    def set_pan_tilt_values(self, pan: int, tilt: int) -> None:
        pan, tilt = self.fixture.clamp_pan_tilt(pan, tilt)
        self.current_pan = pan
        self.current_tilt = tilt
        self._building_ui = True
        self.pan_spin.setValue(pan)
        self.tilt_spin.setValue(tilt)
        self._building_ui = False
        self.fixture.set_pan_tilt(pan, tilt)
        self.send_current_dmx()

    def set_laser(self, enabled: bool) -> None:
        self.fixture.set_laser(enabled)
        self.send_current_dmx()

    def emergency_laser_off(self) -> None:
        self.fixture.laser_off()
        self.send_current_dmx()

    def flash_point(self) -> None:
        self.set_laser(True)
        QTimer.singleShot(1000, self.emergency_laser_off)

    def goto_point(self, x_mm: float, y_mm: float) -> None:
        try:
            pan, tilt = self.calibration.interpolate(x_mm, y_mm)
        except ValueError:
            QMessageBox.warning(self, "Calibration required", "Add at least one calibration point before clicking the plan.")
            return
        self.current_pan, self.current_tilt = self.fixture.clamp_pan_tilt(pan, tilt)
        self._building_ui = True
        self.pan_spin.setValue(self.current_pan)
        self.tilt_spin.setValue(self.current_tilt)
        self._building_ui = False
        self.fixture.set_pan_tilt(self.current_pan, self.current_tilt)
        self.plan_view.show_target(x_mm, y_mm)
        self.send_current_dmx()

    def open_calibration_wizard(self) -> None:
        dialog = CalibrationWizardDialog(self)
        dialog.exec()

    def show_selected_center(self) -> None:
        obj = self.plan_view.selected_object()
        if not obj:
            return
        center = obj.bbox_center()
        if center:
            self.point_queue = [center]
            self.point_index = 0
            self.goto_point(*center)

    def show_selected_corners(self) -> None:
        obj = self.plan_view.selected_object()
        if not obj:
            return
        self.point_queue = obj.corners()
        self.point_index = 0
        if self.point_queue:
            self.goto_point(*self.point_queue[0])

    def next_point(self) -> None:
        if not self.point_queue:
            return
        self.point_index = (self.point_index + 1) % len(self.point_queue)
        self.goto_point(*self.point_queue[self.point_index])

    def previous_point(self) -> None:
        if not self.point_queue:
            return
        self.point_index = (self.point_index - 1) % len(self.point_queue)
        self.goto_point(*self.point_queue[self.point_index])

    def select_object_in_list(self, obj: PlanObject | None) -> None:
        if not obj:
            return
        for i in range(self.object_list.count()):
            item = self.object_list.item(i)
            if item.data(Qt.UserRole) == obj.id:
                self.object_list.blockSignals(True)
                self.object_list.setCurrentItem(item)
                self.object_list.blockSignals(False)
                break

    def _object_list_changed(self) -> None:
        item = self.object_list.currentItem()
        if not item:
            return
        target_id = item.data(Qt.UserRole)
        for scene_item in self.plan_view.scene.items():
            if hasattr(scene_item, "plan_object"):
                scene_item.setSelected(scene_item.plan_object.id == target_id)

    def send_current_dmx(self) -> None:
        try:
            self.artnet.send_dmx(self.fixture.levels)
        except OSError as exc:
            self.statusBar().showMessage(f"Art-Net send failed: {exc}", 5000)

    def _refresh_all(self) -> None:
        self._building_ui = True
        cfg = self.project.fixture
        self.ip_edit.setText(cfg.artnet_ip)
        self.port_spin.setValue(cfg.artnet_port)
        self.universe_spin.setValue(cfg.universe)
        self.address_spin.setValue(cfg.address)
        self.pan_channel_spin.setValue(cfg.pan_channel)
        self.tilt_channel_spin.setValue(cfg.tilt_channel)
        self.laser_channel_spin.setValue(cfg.laser_channel)
        self.mode_16bit.setChecked(cfg.pan_tilt_16bit)
        self.pan_min_spin.setValue(cfg.pan_min)
        self.pan_max_spin.setValue(cfg.pan_max)
        self.tilt_min_spin.setValue(cfg.tilt_min)
        self.tilt_max_spin.setValue(cfg.tilt_max)
        self.pan_spin.setRange(0, 65535 if cfg.pan_tilt_16bit else 255)
        self.tilt_spin.setRange(0, 65535 if cfg.pan_tilt_16bit else 255)
        self.pan_spin.setValue(min(self.current_pan, self.pan_spin.maximum()))
        self.tilt_spin.setValue(min(self.current_tilt, self.tilt_spin.maximum()))
        self._building_ui = False
        if not self.project.calibration:
            self.project.calibration = make_cross_calibration_points(1000, self.current_pan, self.current_tilt)
        self.calibration.set_samples(self.project.calibration)
        self.fixture.update_config(cfg)
        self.artnet.configure(cfg.artnet_ip, cfg.artnet_port, cfg.universe)
        self._populate_objects(self.project.objects)

    def _populate_objects(self, objects: list[PlanObject]) -> None:
        self.object_list.clear()
        for obj in objects:
            item = QListWidgetItem(obj.name)
            item.setData(Qt.UserRole, obj.id)
            self.object_list.addItem(item)

    def _populate_calibration(self) -> None:
        self.calibration.set_samples(self.project.calibration)

    def _sync_project_from_ui(self) -> None:
        cfg = FixtureConfig(
            artnet_ip=self.ip_edit.text().strip() or "2.255.255.255",
            artnet_port=self.port_spin.value(),
            universe=self.universe_spin.value(),
            address=self.address_spin.value(),
            pan_channel=self.pan_channel_spin.value(),
            tilt_channel=self.tilt_channel_spin.value(),
            laser_channel=self.laser_channel_spin.value(),
            pan_min=self.pan_min_spin.value(),
            pan_max=self.pan_max_spin.value(),
            tilt_min=self.tilt_min_spin.value(),
            tilt_max=self.tilt_max_spin.value(),
            pan_tilt_16bit=self.mode_16bit.isChecked(),
        )
        self.project.fixture = cfg
        self.project.pdf_scale = self.plan_view.pdf_scale



def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Laser Director")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
