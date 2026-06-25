from __future__ import annotations

import math
from typing import Iterable

from .models import CalibrationSample


class CalibrationModel:
    def __init__(self, samples: Iterable[CalibrationSample] | None = None) -> None:
        self.samples = list(samples or [])

    def set_samples(self, samples: Iterable[CalibrationSample]) -> None:
        self.samples = list(samples)

    def add_sample(self, sample: CalibrationSample) -> None:
        self.samples = [item for item in self.samples if item.name != sample.name]
        self.samples.append(sample)

    def interpolate(self, x_mm: float, y_mm: float) -> tuple[int, int]:
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
