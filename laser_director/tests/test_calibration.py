from __future__ import annotations

import unittest

from laser_director.calibration import (
    CalibrationModel,
    equivalent_pan_values,
    pan_turn_period_dmx,
    recommended_pan_branch,
)
from laser_director.models import CalibrationSample, FixtureGeometry


class PanBranchTests(unittest.TestCase):
    def test_540_degree_pan_has_170_dmx_full_turn(self) -> None:
        self.assertEqual(pan_turn_period_dmx(255, 540), 170)
        self.assertEqual(equivalent_pan_values(217, 255, 540), [47, 217])

    def test_branch_nearest_saved_neighbors_is_selected(self) -> None:
        self.assertEqual(recommended_pan_branch(47, 255, 540, expected_pan=220), 217)
        self.assertEqual(recommended_pan_branch(217, 255, 540, expected_pan=40), 47)


class CalibrationAnchorTests(unittest.TestCase):
    def test_fitted_geometry_preserves_saved_point_exactly(self) -> None:
        sample = CalibrationSample("center", 0, 0, 217, 129)
        model = CalibrationModel([sample])
        model.set_dmx_max(255)
        model.set_geometry(FixtureGeometry(enabled=True, fitted=True))
        self.assertEqual(model.interpolate(0, 0), (217, 129))


if __name__ == "__main__":
    unittest.main()
