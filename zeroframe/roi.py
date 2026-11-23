"""Validated seat coordinates, bottom-center mapping, and polygon/box overlap."""

import json
import os
import tempfile
from dataclasses import dataclass, field
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


def _polygon_box_area(polygon, bbox):
    # Clip against the four rectangle half-planes; preserves concave ROI area.
    points = list(polygon)
    x1, y1, x2, y2 = bbox
    for axis, bound, lower in ((0, x1, True), (0, x2, False), (1, y1, True), (1, y2, False)):
        if not points:
            return 0.0
        clipped = []
        a = points[-1]
        a_inside = a[axis] >= bound if lower else a[axis] <= bound
        for b in points:
            b_inside = b[axis] >= bound if lower else b[axis] <= bound
            if a_inside != b_inside:
                other = 1 - axis
                t = (bound - a[axis]) / (b[axis] - a[axis])
                crossing = [0.0, 0.0]
                crossing[axis] = bound
                crossing[other] = a[other] + t * (b[other] - a[other])
                clipped.append(tuple(crossing))
            if b_inside:
                clipped.append(b)
            a, a_inside = b, b_inside
        points = clipped
    return abs(sum(a[0] * b[1] - b[0] * a[1]
                   for a, b in zip(points, points[1:] + points[:1]))) / 2


@dataclass(frozen=True)
class SeatROI:
    seat_id: str
    polygon: tuple[tuple[int, int], ...]
    area: float = field(init=False, repr=False, compare=False)

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
        double_area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges))
        if double_area == 0:
            raise ValueError("polygon must have nonzero area")
        for i, (a, b) in enumerate(edges):
            for j in range(i + 1, len(edges)):
                if j == i + 1 or (i == 0 and j == len(edges) - 1):
                    continue
                if _intersects(a, b, *edges[j]):
                    raise ValueError("polygon edges must not intersect or touch themselves")
        object.__setattr__(self, "polygon", polygon)
        object.__setattr__(self, "area", double_area / 2)


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

    def _validate_bbox(self, bbox, frame_width, frame_height):
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

    def matching_seat_ids(self, bbox, *, frame_width: int, frame_height: int) -> tuple[str, ...]:
        """Return seats containing the box's bottom-center point (legacy point mapping)."""
        self._validate_bbox(bbox, frame_width, frame_height)
        x1, y1, x2, y2 = bbox
        point = ((x1 + x2) / 2, y2)
        return tuple(seat.seat_id for seat in self.seats if _contains(seat.polygon, point))

    def overlapping_seat_ratios(self, bbox, *, frame_width: int, frame_height: int,
                                normalize_to_smaller: bool = False) -> dict[str, float]:
        """Return positive overlaps as fractions of box area by default."""
        self._validate_bbox(bbox, frame_width, frame_height)
        x1, y1, x2, y2 = bbox
        box_area = (x2 - x1) * (y2 - y1)
        if not isfinite(box_area) or box_area <= 0:
            raise ValueError("bbox area must be finite and positive")
        ratios = {}
        for seat in self.seats:
            area = _polygon_box_area(seat.polygon, bbox)
            if area > 0:
                denominator = min(box_area, seat.area) if normalize_to_smaller else box_area
                ratios[seat.seat_id] = min(area / denominator, 1.0)
        return ratios

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
