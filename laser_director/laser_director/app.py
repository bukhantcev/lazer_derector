from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
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
    QScrollArea,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .artnet import ArtNetSender
from .calibration import (
    CalibrationModel,
    equivalent_pan_values,
    fit_geometric_model,
    pan_turn_period_dmx,
    recommended_pan_branch,
)
from .dmx_fixture import DmxFixture
from . import __build__, __version__
from .models import CalibrationSample, ExtraDmxChannel, FixtureConfig, PlanObject, Project
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


MINI_LASER_13CH_EXTRA_CHANNELS = [
    ExtraDmxChannel(channel=3, level=128, name="XY speed"),
    ExtraDmxChannel(channel=4, level=0, name="Z rotation off"),
    ExtraDmxChannel(channel=6, level=0, name="Strobe off"),
    ExtraDmxChannel(channel=7, level=255, name="Red"),
    ExtraDmxChannel(channel=8, level=255, name="Green"),
    ExtraDmxChannel(channel=9, level=255, name="Blue"),
    ExtraDmxChannel(channel=10, level=255, name="White"),
    ExtraDmxChannel(channel=11, level=255, name="Green laser"),
    ExtraDmxChannel(channel=12, level=0, name="Macro off"),
    ExtraDmxChannel(channel=13, level=0, name="Reset off"),
]


def make_cross_calibration_points(radius_mm: int, pan: int, tilt: int) -> list[CalibrationSample]:
    return [
        CalibrationSample("center", 0, 0, pan, tilt),
        CalibrationSample(f"left_{radius_mm}mm", -radius_mm, 0, pan, tilt),
        CalibrationSample(f"right_{radius_mm}mm", radius_mm, 0, pan, tilt),
        CalibrationSample(f"front_{radius_mm}mm", 0, -radius_mm, pan, tilt),
        CalibrationSample(f"back_{radius_mm}mm", 0, radius_mm, pan, tilt),
    ]


def normalize_calibration_samples(samples: list[CalibrationSample], fixture: FixtureConfig) -> list[CalibrationSample]:
    if fixture.pan_tilt_16bit:
        return samples
    normalized: list[CalibrationSample] = []
    for sample in samples:
        pan = sample.pan
        tilt = sample.tilt
        if pan > 255:
            pan = round(pan / 257)
        if tilt > 255:
            tilt = round(tilt / 257)
        normalized.append(
            CalibrationSample(
                sample.name,
                sample.x_mm,
                sample.y_mm,
                max(0, min(255, pan)),
                max(0, min(255, tilt)),
            )
        )
    return normalized


def calibration_samples_have_movement(samples: list[CalibrationSample]) -> bool:
    if len(samples) < 2:
        return False
    pans = [sample.pan for sample in samples]
    tilts = [sample.tilt for sample in samples]
    return max(pans) - min(pans) > 1 or max(tilts) - min(tilts) > 1


