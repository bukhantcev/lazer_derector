from __future__ import annotations

from dataclasses import replace

from .models import FixtureConfig


class DmxFixture:
    def __init__(self, config: FixtureConfig | None = None) -> None:
        self.config = config or FixtureConfig()
        self.levels = bytearray(512)

    def update_config(self, config: FixtureConfig) -> None:
        self.config = replace(config)

    def clamp_pan_tilt(self, pan: int, tilt: int) -> tuple[int, int]:
        cfg = self.config
        pan = max(cfg.pan_min, min(cfg.pan_max, int(round(pan))))
        tilt = max(cfg.tilt_min, min(cfg.tilt_max, int(round(tilt))))
        if not cfg.pan_tilt_16bit:
            pan = max(0, min(255, pan))
            tilt = max(0, min(255, tilt))
        return pan, tilt

    def set_pan_tilt(self, pan: int, tilt: int) -> bytearray:
        pan, tilt = self.clamp_pan_tilt(pan, tilt)
        cfg = self.config
        if cfg.pan_tilt_16bit:
            self._set_16(cfg.address + cfg.pan_channel - 2, pan)
            self._set_16(cfg.address + cfg.tilt_channel - 2, tilt)
        else:
            self._set_8(cfg.address + cfg.pan_channel - 2, pan)
            self._set_8(cfg.address + cfg.tilt_channel - 2, tilt)
        return self.levels

    def set_laser(self, enabled: bool) -> bytearray:
        value = self.config.laser_on_value if enabled else self.config.laser_off_value
        self._set_8(self.config.address + self.config.laser_channel - 2, value)
        return self.levels

    def laser_off(self) -> bytearray:
        return self.set_laser(False)

    def _set_8(self, index: int, value: int) -> None:
        if 0 <= index < 512:
            self.levels[index] = max(0, min(255, int(value)))

    def _set_16(self, index: int, value: int) -> None:
        value = max(0, min(65535, int(value)))
        if 0 <= index < 512:
            self.levels[index] = (value >> 8) & 0xFF
        if 0 <= index + 1 < 512:
            self.levels[index + 1] = value & 0xFF
