from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StagePoint:
    name: str
    x_mm: float
    y_mm: float


@dataclass
class PlanObject:
    id: str
    name: str
    kind: str
    points: list[tuple[float, float]] = field(default_factory=list)
    center: tuple[float, float] | None = None
    radius: float | None = None
    text: str | None = None

    def bbox(self) -> tuple[float, float, float, float] | None:
        pts = list(self.points)
        if self.center and self.radius is not None:
            cx, cy = self.center
            pts.extend([(cx - self.radius, cy - self.radius), (cx + self.radius, cy + self.radius)])
        elif self.center:
            pts.append(self.center)
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def bbox_center(self) -> tuple[float, float] | None:
        box = self.bbox()
        if not box:
            return self.center
        x1, y1, x2, y2 = box
        return (x1 + x2) / 2, (y1 + y2) / 2

    def corners(self) -> list[tuple[float, float]]:
        box = self.bbox()
        if not box:
            return []
        x1, y1, x2, y2 = box
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


@dataclass
class CalibrationSample:
    name: str
    x_mm: float
    y_mm: float
    pan: int
    tilt: int


@dataclass
class FixtureConfig:
    name: str = "Moving Head Laser"
    artnet_ip: str = "2.255.255.255"
    artnet_port: int = 6454
    universe: int = 0
    address: int = 1
    pan_channel: int = 1
    tilt_channel: int = 3
    laser_channel: int = 5
    pan_min: int = 0
    pan_max: int = 65535
    tilt_min: int = 0
    tilt_max: int = 65535
    pan_tilt_16bit: bool = True
    laser_on_value: int = 255
    laser_off_value: int = 0


@dataclass
class FixtureGeometry:
    enabled: bool = True
    head_x_mm: float = 0
    head_y_mm: float = -3000
    head_z_mm: float = 3000
    target_z_mm: float = 0
    pan_range_deg: float = 540
    tilt_range_deg: float = 270
    yaw_deg: float = 0
    pitch_deg: float = 0
    roll_deg: float = 0
    pan_center: float = 32768
    tilt_center: float = 32768
    pan_sign: int = 1
    tilt_sign: int = 1
    fit_error_mm: float | None = None
    fitted: bool = False


@dataclass
class PdfScale:
    p1_scene: tuple[float, float] | None = None
    p2_scene: tuple[float, float] | None = None
    real_distance_mm: float | None = None


@dataclass
class Project:
    name: str = "Untitled"
    coordinate_units: str = "mm"
    y_direction: str = "depth"
    fixture: FixtureConfig = field(default_factory=FixtureConfig)
    geometry: FixtureGeometry = field(default_factory=FixtureGeometry)
    calibration: list[CalibrationSample] = field(default_factory=list)
    objects: list[PlanObject] = field(default_factory=list)
    plan_path: str | None = None
    plan_type: str | None = None
    pdf_scale: PdfScale = field(default_factory=PdfScale)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        fixture = FixtureConfig(**data.get("fixture", {}))
        geometry = FixtureGeometry(**data.get("geometry", {}))
        calibration = [CalibrationSample(**item) for item in data.get("calibration", [])]
        objects = [PlanObject(**item) for item in data.get("objects", [])]
        pdf_scale = PdfScale(**data.get("pdf_scale", {}))
        return cls(
            name=data.get("name", "Untitled"),
            coordinate_units=data.get("coordinate_units", "mm"),
            y_direction=data.get("y_direction", "depth"),
            fixture=fixture,
            geometry=geometry,
            calibration=calibration,
            objects=objects,
            plan_path=data.get("plan_path"),
            plan_type=data.get("plan_type"),
            pdf_scale=pdf_scale,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolve_plan_path(self, project_file: str | Path | None = None) -> Path | None:
        if not self.plan_path:
            return None
        path = Path(self.plan_path)
        if path.is_absolute() or project_file is None:
            return path
        return Path(project_file).parent / path
