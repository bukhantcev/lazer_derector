from __future__ import annotations

from pathlib import Path

import ezdxf

from .models import PlanObject


def read_dxf(path: str | Path) -> list[PlanObject]:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    objects: list[PlanObject] = []
    index = 1
    for entity in msp:
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                start = entity.dxf.start
                end = entity.dxf.end
                objects.append(_obj(index, "LINE", [(start.x, start.y), (end.x, end.y)]))
            elif kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                if entity.closed and pts:
                    pts.append(pts[0])
                objects.append(_obj(index, "LWPOLYLINE", pts))
            elif kind == "POLYLINE":
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
                objects.append(_obj(index, "POLYLINE", pts))
            elif kind == "CIRCLE":
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                objects.append(PlanObject(str(index), f"CIRCLE {index}", "CIRCLE", center=(center.x, center.y), radius=radius))
            elif kind == "INSERT":
                insert = entity.dxf.insert
                name = getattr(entity.dxf, "name", "INSERT")
                objects.append(PlanObject(str(index), f"{name} {index}", "INSERT", center=(insert.x, insert.y)))
            elif kind in {"TEXT", "MTEXT"}:
                insert = entity.dxf.insert if hasattr(entity.dxf, "insert") else entity.dxf.location
                text = entity.plain_text() if kind == "MTEXT" else entity.dxf.text
                objects.append(PlanObject(str(index), f"TEXT {index}: {text[:24]}", kind, center=(insert.x, insert.y), text=text))
            else:
                continue
            index += 1
        except Exception:
            continue
    return objects


def _obj(index: int, kind: str, points: list[tuple[float, float]]) -> PlanObject:
    return PlanObject(str(index), f"{kind} {index}", kind, points=points)
