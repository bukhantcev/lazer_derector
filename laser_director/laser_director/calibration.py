from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable

from .models import CalibrationSample, FixtureGeometry


def _deg_to_rad(value: float) -> float:
    return value * math.pi / 180.0


def _rotation_matrix(yaw: float, pitch: float, roll: float):
    import numpy as np

    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
    ry = np.array([[cr, 0.0, sr], [0.0, 1.0, 0.0], [-sr, 0.0, cr]])
    return rz @ ry @ rx


def _predict_xy(sample: CalibrationSample, geometry: FixtureGeometry) -> tuple[float, float] | None:
    import numpy as np

    pan_scale = _deg_to_rad(geometry.pan_range_deg) / 65535.0
    tilt_scale = _deg_to_rad(geometry.tilt_range_deg) / 65535.0
    pan = geometry.pan_sign * (sample.pan - geometry.pan_center) * pan_scale
    tilt = geometry.tilt_sign * (sample.tilt - geometry.tilt_center) * tilt_scale

    local = np.array([
        math.sin(pan) * math.cos(tilt),
        math.cos(pan) * math.cos(tilt),
        -math.sin(tilt),
    ])
    direction = _rotation_matrix(_deg_to_rad(geometry.yaw_deg), _deg_to_rad(geometry.pitch_deg), _deg_to_rad(geometry.roll_deg)) @ local
    if direction[2] >= -1e-6:
        return None
    distance = (geometry.target_z_mm - geometry.head_z_mm) / direction[2]
    if distance <= 0:
        return None
    x = geometry.head_x_mm + distance * direction[0]
    y = geometry.head_y_mm + distance * direction[1]
    return float(x), float(y)


def fit_geometric_model(samples: Iterable[CalibrationSample], initial: FixtureGeometry) -> FixtureGeometry:
    import numpy as np
    from scipy.optimize import least_squares

    points = list(samples)
    if len(points) < 5:
        raise ValueError("Geometric calibration needs at least 5 points near stage zero")

    def pack(geometry: FixtureGeometry) -> np.ndarray:
        return np.array([
            geometry.head_x_mm,
            geometry.head_y_mm,
            max(500.0, geometry.head_z_mm),
            geometry.yaw_deg,
            geometry.pitch_deg,
            geometry.roll_deg,
            geometry.pan_center,
            geometry.tilt_center,
        ], dtype=float)

    def unpack(values: np.ndarray, pan_sign: int, tilt_sign: int) -> FixtureGeometry:
        return replace(
            initial,
            enabled=True,
            head_x_mm=float(values[0]),
            head_y_mm=float(values[1]),
            head_z_mm=float(values[2]),
            yaw_deg=float(values[3]),
            pitch_deg=float(values[4]),
            roll_deg=float(values[5]),
            pan_center=float(values[6]),
            tilt_center=float(values[7]),
            pan_sign=pan_sign,
            tilt_sign=tilt_sign,
        )

    lower = np.array([-50000, -50000, 500, -360, -45, -45, 0, 0], dtype=float)
    upper = np.array([50000, 50000, 30000, 360, 45, 45, 65535, 65535], dtype=float)
    starts = []
    base = pack(initial)
    starts.append(base)
    for x in (-3000, 0, 3000):
        for y in (-5000, 0, 5000):
            start = base.copy()
            start[0] = x
            start[1] = y
            start[2] = max(2000, initial.head_z_mm)
            starts.append(start)

    best_geometry: FixtureGeometry | None = None
    best_cost = float("inf")
    for pan_sign in (initial.pan_sign, -initial.pan_sign):
        for tilt_sign in (initial.tilt_sign, -initial.tilt_sign):
            for start in starts:
                def residual(values: np.ndarray) -> np.ndarray:
                    geometry = unpack(values, pan_sign, tilt_sign)
                    errors: list[float] = []
                    for point in points:
                        predicted = _predict_xy(point, geometry)
                        if predicted is None:
                            errors.extend([1000.0, 1000.0])
                        else:
                            errors.append((predicted[0] - point.x_mm) / 1000.0)
                            errors.append((predicted[1] - point.y_mm) / 1000.0)
                    errors.append((geometry.pitch_deg - initial.pitch_deg) / 20.0)
                    errors.append((geometry.roll_deg - initial.roll_deg) / 20.0)
                    return np.array(errors, dtype=float)

                result = least_squares(residual, np.clip(start, lower, upper), bounds=(lower, upper), max_nfev=3000)
                cost = float(np.mean(np.square(residual(result.x)[: len(points) * 2])) ** 0.5 * 1000.0)
                if cost < best_cost:
                    best_cost = cost
                    best_geometry = unpack(result.x, pan_sign, tilt_sign)

    if best_geometry is None:
        raise ValueError("Could not solve geometric calibration")
    return replace(best_geometry, fitted=True, fit_error_mm=best_cost)


