from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable

from .models import CalibrationSample, FixtureGeometry


INTERNAL_DMX_MAX = 65535


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


def _to_internal_dmx(value: float, dmx_max: int) -> float:
    return float(value) * INTERNAL_DMX_MAX / max(1, dmx_max)


def _from_internal_dmx(value: float, dmx_max: int) -> int:
    return int(round(float(value) * max(1, dmx_max) / INTERNAL_DMX_MAX))


def pan_turn_period_dmx(dmx_max: int, pan_range_deg: float) -> float:
    if pan_range_deg <= 0:
        raise ValueError("Pan range must be greater than zero")
    return max(1, dmx_max) * 360.0 / pan_range_deg


def equivalent_pan_values(pan: int, dmx_max: int, pan_range_deg: float) -> list[int]:
    period = pan_turn_period_dmx(dmx_max, pan_range_deg)
    candidates = {
        int(round(pan + turn * period))
        for turn in range(-4, 5)
        if 0 <= int(round(pan + turn * period)) <= dmx_max
    }
    return sorted(candidates)


def recommended_pan_branch(
    pan: int,
    dmx_max: int,
    pan_range_deg: float,
    expected_pan: float | None = None,
) -> int:
    candidates = equivalent_pan_values(pan, dmx_max, pan_range_deg)
    if not candidates:
        return max(0, min(dmx_max, int(round(pan))))
    if expected_pan is None:
        return max(candidates, key=lambda value: (min(value, dmx_max - value), -abs(value - dmx_max / 2)))

    def score(value: int) -> float:
        edge_margin = min(value, dmx_max - value)
        edge_penalty = max(0.0, dmx_max * 0.08 - edge_margin) * 0.25
        return abs(value - expected_pan) + edge_penalty

    return min(candidates, key=score)


def fit_geometric_model(
    samples: Iterable[CalibrationSample],
    initial: FixtureGeometry,
    dmx_max: int = INTERNAL_DMX_MAX,
) -> FixtureGeometry:
    import numpy as np
    from scipy.optimize import least_squares

    points = [
        CalibrationSample(
            sample.name,
            sample.x_mm,
            sample.y_mm,
            _to_internal_dmx(sample.pan, dmx_max),
            _to_internal_dmx(sample.tilt, dmx_max),
        )
        for sample in samples
    ]
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
    return replace(best_geometry, fitted=True, fit_error_mm=best_cost, model_version=2)


