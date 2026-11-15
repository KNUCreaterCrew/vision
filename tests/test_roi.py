import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zeroframe.roi import ROIConfig, SeatROI, load_config, save_config, validate_ids


SQUARE = ((10, 10), (100, 10), (100, 100), (10, 100))


def config(*seats):
    return ROIConfig("IT4-101", 640, 480, seats or (SeatROI("A-01", SQUARE),))


def match(roi, bbox):
    return roi.matching_seat_ids(bbox, frame_width=640, frame_height=480)


class PolygonTests(unittest.TestCase):
    def test_bottom_center_is_used_instead_of_box_center(self):
        self.assertEqual(match(config(), (40, 0, 60, 20)), ("A-01",))
        self.assertEqual(match(config(), (40, 80, 60, 120)), ())

    def test_clockwise_and_counterclockwise_polygons_work(self):
        for polygon in (SQUARE, SQUARE[::-1]):
            with self.subTest(polygon=polygon):
                self.assertEqual(match(config(SeatROI("A", polygon)), (20, 20, 40, 40)), ("A",))

    def test_concave_polygon_does_not_match_its_cutout(self):
        roi = config(SeatROI("L", ((0, 0), (100, 0), (100, 30), (30, 30), (30, 100), (0, 100))))
        self.assertEqual(match(roi, (5, 10, 15, 80)), ("L",))
        self.assertEqual(match(roi, (40, 40, 60, 60)), ())

    def test_boundary_and_vertex_are_included(self):
        self.assertEqual(match(config(), (5, 5, 15, 10)), ("A-01",))
        self.assertEqual(match(config(), (20, 80, 40, 100)), ("A-01",))

    def test_overlap_returns_all_candidates(self):
        roi = config(SeatROI("A", SQUARE), SeatROI("B", ((50, 10), (150, 10), (150, 100), (50, 100))))
        self.assertEqual(match(roi, (60, 20, 80, 50)), ("A", "B"))

    def test_shared_boundary_is_ambiguous(self):
        roi = config(SeatROI("A", SQUARE), SeatROI("B", ((100, 10), (150, 10), (150, 100), (100, 100))))
        self.assertEqual(match(roi, (90, 20, 110, 50)), ("A", "B"))

    def test_bad_polygons_are_rejected(self):
        cases = [
            None, "polygon", [], [(0, 0), (1, 1)],
            [(0, 0), (1, 1), (2, 2)],
            [(0, 0), (4, 4), (0, 4), (3, 0)],
            [(0, 0), (4, 0), (4, 4), (0, 0)],
            [(0, 0), (True, 2), (2, 0)],
            [(0, 0), (1.5, 2), (2, 0)],
            [(0, 0), (-1, 2), (2, 0)],
            [(0, 0), (1, 2, 3), (2, 0)],
        ]
        for polygon in cases:
            with self.subTest(polygon=polygon):
                with self.assertRaises(ValueError):
                    SeatROI("A", polygon)

    def test_input_lists_are_copied_into_immutable_coordinates(self):
        points = [list(point) for point in SQUARE]
        seat = SeatROI("A", points)
        points[0][0] = 999
        self.assertEqual(seat.polygon, SQUARE)

    def test_bbox_and_resolution_validation(self):
        for bbox in (
            None, [1, 2], [0, 0, 0, 10], [10, 0, 5, 10],
            [-1, 0, 10, 10], [0, 0, 641, 10], [0, 0, 10, 481],
            [False, 0, 10, 10], [0, 0, float("nan"), 10], [0, 0, float("inf"), 10],
        ):
            with self.subTest(bbox=bbox):
                with self.assertRaises(ValueError):
                    match(config(), bbox)
        with self.assertRaises(ValueError):
            config().matching_seat_ids((20, 20, 40, 40), frame_width=1280, frame_height=720)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_ids_dimensions_seats_and_coordinates(self):
        for room, seats in (("", ["A"]), ("room", []), (" room", ["A"]), ("room", ["A", "A"]), ("room", ["A\n"]), (None, ["A"])):
            with self.subTest(room=room, seats=seats):
                with self.assertRaises(ValueError):
                    validate_ids(room, seats)
        for dimension in (0, -1, True, 640.0, "640"):
            for kwargs in ({"frame_width": dimension}, {"frame_height": dimension}):
                values = dict(room_id="R", frame_width=640, frame_height=480, seats=(SeatROI("A", SQUARE),))
                values.update(kwargs)
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        ROIConfig(**values)
        for seats in ((), ("seat",), (SeatROI("A", SQUARE), SeatROI("A", SQUARE))):
            with self.assertRaises(ValueError):
                ROIConfig("R", 640, 480, seats)
        with self.assertRaises(ValueError):
            ROIConfig("R", 100, 100, (SeatROI("A", SQUARE),))

    def test_example_is_valid_and_maps_seats(self):
        roi = load_config(Path(__file__).resolve().parents[1] / "examples/roi_config.example.json")
        self.assertEqual(match(roi, (100, 150, 200, 300)), ("A-01",))
        self.assertEqual(match(roi, (400, 150, 500, 300)), ("A-02",))

    def test_round_trip_writes_metadata_only_and_refuses_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            save_config(config(), path)
            original = path.read_bytes()
            self.assertEqual(load_config(path), config())
            self.assertEqual(set(json.loads(original)), {"room_id", "frame_width", "frame_height", "seats"})
            with self.assertRaises(FileExistsError):
                save_config(config(), path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_explicit_overwrite_replaces_only_after_successful_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            save_config(config(), path)
            replacement = config(SeatROI("B", SQUARE))
            original = path.read_bytes()
            with patch("zeroframe.roi.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    save_config(replacement, path, overwrite=True)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            save_config(replacement, path, overwrite=True)
            self.assertEqual(load_config(path), replacement)

    def test_unknown_fields_and_malformed_json_are_rejected(self):
        valid = config().to_dict()
        cases = [[], {}, {**valid, "image": "forbidden"}, {**valid, "seats": {}},
                 {**valid, "seats": [{"seat_id": "A", "polygon": SQUARE, "image": "forbidden"}]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            for data in cases:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_config(path)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
