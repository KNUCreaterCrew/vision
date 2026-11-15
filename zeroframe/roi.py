"""Validated, image-free seat coordinates and bottom-center object mapping."""

import json
import os
import tempfile
from dataclasses import dataclass
from math import isfinite
from pathlib import Path


def _identifier(value, name):
    if not isinstance(value, str) or not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{name} must be a nonempty, printable ID without outer whitespace")


def _dimension(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def validate_ids(room_id, seat_ids):
    _identifier(room_id, "room_id")
    if not isinstance(seat_ids, (list, tuple)) or not seat_ids:
        raise ValueError("at least one seat ID is required")
    for seat_id in seat_ids:
        _identifier(seat_id, "seat_id")
    if len(set(seat_ids)) != len(seat_ids):
        raise ValueError("seat IDs must be unique")


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, point):
    return (
        _cross(a, b, point) == 0
        and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
    )


def _intersects(a, b, c, d):
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    return (
        ((ab_c > 0 > ab_d or ab_d > 0 > ab_c) and (cd_a > 0 > cd_b or cd_b > 0 > cd_a))
        or _on_segment(a, b, c)
        or _on_segment(a, b, d)
        or _on_segment(c, d, a)
        or _on_segment(c, d, b)
    )


def _contains(polygon, point):
    inside = False
    x, y = point
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if _on_segment(a, b, point):
            return True
        if (a[1] > y) != (b[1] > y):
            crossing_x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < crossing_x:
                inside = not inside
    return inside


@dataclass(frozen=True)
class SeatROI:
    seat_id: str
    polygon: tuple[tuple[int, int], ...]

    def __post_init__(self):
        _identifier(self.seat_id, "seat_id")
        if not isinstance(self.polygon, (list, tuple)) or len(self.polygon) < 3:
            raise ValueError("polygon needs at least three vertices")
        for point in self.polygon:
            if (
                not isinstance(point, (list, tuple))
                or len(point) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in point)
            ):
                raise ValueError("polygon vertices must be nonnegative integer [x, y] pairs")
        polygon = tuple(tuple(point) for point in self.polygon)
        if len(set(polygon)) != len(polygon):
            raise ValueError("polygon vertices must be distinct; do not repeat the first vertex")
        edges = list(zip(polygon, polygon[1:] + polygon[:1]))
        if sum(a[0] * b[1] - b[0] * a[1] for a, b in edges) == 0:
            raise ValueError("polygon must have nonzero area")
        for i, (a, b) in enumerate(edges):
            for j in range(i + 1, len(edges)):
                if j == i + 1 or (i == 0 and j == len(edges) - 1):
                    continue
                if _intersects(a, b, *edges[j]):
                    raise ValueError("polygon edges must not intersect or touch themselves")
        object.__setattr__(self, "polygon", polygon)


@dataclass(frozen=True)
class ROIConfig:
    room_id: str
    frame_width: int
    frame_height: int
    seats: tuple[SeatROI, ...]

    def __post_init__(self):
        _dimension(self.frame_width, "frame_width")
        _dimension(self.frame_height, "frame_height")
        if not isinstance(self.seats, (list, tuple)) or not self.seats:
            raise ValueError("seats must contain at least one SeatROI")
        if any(not isinstance(seat, SeatROI) for seat in self.seats):
            raise ValueError("seats must contain SeatROI values")
        validate_ids(self.room_id, [seat.seat_id for seat in self.seats])
        for seat in self.seats:
            if any(x >= self.frame_width or y >= self.frame_height for x, y in seat.polygon):
                raise ValueError(f"{seat.seat_id}: polygon exceeds the calibrated frame")
        object.__setattr__(self, "seats", tuple(self.seats))

    def matching_seat_ids(self, bbox, *, frame_width: int, frame_height: int) -> tuple[str, ...]:
        """Return every candidate; multiple matches must be treated as ambiguous."""
        _dimension(frame_width, "frame_width")
        _dimension(frame_height, "frame_height")
        if (frame_width, frame_height) != (self.frame_width, self.frame_height):
            raise ValueError("frame resolution changed; recalibrate instead of silently scaling ROIs")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("bbox must contain [x1, y1, x2, y2]")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
            for v in bbox
        ):
            raise ValueError("bbox coordinates must be finite numbers")
        x1, y1, x2, y2 = bbox
        if not (0 <= x1 < x2 <= frame_width and 0 <= y1 < y2 <= frame_height):
            raise ValueError("bbox must have positive area and fit inside the frame")
        point = ((x1 + x2) / 2, y2)
        return tuple(seat.seat_id for seat in self.seats if _contains(seat.polygon, point))

    def to_dict(self):
        return {
            "room_id": self.room_id,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "seats": [
                {"seat_id": seat.seat_id, "polygon": [list(point) for point in seat.polygon]}
                for seat in self.seats
            ],
        }


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{name} must contain exactly: {', '.join(expected)}")


def load_config(path) -> ROIConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _keys(data, ("room_id", "frame_width", "frame_height", "seats"), "ROI config")
    if not isinstance(data["seats"], list):
        raise ValueError("seats must be a JSON array")
    seats = []
    for item in data["seats"]:
        _keys(item, ("seat_id", "polygon"), "seat")
        seats.append(SeatROI(item["seat_id"], item["polygon"]))
    return ROIConfig(data["room_id"], data["frame_width"], data["frame_height"], tuple(seats))


def save_config(config: ROIConfig, path, *, overwrite: bool = False) -> None:
    path = Path(path)
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if not overwrite:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
        return
    # Replace only after a complete JSON write, preserving the previous calibration on failure.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".json", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