class CalibrationModel:
    def __init__(self, samples: Iterable[CalibrationSample] | None = None) -> None:
        self.samples = list(samples or [])
        self.geometry = FixtureGeometry(enabled=False)
        self.dmx_max = INTERNAL_DMX_MAX

    def set_samples(self, samples: Iterable[CalibrationSample]) -> None:
        self.samples = list(samples)

    def set_geometry(self, geometry: FixtureGeometry) -> None:
        self.geometry = geometry

    def set_dmx_max(self, dmx_max: int) -> None:
        self.dmx_max = INTERNAL_DMX_MAX if dmx_max > 255 else 255

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
        for sample in self.samples:
            if math.hypot(sample.x_mm - x_mm, sample.y_mm - y_mm) < 1e-6:
                return sample.pan, sample.tilt

        reference_pan, reference_tilt = self._geometric_reference(x_mm, y_mm)
        pan, tilt = self._solve_geometric_internal(x_mm, y_mm, reference_pan, reference_tilt)
        output_pan = _from_internal_dmx(pan, self.dmx_max)
        output_tilt = _from_internal_dmx(tilt, self.dmx_max)
        pan_correction, tilt_correction = self._geometric_residual_correction(x_mm, y_mm)
        return (
            max(0, min(self.dmx_max, int(round(output_pan + pan_correction)))),
            max(0, min(self.dmx_max, int(round(output_tilt + tilt_correction)))),
        )

    def _solve_geometric_internal(
        self,
        x_mm: float,
        y_mm: float,
        reference_pan: float,
        reference_tilt: float,
    ) -> tuple[float, float]:
        import numpy as np

        geometry = self.geometry
        world_direction = np.array(
            [
                x_mm - geometry.head_x_mm,
                y_mm - geometry.head_y_mm,
                geometry.target_z_mm - geometry.head_z_mm,
            ],
            dtype=float,
        )
        length = float(np.linalg.norm(world_direction))
        if length < 1e-9:
            raise ValueError("Target point is at the moving head position")
        world_direction /= length
        rotation = _rotation_matrix(
            _deg_to_rad(geometry.yaw_deg),
            _deg_to_rad(geometry.pitch_deg),
            _deg_to_rad(geometry.roll_deg),
        )
        local = rotation.T @ world_direction
        pan_angle = math.atan2(float(local[0]), float(local[1]))
        tilt_angle = math.atan2(-float(local[2]), math.hypot(float(local[0]), float(local[1])))
        pan_scale = _deg_to_rad(geometry.pan_range_deg) / INTERNAL_DMX_MAX
        tilt_scale = _deg_to_rad(geometry.tilt_range_deg) / INTERNAL_DMX_MAX
        if pan_scale <= 0 or tilt_scale <= 0:
            raise ValueError("Pan and tilt ranges must be greater than zero")

        candidates: list[tuple[float, float]] = []
        angle_branches = [
            (pan_angle, tilt_angle),
            (pan_angle + math.pi, math.pi - tilt_angle),
        ]
        for base_pan, base_tilt in angle_branches:
            for pan_turn in range(-3, 4):
                for tilt_turn in range(-3, 4):
                    pan = geometry.pan_center + (base_pan + 2 * math.pi * pan_turn) / (geometry.pan_sign * pan_scale)
                    tilt = geometry.tilt_center + (base_tilt + 2 * math.pi * tilt_turn) / (geometry.tilt_sign * tilt_scale)
                    if 0 <= pan <= INTERNAL_DMX_MAX and 0 <= tilt <= INTERNAL_DMX_MAX:
                        candidates.append((pan, tilt))
        if not candidates:
            raise ValueError("Target is outside the fitted pan/tilt range")

        pan, tilt = min(
            candidates,
            key=lambda values: (values[0] - reference_pan) ** 2 + (values[1] - reference_tilt) ** 2,
        )
        return pan, tilt

    def _geometric_residual_correction(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        ranked: list[tuple[float, float, float]] = []
        for sample in self.samples:
            reference_pan = _to_internal_dmx(sample.pan, self.dmx_max)
            reference_tilt = _to_internal_dmx(sample.tilt, self.dmx_max)
            try:
                predicted_pan, predicted_tilt = self._solve_geometric_internal(
                    sample.x_mm,
                    sample.y_mm,
                    reference_pan,
                    reference_tilt,
                )
            except ValueError:
                continue
            pan_error = sample.pan - _from_internal_dmx(predicted_pan, self.dmx_max)
            tilt_error = sample.tilt - _from_internal_dmx(predicted_tilt, self.dmx_max)
            distance = math.hypot(sample.x_mm - x_mm, sample.y_mm - y_mm)
            if distance < 1e-6:
                return float(pan_error), float(tilt_error)
            ranked.append((distance, float(pan_error), float(tilt_error)))

        if not ranked:
            return 0.0, 0.0
        ranked.sort(key=lambda item: item[0])
        selected = ranked[: min(5, len(ranked))]
        weights = [1.0 / (distance**2) for distance, _, _ in selected]
        weight_sum = sum(weights)
        pan = sum(weight * pan_error for weight, (_, pan_error, _) in zip(weights, selected)) / weight_sum
        tilt = sum(weight * tilt_error for weight, (_, _, tilt_error) in zip(weights, selected)) / weight_sum
        return pan, tilt

    def _geometric_reference(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        if not self.samples:
            return self.geometry.pan_center, self.geometry.tilt_center
        pan, tilt = self._idw(x_mm, y_mm)
        return _to_internal_dmx(pan, self.dmx_max), _to_internal_dmx(tilt, self.dmx_max)

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