class CalibrationWizardDialog(QDialog):
    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.samples = self._initial_samples()
        self.current_index = 0
        self.saved_indexes: set[int] = set(range(len(self.samples))) if calibration_samples_have_movement(self.samples) else set()
        self._syncing = False

        self.setWindowTitle("Calibration Wizard")
        self.resize(720, 520)
        self._build_ui()
        self._fit_to_screen()
        self._populate_points()
        self._select_index(0)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        header = QLabel("Point-by-point calibration")
        header.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        body = QHBoxLayout(content)
        root.addWidget(scroll, 1)

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

        branch_box = QGroupBox("Pan rotation")
        branch_layout = QVBoxLayout(branch_box)
        self.branch_lock = QCheckBox("Keep one Pan rotation")
        self.branch_lock.setChecked(True)
        self.branch_lock.toggled.connect(self._refresh_branch_status)
        self.branch_status = QLabel()
        self.branch_status.setWordWrap(True)
        self.use_branch_btn = QPushButton("Use recommended Pan turn")
        self.use_branch_btn.clicked.connect(self._use_recommended_branch)
        self.verify_btn = QPushButton("Verify saved point")
        self.verify_btn.clicked.connect(self._verify_current_point)
        branch_buttons = QHBoxLayout()
        branch_buttons.addWidget(self.use_branch_btn)
        branch_buttons.addWidget(self.verify_btn)
        branch_layout.addWidget(self.branch_lock)
        branch_layout.addWidget(self.branch_status)
        branch_layout.addLayout(branch_buttons)
        right.addWidget(branch_box)

        geometry_box = QGroupBox("Auto geometry")
        geometry_form = QFormLayout(geometry_box)
        self.pan_range_spin = QDoubleSpinBox()
        self.pan_range_spin.setRange(1, 1080)
        self.pan_range_spin.setValue(self.main_window.project.geometry.pan_range_deg)
        self.pan_range_spin.setSuffix(" deg")
        self.pan_range_spin.valueChanged.connect(self._refresh_branch_status)
        self.tilt_range_spin = QDoubleSpinBox()
        self.tilt_range_spin.setRange(1, 540)
        self.tilt_range_spin.setValue(self.main_window.project.geometry.tilt_range_deg)
        self.tilt_range_spin.setSuffix(" deg")
        fit_btn = QPushButton("Fit head from saved points")
        fit_btn.setStyleSheet("font-weight: 700;")
        fit_btn.clicked.connect(self._fit_geometry)
        self.fit_status = QLabel("Use the center cross. Coordinates come from the plan, not from walking the whole stage.")
        self.fit_status.setWordWrap(True)
        geometry_form.addRow("Pan range", self.pan_range_spin)
        geometry_form.addRow("Tilt range", self.tilt_range_spin)
        geometry_form.addRow(fit_btn)
        geometry_form.addRow(self.fit_status)
        right.addWidget(geometry_box)

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

    def _fit_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        self.resize(min(self.width(), available.width() - 80), min(self.height(), available.height() - 100))

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(max(minimum, min(maximum, value)))
        return spin

    def _initial_samples(self) -> list[CalibrationSample]:
        existing = self.main_window.project.calibration
        if not existing:
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
        if index in self.saved_indexes:
            self.main_window.set_pan_tilt_values(sample.pan, sample.tilt)
        self._syncing = True
        self.pan_spin.setValue(self.main_window.current_pan)
        self.tilt_spin.setValue(self.main_window.current_tilt)
        self._syncing = False
        self._refresh_branch_status()

    def _manual_values_changed(self) -> None:
        if self._syncing:
            return
        self.main_window.set_pan_tilt_values(self.pan_spin.value(), self.tilt_spin.value())
        self._refresh_branch_status()

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
        self._refresh_branch_status()

    def _dmx_max(self) -> int:
        return 65535 if self.main_window.project.fixture.pan_tilt_16bit else 255

    def _saved_samples_except_current(self) -> list[CalibrationSample]:
        return [
            sample
            for index, sample in enumerate(self.samples)
            if index in self.saved_indexes and index != self.current_index
        ]

    def _expected_pan(self) -> float | None:
        sample = self.samples[self.current_index]
        saved = self._saved_samples_except_current()
        if not saved:
            return None
        ranked: list[tuple[float, CalibrationSample]] = []
        for item in saved:
            distance = ((item.x_mm - sample.x_mm) ** 2 + (item.y_mm - sample.y_mm) ** 2) ** 0.5
            if distance < 1e-6:
                return float(item.pan)
            ranked.append((distance, item))
        ranked.sort(key=lambda value: value[0])
        selected = ranked[: min(4, len(ranked))]
        weights = [1.0 / max(1.0, distance**2) for distance, _ in selected]
        weight_sum = sum(weights)
        return sum(weight * item.pan for weight, (_, item) in zip(weights, selected)) / weight_sum

    def _recommended_pan(self) -> int:
        return recommended_pan_branch(
            self.main_window.current_pan,
            self._dmx_max(),
            self.pan_range_spin.value(),
            self._expected_pan(),
        )

    def _refresh_branch_status(self) -> None:
        if not hasattr(self, "branch_status") or not self.samples:
            return
        current = self.main_window.current_pan
        maximum = self._dmx_max()
        period = pan_turn_period_dmx(maximum, self.pan_range_spin.value())
        candidates = equivalent_pan_values(current, maximum, self.pan_range_spin.value())
        recommended = self._recommended_pan()
        expected = self._expected_pan()
        margin = min(current, maximum - current)
        if recommended != current:
            self.branch_status.setText(
                f"Recommended Pan {recommended}; current {current} is another 360 deg turn. "
                f"One turn is {period:.1f} DMX."
            )
            self.branch_status.setStyleSheet("color: #b54708; font-weight: 600;")
            self.use_branch_btn.setEnabled(True)
            return

        possible_flip = False
        if expected is not None:
            half_turn = period / 2
            possible_flip = abs(abs(current - expected) - half_turn) < max(4.0, half_turn * 0.18)
        if possible_flip:
            self.branch_status.setText(
                f"Possible head-flip branch: Pan differs from nearby points by about 180 deg. "
                f"Verify the saved point before continuing."
            )
            self.branch_status.setStyleSheet("color: #b54708; font-weight: 600;")
        else:
            options = ", ".join(str(value) for value in candidates)
            self.branch_status.setText(
                f"Branch OK. Pan margin {margin}; equivalent full-turn values: {options}."
            )
            self.branch_status.setStyleSheet("color: #18794e;")
        self.use_branch_btn.setEnabled(False)

    def _use_recommended_branch(self) -> None:
        recommended = self._recommended_pan()
        if recommended == self.main_window.current_pan:
            return
        self.main_window.set_pan_tilt_values(recommended, self.main_window.current_tilt)
        self._syncing = True
        self.pan_spin.setValue(self.main_window.current_pan)
        self.tilt_spin.setValue(self.main_window.current_tilt)
        self._syncing = False
        self._refresh_branch_status()
        self.main_window.statusBar().showMessage(
            "Moved to the recommended full-turn equivalent. Verify the laser point, then save.",
            5000,
        )

    def _verify_current_point(self) -> None:
        if self.current_index not in self.saved_indexes:
            QMessageBox.information(self, "Verify point", "Save this calibration point first.")
            return
        sample = self.samples[self.current_index]
        self.main_window.set_pan_tilt_values(sample.pan, sample.tilt)
        self._syncing = True
        self.pan_spin.setValue(sample.pan)
        self.tilt_spin.setValue(sample.tilt)
        self._syncing = False
        self.main_window.flash_point()
        self._refresh_branch_status()

    def _save_current(self) -> bool:
        sample = self.samples[self.current_index]
        if self.branch_lock.isChecked():
            recommended = self._recommended_pan()
            if recommended != self.main_window.current_pan:
                answer = QMessageBox.question(
                    self,
                    "Different Pan rotation",
                    f"Current Pan {self.main_window.current_pan} is on another 360 deg turn. "
                    f"Move to equivalent Pan {recommended} before saving?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if answer == QMessageBox.Yes:
                    self._use_recommended_branch()
                    return False
        sample.pan = self.main_window.current_pan
        sample.tilt = self.main_window.current_tilt
        self.saved_indexes.add(self.current_index)
        self._apply_samples()
        self._populate_points()
        self.point_list.setCurrentRow(self.current_index)
        self.main_window.statusBar().showMessage(f"Saved calibration point: {sample.name}", 2500)
        self._refresh_branch_status()
        return True

    def _save_and_next(self) -> None:
        if not self._save_current():
            return
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
        self.main_window.project.calibration = [
            CalibrationSample(s.name, s.x_mm, s.y_mm, s.pan, s.tilt)
            for index, s in enumerate(self.samples)
            if index in self.saved_indexes
        ]
        self.main_window.calibration.set_samples(self.main_window.project.calibration)
        self.main_window._populate_calibration()

    def _calibration_conflict(self) -> str | None:
        saved = [sample for index, sample in enumerate(self.samples) if index in self.saved_indexes]
        for index, first in enumerate(saved):
            for second in saved[index + 1 :]:
                coordinate_distance = ((first.x_mm - second.x_mm) ** 2 + (first.y_mm - second.y_mm) ** 2) ** 0.5
                if coordinate_distance < 1.0:
                    continue
                if abs(first.pan - second.pan) <= 1 and abs(first.tilt - second.tilt) <= 1:
                    return (
                        f"{first.name} and {second.name} are different stage points but both use "
                        f"Pan {first.pan}, Tilt {first.tilt}. Re-aim and save one of them."
                    )
        return None

    def _fit_geometry(self) -> bool:
        self._apply_samples()
        if len(self.main_window.project.calibration) < 5:
            QMessageBox.warning(self, "Auto geometry", "Save at least five calibration points before fitting the head.")
            return False
        conflict = self._calibration_conflict()
        if conflict:
            QMessageBox.warning(self, "Calibration conflict", conflict)
            self.fit_status.setText(conflict)
            self.fit_status.setStyleSheet("color: #b42318; font-weight: 600;")
            return False
        self.main_window.project.geometry.pan_range_deg = self.pan_range_spin.value()
        self.main_window.project.geometry.tilt_range_deg = self.tilt_range_spin.value()
        dmx_max = 65535 if self.main_window.project.fixture.pan_tilt_16bit else 255
        try:
            fitted = fit_geometric_model(
                self.main_window.project.calibration,
                self.main_window.project.geometry,
                dmx_max,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Auto geometry", str(exc))
            return False
        self.main_window.project.geometry = fitted
        self.main_window.calibration.set_geometry(fitted)
        fit_error = fitted.fit_error_mm or 0
        self.fit_status.setText(
            f"Fitted head: X={fitted.head_x_mm:.0f}, Y={fitted.head_y_mm:.0f}, "
            f"Z={fitted.head_z_mm:.0f} mm. Error {fit_error:.0f} mm."
        )
        if fit_error > self.radius_spin.value() * 0.25:
            self.fit_status.setStyleSheet("color: #b54708; font-weight: 600;")
        else:
            self.fit_status.setStyleSheet("color: #18794e; font-weight: 600;")
        self.main_window.refresh_geometry_status()
        self.main_window.statusBar().showMessage("Auto geometry fitted", 5000)
        return True

    def _finish(self) -> None:
        self._apply_samples()
        if not self._fit_geometry():
            return
        self.main_window.emergency_laser_off()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Laser Director {__version__} [{__build__}]")
        self.resize(1440, 900)
        self.project = Project(name="Untitled")
        self.project_file: Path | None = None
        self.fixture = DmxFixture(self.project.fixture)
        self.artnet = ArtNetSender(self.project.fixture.artnet_ip, self.project.fixture.artnet_port, self.project.fixture.universe)
        self.calibration = CalibrationModel(self.project.calibration)
        self.extra_channel_rows: list[tuple[QWidget, QLineEdit, QSpinBox, QSpinBox]] = []
        self.current_pan = 128
        self.current_tilt = 128
        self.point_queue: list[tuple[float, float]] = []
        self.point_index = 0
        self._building_ui = False
        self.follow_cursor_enabled = False
        self._pending_follow_point: tuple[float, float] | None = None
        self._calibration_warning_shown = False

        self.plan_view = PlanView()
        self.plan_view.pointClicked.connect(self.goto_point)
        self.plan_view.pointHovered.connect(self.follow_cursor_point)
        self.plan_view.objectSelected.connect(self.select_object_in_list)
        self.object_list = QListWidget()
        self.object_list.itemSelectionChanged.connect(self._object_list_changed)
        self.follow_cursor_timer = QTimer(self)
        self.follow_cursor_timer.setInterval(40)
        self.follow_cursor_timer.timeout.connect(self._flush_follow_cursor)

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
        self.toolbar.addSeparator()
        self.follow_cursor_action = QAction("Follow Cursor", self)
        self.follow_cursor_action.setCheckable(True)
        self.follow_cursor_action.toggled.connect(self.set_follow_cursor_enabled)
        self.toolbar.addAction(self.follow_cursor_action)

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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        dock.setWidget(scroll)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.setFloating(True)
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            width = min(width, max(280, available.width() - 80))
            height = min(height, max(420, available.height() - 100))
            dock.move(available.x() + 40, available.y() + 60)
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
        self.mode_16bit.setChecked(False)
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
        form.addRow("Dimmer channel", self.laser_channel_spin)
        form.addRow(self.mode_16bit)
        preset_13ch = QPushButton("Apply 13CH laser manual")
        preset_13ch.clicked.connect(self.apply_mini_laser_13ch_preset)
        form.addRow(preset_13ch)
        apply_settings = QPushButton("APPLY / SEND SETTINGS")
        apply_settings.setMinimumHeight(42)
        apply_settings.setStyleSheet("font-weight: 700;")
        apply_settings.clicked.connect(self.apply_fixture_config)
        self.fixture_status = QLabel("Settings not applied yet")
        self.fixture_status.setWordWrap(True)
        form.addRow(apply_settings)
        form.addRow(self.fixture_status)
        layout.addWidget(artnet_box)

        extra_box = QGroupBox("Extra DMX channels")
        extra_layout = QVBoxLayout(extra_box)
        self.extra_channels_layout = QVBoxLayout()
        extra_layout.addLayout(self.extra_channels_layout)
        add_extra = QPushButton("Add channel")
        add_extra.clicked.connect(lambda: self.add_extra_channel_row())
        extra_layout.addWidget(add_extra)
        layout.addWidget(extra_box)

        range_box = QGroupBox("Pan / Tilt")
        range_form = QFormLayout(range_box)
        self.pan_spin = self._spin(0, 255, min(self.current_pan, 255))
        self.tilt_spin = self._spin(0, 255, min(self.current_tilt, 255))
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
        self.dimmer_level_spin = self._spin(0, 255, 255)
        self.blackout_level_spin = self._spin(0, 255, 0)
        self.dimmer_level_spin.valueChanged.connect(self.apply_fixture_config)
        self.blackout_level_spin.valueChanged.connect(self.apply_fixture_config)
        laser_on = QPushButton("Laser ON")
        laser_off = QPushButton("Laser OFF")
        flash = QPushButton("Flash point 1 sec")
        full_test = QPushButton("FULL LASER TEST")
        emergency = QPushButton("EMERGENCY OFF")
        wizard = QPushButton("CALIBRATION WIZARD")
        self.follow_cursor_checkbox = QCheckBox("Follow cursor")
        emergency.setStyleSheet("font-weight: 700; color: white; background: #b00020;")
        full_test.setStyleSheet("font-weight: 700;")
        wizard.setStyleSheet("font-weight: 700;")
        laser_on.clicked.connect(lambda: self.set_laser(True))
        laser_off.clicked.connect(lambda: self.set_laser(False))
        flash.clicked.connect(self.flash_point)
        full_test.clicked.connect(self.full_laser_test)
        emergency.clicked.connect(self.emergency_laser_off)
        wizard.clicked.connect(self.open_calibration_wizard)
        self.follow_cursor_checkbox.toggled.connect(self.set_follow_cursor_enabled)
        laser_layout.addWidget(QLabel("Dimmer level"), 0, 0)
        laser_layout.addWidget(self.dimmer_level_spin, 0, 1)
        laser_layout.addWidget(QLabel("Blackout level"), 1, 0)
        laser_layout.addWidget(self.blackout_level_spin, 1, 1)
        laser_layout.addWidget(self.follow_cursor_checkbox, 2, 0, 1, 2)
        laser_layout.addWidget(laser_on, 3, 0)
        laser_layout.addWidget(laser_off, 3, 1)
        laser_layout.addWidget(flash, 4, 0, 1, 2)
        laser_layout.addWidget(full_test, 5, 0, 1, 2)
        laser_layout.addWidget(wizard, 6, 0, 1, 2)
        laser_layout.addWidget(emergency, 7, 0, 1, 2)
        layout.addWidget(laser_box)

        geometry_box = QGroupBox("Aiming model")
        geometry_form = QFormLayout(geometry_box)
        self.geometry_enabled = QCheckBox("Use fitted geometry")
        self.geometry_enabled.setChecked(True)
        self.geometry_enabled.toggled.connect(self.apply_fixture_config)
        self.pan_range_control = QDoubleSpinBox()
        self.pan_range_control.setRange(1, 1080)
        self.pan_range_control.setValue(540)
        self.pan_range_control.setSuffix(" deg")
        self.tilt_range_control = QDoubleSpinBox()
        self.tilt_range_control.setRange(1, 540)
        self.tilt_range_control.setValue(270)
        self.tilt_range_control.setSuffix(" deg")
        self.pan_range_control.valueChanged.connect(self.apply_fixture_config)
        self.tilt_range_control.valueChanged.connect(self.apply_fixture_config)
        self.geometry_status = QLabel("Not fitted")
        self.geometry_status.setWordWrap(True)
        geometry_form.addRow(self.geometry_enabled)
        geometry_form.addRow("Pan range", self.pan_range_control)
        geometry_form.addRow("Tilt range", self.tilt_range_control)
        geometry_form.addRow(self.geometry_status)
        layout.addWidget(geometry_box)

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

    def add_extra_channel_row(self, item: ExtraDmxChannel | None = None) -> None:
        channel = item.channel if item else 1
        level = item.level if item else 0
        name = item.name if item else ""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("Name")
        channel_spin = self._spin(1, 512, channel)
        level_spin = self._spin(0, 255, level)
        remove_btn = QPushButton("Remove")
        row_layout.addWidget(name_edit, 2)
        row_layout.addWidget(QLabel("Ch"))
        row_layout.addWidget(channel_spin)
        row_layout.addWidget(QLabel("Level"))
        row_layout.addWidget(level_spin)
        row_layout.addWidget(remove_btn)
        self.extra_channels_layout.addWidget(row)
        self.extra_channel_rows.append((row, name_edit, channel_spin, level_spin))

        name_edit.textChanged.connect(self.apply_fixture_config)
        channel_spin.valueChanged.connect(self.apply_fixture_config)
        level_spin.valueChanged.connect(self.apply_fixture_config)
        remove_btn.clicked.connect(lambda: self.remove_extra_channel_row(row))

    def remove_extra_channel_row(self, row: QWidget) -> None:
        self.extra_channel_rows = [item for item in self.extra_channel_rows if item[0] is not row]
        row.setParent(None)
        row.deleteLater()
        self.apply_fixture_config()

    def populate_extra_channel_rows(self) -> None:
        for row, _, _, _ in self.extra_channel_rows:
            row.setParent(None)
            row.deleteLater()
        self.extra_channel_rows = []
        for item in self.project.fixture.extra_channels:
            self.add_extra_channel_row(item)

    def read_extra_channel_rows(self) -> list[ExtraDmxChannel]:
        channels: list[ExtraDmxChannel] = []
        for _, name_edit, channel_spin, level_spin in self.extra_channel_rows:
            channels.append(
                ExtraDmxChannel(
                    channel=channel_spin.value(),
                    level=level_spin.value(),
                    name=name_edit.text().strip(),
                )
            )
        return channels

    def apply_mini_laser_13ch_preset(self) -> None:
        self._building_ui = True
        self.pan_channel_spin.setValue(1)
        self.tilt_channel_spin.setValue(2)
        self.laser_channel_spin.setValue(5)
        self.dimmer_level_spin.setValue(255)
        self.blackout_level_spin.setValue(0)
        self.mode_16bit.setChecked(False)
        self.pan_min_spin.setValue(0)
        self.pan_max_spin.setValue(255)
        self.tilt_min_spin.setValue(0)
        self.tilt_max_spin.setValue(255)
        self._building_ui = False
        self.project.fixture.extra_channels = [
            ExtraDmxChannel(channel=item.channel, level=item.level, name=item.name)
            for item in MINI_LASER_13CH_EXTRA_CHANNELS
        ]
        self.populate_extra_channel_rows()
        self.apply_fixture_config()
        self.statusBar().showMessage("13CH fixture settings applied and sent", 5000)

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
        self.project.calibration = normalize_calibration_samples(self.project.calibration, self.project.fixture)
        self.calibration.set_samples(self.project.calibration)
        maximum = 65535 if self.project.fixture.pan_tilt_16bit else 255
        self.calibration.set_dmx_max(maximum)
        self.pan_spin.setRange(0, maximum)
        self.tilt_spin.setRange(0, maximum)
        self.fixture.update_config(self.project.fixture)
        self.fixture.apply_extra_channels()
        self.artnet.configure(self.project.fixture.artnet_ip, self.project.fixture.artnet_port, self.project.fixture.universe)
        self.send_current_dmx()
        self.refresh_geometry_status()
        self.refresh_fixture_status()

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
        self.refresh_fixture_status()

    def emergency_laser_off(self) -> None:
        self.fixture.laser_off()
        self.send_current_dmx()
        self.refresh_fixture_status()

    def flash_point(self) -> None:
        self.set_laser(True)
        QTimer.singleShot(1000, self.emergency_laser_off)

    def full_laser_test(self) -> None:
        self.apply_mini_laser_13ch_preset()
        self.set_pan_tilt_values(128, 128)
        self.set_laser(True)
        self.statusBar().showMessage("Full laser test sent: dimmer and green laser at 255", 5000)

    def goto_point(self, x_mm: float, y_mm: float) -> None:
        self.aim_at_point(x_mm, y_mm, warn=True)

    def aim_at_point(self, x_mm: float, y_mm: float, warn: bool = False) -> bool:
        geometry = self.project.geometry
        if not (geometry.enabled and geometry.fitted) and not calibration_samples_have_movement(self.project.calibration):
            message = "Calibration has no pan/tilt movement yet. Open Calibration Wizard and save several real aimed points."
            if warn:
                QMessageBox.warning(self, "Calibration required", message)
            elif not self._calibration_warning_shown:
                self.statusBar().showMessage(message, 5000)
                self._calibration_warning_shown = True
            return False
        try:
            pan, tilt = self.calibration.interpolate(x_mm, y_mm)
        except ValueError as exc:
            if warn:
                QMessageBox.warning(self, "Calibration required", "Run Calibration Wizard before aiming at points on the plan.")
            elif not self._calibration_warning_shown:
                self.statusBar().showMessage(f"Calibration required: {exc}", 5000)
                self._calibration_warning_shown = True
            return False
        self._calibration_warning_shown = False
        self.set_pan_tilt_values(pan, tilt)
        self.plan_view.show_target(x_mm, y_mm)
        self.statusBar().showMessage(f"Target X {x_mm:.0f} mm, Y {y_mm:.0f} mm -> pan {self.current_pan}, tilt {self.current_tilt}", 1500)
        return True

    def set_follow_cursor_enabled(self, enabled: bool) -> None:
        if self.follow_cursor_enabled == enabled:
            return
        self.follow_cursor_enabled = enabled
        self.follow_cursor_action.blockSignals(True)
        self.follow_cursor_action.setChecked(enabled)
        self.follow_cursor_action.blockSignals(False)
        if hasattr(self, "follow_cursor_checkbox"):
            self.follow_cursor_checkbox.blockSignals(True)
            self.follow_cursor_checkbox.setChecked(enabled)
            self.follow_cursor_checkbox.blockSignals(False)
        if enabled:
            self.follow_cursor_timer.start()
            self.statusBar().showMessage("Follow cursor ON: pan/tilt follows the mouse, laser state is unchanged", 5000)
        else:
            self.follow_cursor_timer.stop()
            self._pending_follow_point = None
            self.statusBar().showMessage("Follow cursor OFF", 3000)

    def follow_cursor_point(self, x_mm: float, y_mm: float) -> None:
        if self.follow_cursor_enabled:
            self._pending_follow_point = (x_mm, y_mm)

    def _flush_follow_cursor(self) -> None:
        if not self.follow_cursor_enabled or self._pending_follow_point is None:
            return
        x_mm, y_mm = self._pending_follow_point
        self._pending_follow_point = None
        self.aim_at_point(x_mm, y_mm, warn=False)

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

    def refresh_geometry_status(self) -> None:
        geometry = self.project.geometry
        if not hasattr(self, "geometry_status"):
            return
        if geometry.fitted:
            self.geometry_status.setText(
                f"Fitted: X={geometry.head_x_mm:.0f}, Y={geometry.head_y_mm:.0f}, Z={geometry.head_z_mm:.0f} mm. Error {geometry.fit_error_mm:.0f} mm."
            )
        else:
            self.geometry_status.setText("Not fitted. Use Calibration Wizard, save center-cross points, then Fit head.")

    def refresh_fixture_status(self) -> None:
        if not hasattr(self, "fixture_status"):
            return
        cfg = self.project.fixture
        mode = "16-bit" if cfg.pan_tilt_16bit else "8-bit"
        non_zero = [(index + 1, value) for index, value in enumerate(self.fixture.levels[:32]) if value]
        preview = ", ".join(f"{channel}={level}" for channel, level in non_zero[:16]) or "all zero"
        self.fixture_status.setText(
            f"Applied: {cfg.artnet_ip}:{cfg.artnet_port}, universe {cfg.universe}, address {cfg.address}, {mode}, {len(cfg.extra_channels)} extra channels.\nDMX: {preview}"
        )

    def _refresh_all(self) -> None:
        self._upgrade_legacy_geometry()
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
        self.dimmer_level_spin.setValue(cfg.laser_on_value)
        self.blackout_level_spin.setValue(cfg.laser_off_value)
        self.geometry_enabled.setChecked(self.project.geometry.enabled)
        self.pan_range_control.setValue(self.project.geometry.pan_range_deg)
        self.tilt_range_control.setValue(self.project.geometry.tilt_range_deg)
        self.populate_extra_channel_rows()
        self.pan_min_spin.setValue(cfg.pan_min)
        self.pan_max_spin.setValue(cfg.pan_max)
        self.tilt_min_spin.setValue(cfg.tilt_min)
        self.tilt_max_spin.setValue(cfg.tilt_max)
        self.pan_spin.setRange(0, 65535 if cfg.pan_tilt_16bit else 255)
        self.tilt_spin.setRange(0, 65535 if cfg.pan_tilt_16bit else 255)
        self.pan_spin.setValue(min(self.current_pan, self.pan_spin.maximum()))
        self.tilt_spin.setValue(min(self.current_tilt, self.tilt_spin.maximum()))
        self._building_ui = False
        self.project.calibration = normalize_calibration_samples(self.project.calibration, cfg)
        self.calibration.set_samples(self.project.calibration)
        self.calibration.set_dmx_max(65535 if cfg.pan_tilt_16bit else 255)
        self.calibration.set_geometry(self.project.geometry)
        self.fixture.update_config(cfg)
        self.artnet.configure(cfg.artnet_ip, cfg.artnet_port, cfg.universe)
        self._populate_objects(self.project.objects)
        self.refresh_geometry_status()
        self.refresh_fixture_status()

    def _upgrade_legacy_geometry(self) -> None:
        geometry = self.project.geometry
        if not geometry.fitted or geometry.model_version >= 2:
            return
        dmx_max = 65535 if self.project.fixture.pan_tilt_16bit else 255
        try:
            self.project.geometry = fit_geometric_model(self.project.calibration, geometry, dmx_max)
        except Exception as exc:
            geometry.fitted = False
            geometry.enabled = False
            geometry.model_version = 2
            self.statusBar().showMessage(f"Old geometry disabled: {exc}", 8000)
        else:
            self.statusBar().showMessage("Saved geometry upgraded for the fixture DMX resolution", 8000)

    def _populate_objects(self, objects: list[PlanObject]) -> None:
        self.object_list.clear()
        for obj in objects:
            item = QListWidgetItem(obj.name)
            item.setData(Qt.UserRole, obj.id)
            self.object_list.addItem(item)

    def _populate_calibration(self) -> None:
        self.calibration.set_samples(self.project.calibration)
        self.calibration.set_geometry(self.project.geometry)

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
            laser_on_value=self.dimmer_level_spin.value(),
            laser_off_value=self.blackout_level_spin.value(),
        )
        cfg.extra_channels = self.read_extra_channel_rows()
        self.project.fixture = cfg
        self.project.geometry.enabled = self.geometry_enabled.isChecked()
        self.project.geometry.pan_range_deg = self.pan_range_control.value()
        self.project.geometry.tilt_range_deg = self.tilt_range_control.value()
        self.calibration.set_geometry(self.project.geometry)
        self.project.pdf_scale = self.plan_view.pdf_scale



def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Laser Director")
    print(f"Laser Director {__version__} [{__build__}]")
    window = MainWindow()
    window.statusBar().showMessage(f"Running Laser Director {__version__} [{__build__}]", 8000)
    window.show()
    sys.exit(app.exec())
