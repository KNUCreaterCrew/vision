from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import run_vision
from zeroframe.detection import Detection
from zeroframe.roi import load_config


class VisionRunnerTests(unittest.TestCase):
    def run_fake(self, *, preview=False, frame=None, detect_error=None, camera_error=None,
                 transport=None, publisher=None):
        cv = SimpleNamespace(
            error=type("FakeCVError", (Exception,), {}), WINDOW_AUTOSIZE=1,
            WND_PROP_VISIBLE=1, namedWindow=Mock(), waitKey=Mock(side_effect=[0, 0, ord("q")]),
            getWindowProperty=Mock(return_value=1), destroyAllWindows=Mock(),
        )
        frame = frame or SimpleNamespace(shape=(480, 640, 3))
        detector = SimpleNamespace(detect=Mock(return_value=[Detection(0, .8, (100, 120, 200, 300))]),
                                   release_frames=Mock())
        detector.detect.side_effect = detect_error
        samples = [(1, frame)]
        times = [0, 1, 1.1, 1.2]
        if preview:
            samples.append((1.4, SimpleNamespace(shape=(480, 640, 3))))
            times.extend([1.2, 1.4, 1.4])
        stream = SimpleNamespace(read_after=Mock(side_effect=[*samples, KeyboardInterrupt()]))
        closed = []

        @contextmanager
        def session(index):
            try:
                if camera_error:
                    raise camera_error
                yield stream
            finally:
                closed.append(True)

        with patch.object(run_vision, "load_config", return_value=load_config("examples/roi_config.example.json")), \
             patch.object(run_vision, "YoloDetector", return_value=detector), \
             patch.object(run_vision, "require_opencv", return_value=cv), \
             patch.object(run_vision, "LatestCamera", side_effect=session), \
             patch.object(run_vision, "load_transport_config", return_value=transport), \
             patch.object(run_vision, "StatusPublisher", return_value=publisher), \
             patch.object(run_vision, "monotonic", side_effect=[*times, 12]), \
             patch.object(run_vision, "draw_preview") as draw, \
             patch.dict("os.environ", {"DISPLAY": ":test"}, clear=True), \
             patch("sys.stdout", new_callable=StringIO) as output:
            try:
                run_vision.run("unused", "local/yolo11n.pt", preview=preview)
            except KeyboardInterrupt:
                pass
            finally:
                self.assertTrue(closed)
                detector.release_frames.assert_called_once()
                if preview:
                    cv.destroyAllWindows.assert_called_once()
        return detector, cv, draw, output.getvalue()

    def test_headless_path_reports_states_and_never_opens_window(self):
        detector, cv, draw, output = self.run_fake()
        detector.detect.assert_called_once()
        cv.namedWindow.assert_not_called()
        draw.assert_not_called()
        self.assertIn("A-01=UNKNOWN", output)
        self.assertIn("predict=100ms", output)

    def test_configured_transport_receives_only_states_and_is_closed(self):
        publisher = MagicMock()
        self.run_fake(transport=Mock(), publisher=publisher)
        publisher.__enter__.assert_called_once()
        publisher.__exit__.assert_called_once()
        sent_states = publisher.update.call_args.args[0]
        self.assertEqual(set(sent_states), {"A-01", "A-02"})
        self.assertTrue(all(hasattr(state, "last_observed_at") for state in sent_states.values()))
        self.assertEqual(publisher.update.call_args.kwargs, {"inference_count": 1})

    def test_ten_second_schedule_transitions_stale_result_and_expiry(self):
        now = 0.0
        starts = []
        person = Detection(0, .8, (100, 120, 200, 300))
        bottle = Detection(39, .35, (100, 120, 200, 300))
        scenes = [[person], [person], [bottle], [bottle], [person], [person],
                  [person], [person], [], [], []]

        def advance(seconds):
            nonlocal now
            now += seconds

        def read(previous_at):
            if len(starts) == len(scenes):
                raise KeyboardInterrupt
            return now, SimpleNamespace(shape=(480, 640, 3))

        def detect(frame):
            index = len(starts)
            starts.append(now)
            advance(2.1 if index == 5 else 0.25)
            return scenes[index]

        camera = MagicMock()
        camera.__enter__.return_value.read_after.side_effect = read
        publisher = MagicMock()
        with patch.object(run_vision, "LatestCamera", return_value=camera), \
             patch.object(run_vision, "YoloDetector", return_value=Mock(detect=detect)), \
             patch.object(run_vision, "require_opencv", return_value=SimpleNamespace(error=RuntimeError)), \
             patch.object(run_vision, "load_transport_config", return_value=Mock()), \
             patch.object(run_vision, "StatusPublisher", return_value=publisher), \
             patch.object(run_vision, "monotonic", side_effect=lambda: now), \
             patch.object(run_vision, "sleep", side_effect=advance), patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(run_vision.main(["--config", "examples/roi_config.example.json"]), 0)

        self.assertEqual(starts, list(range(0, 101, 10)))
        states = [call.args[0]["A-01"] for call in publisher.update.call_args_list]
        self.assertEqual([state.status.value for state in states], [
            "UNKNOWN", "OCCUPIED", "OCCUPIED", "AWAY", "AWAY", "AWAY",
            "UNKNOWN", "OCCUPIED", "OCCUPIED", "OCCUPIED", "UNKNOWN",
        ])
        self.assertEqual(states[5].last_observed_at, 30)
        self.assertEqual(states[-1].last_observed_at, 70)

    def test_inference_failure_closes_transport_before_releasing_model(self):
        publisher = MagicMock()
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            self.run_fake(detect_error=RuntimeError("model failed"), transport=Mock(), publisher=publisher)
        publisher.__exit__.assert_called_once()

    def test_bad_transport_settings_fail_before_model_and_camera_start(self):
        with patch.object(run_vision, "load_config"), \
             patch.object(run_vision, "load_transport_config", side_effect=ValueError("bad backend settings")), \
             patch.object(run_vision, "YoloDetector") as model, patch.object(run_vision, "LatestCamera") as camera:
            with self.assertRaisesRegex(ValueError, "backend settings"):
                run_vision.run("unused", "unused")
            model.assert_not_called()
            camera.assert_not_called()

    def test_preview_displays_new_frames_between_checks_without_extra_reports_or_inference(self):
        publisher = MagicMock()
        detector, cv, draw, output = self.run_fake(preview=True, transport=Mock(), publisher=publisher)
        detector.detect.assert_called_once()
        publisher.update.assert_called_once()
        first, second = draw.call_args_list
        self.assertIsNot(first.args[1], second.args[1])
        self.assertEqual(first.args[3:], second.args[3:])
        self.assertAlmostEqual(second.kwargs["result_age"], .4)
        cv.namedWindow.assert_called_once()

    def test_changed_resolution_and_prediction_failure_release_resources(self):
        with self.assertRaisesRegex(ValueError, "resolution"):
            self.run_fake(frame=SimpleNamespace(shape=(240, 320, 3)))
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            self.run_fake(detect_error=RuntimeError("model failed"))
        with self.assertRaisesRegex(RuntimeError, "camera failed"):
            self.run_fake(camera_error=RuntimeError("camera failed"))

    def test_invalid_options_and_missing_display_fail_before_loading_model(self):
        with patch.object(run_vision, "YoloDetector") as model:
            for options in ({"fps": 0}, {"fps": .09}, {"fps": 3.1}, {"fps": float("nan")},
                            {"fps": True}, {"camera": -1}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    run_vision.run("unused", "unused", **options)
            with patch.object(run_vision, "load_config"), \
                 patch.object(run_vision.sys, "platform", "linux"), \
                 patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(RuntimeError, "desktop"):
                run_vision.run("unused", "unused", preview=True)
            model.assert_not_called()

    def test_main_reports_setup_errors_without_traceback(self):
        with patch.object(run_vision, "run", side_effect=ValueError("invalid ROI")), \
             patch("sys.stderr", new_callable=StringIO) as output:
            self.assertEqual(run_vision.main([]), 1)
            self.assertIn("Vision error: invalid ROI", output.getvalue())


if __name__ == "__main__":
    unittest.main()