class CalibrationModel:
    def __init__(self, samples: Iterable[CalibrationSample] | None = None) -> None:
        self.samples = list(samples or [])
        self.geometry = FixtureGeometry(enabled=False)

    def set_samples(self, samples: Iterable[CalibrationSample]) -> None:
        self.samples = list(samples)

    def set_geometry(self, geometry: FixtureGeometry) -> None:
        self.geometry = geometry

    def add_sample(self, sample: CalibrationSample) -> None:
        self.samples = [item for item in self.samples if item.name != sample.name]
        self.samples.append(sample)

    def interpolate(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        if self.geometry.enabled and self.geometry.fitted:
            return self.solve_geometric(x_mm, y_mm)
        if not self.samples:
            raise ValueError("No calibration samples")
        if len(self.samples) == 1:
            sample = self.samples[0]
            return sample.pan, sample.tilt
        if len(self.samples) >= 3:
            try:
                import numpy as np
                from scipy.interpolate import griddata

                point = np.array([[float(x_mm), float(y_mm)]])
                coords = np.array([[s.x_mm, s.y_mm] for s in self.samples], dtype=float)
                pan_values = np.array([s.pan for s in self.samples], dtype=float)
                tilt_values = np.array([s.tilt for s in self.samples], dtype=float)
                pan = griddata(coords, pan_values, point, method="linear")
                tilt = griddata(coords, tilt_values, point, method="linear")
                if pan is not None and tilt is not None and not np.isnan(pan[0]) and not np.isnan(tilt[0]):
                    return int(round(float(pan[0]))), int(round(float(tilt[0])))
            except Exception:
                pass
        return self._idw(x_mm, y_mm)

    def solve_geometric(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        import numpy as np
        from scipy.optimize import least_squares

        geometry = self.geometry

        def residual(values):
            sample = CalibrationSample("target", x_mm, y_mm, int(values[0]), int(values[1]))
            predicted = _predict_xy(sample, geometry)
            if predicted is None:
                return np.array([1000.0, 1000.0])
            return np.array([(predicted[0] - x_mm) / 1000.0, (predicted[1] - y_mm) / 1000.0])

        start = np.array([geometry.pan_center, geometry.tilt_center], dtype=float)
        result = least_squares(residual, start, bounds=([0, 0], [65535, 65535]), max_nfev=600)
        if not result.success:
            raise ValueError("Could not solve pan/tilt for point")
        return int(round(result.x[0])), int(round(result.x[1]))

    def _idw(self, x_mm: float, y_mm: float, nearest: int = 5, power: float = 2.0) -> tuple[int, int]:
        ranked = []
        for sample in self.samples:
            dist = math.hypot(sample.x_mm - x_mm, sample.y_mm - y_mm)
            if dist < 1e-9:
                return sample.pan, sample.tilt
            ranked.append((dist, sample))
        ranked.sort(key=lambda item: item[0])
        selected = ranked[: max(1, min(nearest, len(ranked)))]
        weights = [1.0 / (dist**power) for dist, _ in selected]
        weight_sum = sum(weights)
        pan = sum(weight * sample.pan for weight, (_, sample) in zip(weights, selected)) / weight_sum
        tilt = sum(weight * sample.tilt for weight, (_, sample) in zip(weights, selected)) / weight_sum
        return int(round(pan)), int(round(tilt))
