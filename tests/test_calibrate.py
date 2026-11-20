from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from calibrate import calibrate, main
from zeroframe.roi import load_config


class FakeFrame:
    shape = (480, 640, 3)
    size = 480 * 640 * 3

    def copy(self):
        return FakeFrame()


def fake_cv(actions):
    events = iter(actions)
    cv = SimpleNamespace(
        WINDOW_AUTOSIZE=1, WND_PROP_VISIBLE=4, EVENT_LBUTTONDOWN=1,
        FONT_HERSHEY_SIMPLEX=0, error=type("CVError", (Exception,), {}),
        namedWindow=Mock(), imshow=Mock(), destroyAllWindows=Mock(),
        circle=Mock(), line=Mock(), putText=Mock(), getWindowProperty=Mock(return_value=1),
    )

    def set_callback(window, callback):
        cv.callback = callback

    def wait_key(delay):
        key, points = next(events)
        for x, y in points:
            cv.callback(cv.EVENT_LBUTTONDOWN, x, y, 0, None)
        return key

    cv.setMouseCallback = set_callback
    cv.waitKey = wait_key
    return cv


class CalibrationTests(unittest.TestCase):
    def run_calibration(self, path, actions, *, seat_ids=None, room="R", display=None, **kwargs):
        cv = fake_cv(actions)
        with patch("calibrate.require_opencv", return_value=cv), \
             patch("calibrate.camera_session", return_value=nullcontext(object())), \
             patch("calibrate.read_frame", return_value=FakeFrame()), \
             patch.dict("os.environ", {"DISPLAY": ":test"} if display is None else display, clear=True), \
             patch("sys.stdout") as stdout:
            if seat_ids is None and "max_seats" not in kwargs:
                seat_ids = ["A-01"]
            result = calibrate(room, seat_ids, output=path, **kwargs)
        self.output = "".join(call.args[0] for call in stdout.write.call_args_list)
        cv.destroyAllWindows.assert_called_once()
        return result

    def test_freeze_click_confirm_and_save_only_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            result = self.run_calibration(path, [
                (32, []), (ord("n"), [(10, 10), (100, 10), (100, 100), (10, 100)]),
                (ord("s"), []),
            ])
            self.assertEqual(load_config(path), result)
            self.assertEqual((result.frame_width, result.frame_height), (640, 480))
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_multiple_seats_follow_requested_order(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_calibration(Path(directory) / "roi.json", [
                (32, []), (ord("n"), [(10, 10), (100, 10), (100, 100)]),
                (ord("n"), [(200, 10), (300, 10), (300, 100)]),
                (ord("s"), []),
            ], seat_ids=["A-01", "A-02"])
            self.assertEqual([seat.seat_id for seat in result.seats], ["A-01", "A-02"])
            self.assertIn("Next seat: A-02", self.output)
            self.assertIn("Press S to save", self.output)

    def test_single_starting_id_can_save_four_registered_seats(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            polygons = [[(x, 10), (x + 30, 10), (x, 40)] for x in (10, 110, 210, 310)]
            self.run_calibration(path, [(32, [])] + [(ord("n"), p) for p in polygons]
                                 + [(ord("s"), [])], room="IT4-101")
            seats = load_config(path).seats
            self.assertEqual([s.seat_id for s in seats], ["A-01", "A-02", "B-01", "B-02"])
            self.assertEqual([list(s.polygon) for s in seats], polygons)

    def test_count_limit_ignores_extra_clicks_and_allows_undo_at_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            result = self.run_calibration(path, [
                (32, []), (ord("n"), [(0, 0), (20, 0), (20, 20)]),
                (ord("n"), [(40, 0), (60, 0), (60, 20)]),
                (ord("n"), [(80, 0), (100, 0), (100, 20)]),
                (ord("u"), []), (ord("u"), []), (ord("n"), [(60, 30)]),
                (ord("s"), [(80, 0)]),
            ], room="IT4-101", max_seats=2)
            self.assertEqual([s.seat_id for s in result.seats], ["A-01", "A-02"])
            self.assertEqual(result.seats[-1].polygon[-1], (60, 30))

    def test_count_mode_can_save_early_and_preserves_existing_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            self.run_calibration(path, [(32, []), (ord("n"), [(0, 0), (20, 0), (0, 20)]),
                                 (ord("n"), [(40, 0), (60, 0), (40, 20)]), (ord("s"), [])],
                                 seat_ids=["Desk-09", "Lab-22"])
            result = self.run_calibration(path, [(32, []), (ord("s"), []),
                                 (ord("n"), [(10, 10), (30, 10), (10, 30)]), (ord("s"), [])],
                                 max_seats=4, overwrite=True)
            self.assertEqual([s.seat_id for s in result.seats], ["Desk-09"])
            self.assertEqual(load_config(path), result)

    def test_unfinished_extra_seat_blocks_save_and_undo_reopens_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            result = self.run_calibration(path, [
                (32, []), (ord("n"), [(0, 0), (30, 0), (0, 30)]),
                (ord("s"), [(50, 0)]), (13, [(80, 0), (50, 30)]),
                (ord("u"), []), (ord("u"), []), (13, [(50, 40)]), (ord("s"), []),
            ], seat_ids=["Desk-09"])
            self.assertEqual([s.seat_id for s in result.seats], ["Desk-09", "Desk-10"])
            self.assertEqual(result.seats[1].polygon[-1], (50, 40))

    def test_cancellation_leaves_existing_coordinates_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            path.write_text("previous calibration", encoding="utf-8")
            for actions in ([(ord("q"), [])], [(32, []), (27, [])]):
                self.assertIsNone(self.run_calibration(path, actions, overwrite=True))
                self.assertEqual(path.read_text(encoding="utf-8"), "previous calibration")

    def test_invalid_polygon_and_unfinished_save_do_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            self.run_calibration(path, [
                (32, []), (ord("n"), [(10, 10), (100, 10)]),
                (ord("s"), []), (27, []),
            ])
            self.assertFalse(path.exists())
            self.assertIn("A-01: 2 points", self.output)
            self.assertIn("0 seats confirmed", self.output)

    def test_undo_reopens_last_seat_for_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            result = self.run_calibration(path, [
                (32, []), (ord("n"), [(10, 10), (100, 10), (100, 100), (10, 100)]),
                (ord("u"), []), (ord("u"), []), (ord("n"), [(10, 120)]),
                (ord("s"), []),
            ])
            self.assertEqual(result.seats[0].polygon[-1], (10, 120))

    def test_ids_and_existing_file_are_checked_before_camera_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            path.touch()
            with patch("calibrate.require_opencv") as cv:
                with self.assertRaises(FileExistsError):
                    calibrate("R", ["A"], output=path)
                with self.assertRaises(ValueError):
                    calibrate("R", ["A", "A"], output=path)
                cv.assert_not_called()

    def test_linux_without_display_is_rejected_before_loading_opencv(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("calibrate.sys.platform", "linux"), \
                 patch.dict("os.environ", {}, clear=True), \
                 patch("calibrate.require_opencv") as cv:
                with self.assertRaisesRegex(RuntimeError, "local Linux desktop"):
                    calibrate("R", ["A"], output=Path(directory) / "roi.json")
                cv.assert_not_called()

    def test_linux_desktop_display_reaches_calibration(self):
        for display in ({"DISPLAY": ":0"}, {"WAYLAND_DISPLAY": "wayland-0"}):
            with self.subTest(display=display), tempfile.TemporaryDirectory() as directory:
                with patch("calibrate.sys.platform", "linux"):
                    result = self.run_calibration(
                        Path(directory) / "roi.json", [(ord("q"), [])], display=display
                    )
                self.assertIsNone(result)

    def test_cli_prints_actionable_error_without_traceback(self):
        with patch("calibrate.calibrate", side_effect=RuntimeError("camera unavailable")), \
             patch("sys.stderr") as stderr:
            self.assertEqual(main(["--room", "R", "--seats", "A"]), 1)
            self.assertIn("camera unavailable", str(stderr.write.call_args_list))

    def test_display_error_closes_window_and_does_not_save(self):
        cv = fake_cv([])
        cv.imshow.side_effect = cv.error("display failed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            with patch("calibrate.require_opencv", return_value=cv), \
                 patch("calibrate.camera_session", return_value=nullcontext(object())), \
                 patch("calibrate.read_frame", return_value=FakeFrame()), \
                 patch.dict("os.environ", {"DISPLAY": ":test"}, clear=True), \
                 patch("sys.stdout"):
                with self.assertRaisesRegex(RuntimeError, "display failed"):
                    calibrate("R", ["A"], output=path)
            cv.destroyAllWindows.assert_called_once()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
