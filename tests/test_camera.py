from types import SimpleNamespace
from threading import Event
import unittest
from unittest.mock import Mock, patch

from zeroframe.camera import LatestCamera, camera_session, read_frame, require_opencv


class CameraTests(unittest.TestCase):
    def cv(self):
        capture = Mock()
        capture.isOpened.return_value = True
        return SimpleNamespace(
            VideoCapture=Mock(return_value=capture),
            CAP_DSHOW=700, CAP_V4L2=200, CAP_ANY=0,
            CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
        ), capture

    def test_session_opens_only_numeric_local_device_and_releases_after_error(self):
        cv, capture = self.cv()
        with patch("zeroframe.camera.require_opencv", return_value=cv):
            with self.assertRaisesRegex(RuntimeError, "processing error"):
                with camera_session(0) as opened:
                    self.assertIs(opened, capture)
                    raise RuntimeError("processing error")
        self.assertEqual(cv.VideoCapture.call_args.args[0], 0)
        capture.release.assert_called_once()

    def test_platform_selects_windows_or_linux_backend(self):
        for platform, expected in (("win32", 700), ("linux", 200), ("darwin", 0)):
            with self.subTest(platform=platform):
                cv, capture = self.cv()
                with patch("zeroframe.camera.require_opencv", return_value=cv), \
                     patch("zeroframe.camera.sys.platform", platform):
                    with camera_session(1):
                        pass
                cv.VideoCapture.assert_called_once_with(1, expected)
                capture.release.assert_called_once()

    def test_failed_open_and_property_setup_release_camera(self):
        for failure in ("open", "set"):
            with self.subTest(failure=failure):
                cv, capture = self.cv()
                if failure == "open":
                    capture.isOpened.return_value = False
                else:
                    capture.set.side_effect = RuntimeError("property error")
                with patch("zeroframe.camera.require_opencv", return_value=cv):
                    with self.assertRaises(RuntimeError):
                        with camera_session():
                            self.fail("failed camera should not be yielded")
                capture.release.assert_called_once()

    def test_urls_paths_negative_and_boolean_camera_indexes_are_rejected(self):
        for index in ("https://example.com", "recording.mp4", -1, True, 0.5):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    with camera_session(index):
                        self.fail("invalid camera index accepted")

    def test_frame_read_failure_is_explicit(self):
        capture = Mock()
        for response in ((False, None), (True, None), (True, SimpleNamespace(size=0))):
            with self.subTest(response=response):
                capture.read.return_value = response
                with self.assertRaises(RuntimeError):
                    read_frame(capture)
        frame = SimpleNamespace(size=100)
        capture.read.return_value = (True, frame)
        self.assertIs(read_frame(capture), frame)

    def test_missing_opencv_reports_installation_instructions(self):
        with patch.dict("sys.modules", {"cv2": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements.txt"):
                require_opencv()

    def test_latest_camera_keeps_only_newest_frame_and_stops_reader(self):
        cv, capture = self.cv()
        second_read = Event()
        finish = Event()
        frames = [SimpleNamespace(size=1), SimpleNamespace(size=2)]
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            if calls <= 2:
                return True, frames[calls - 1]
            second_read.set()
            if not finish.wait(1):
                raise RuntimeError("test reader was not released")
            return True, frames[-1]

        capture.read.side_effect = read
        with patch("zeroframe.camera.require_opencv", return_value=cv), \
             patch("zeroframe.camera.monotonic", return_value=10):
            with LatestCamera() as stream:
                try:
                    self.assertTrue(second_read.wait(1))
                    self.assertIs(stream.read_after(0)[1], frames[1])
                    self.assertIsNone(stream.read_after(10, timeout=0))
                finally:
                    stream._stop.set()
                    finish.set()
            self.assertIsNone(stream._latest)
        capture.release.assert_called_once()

    def test_latest_camera_propagates_failure_and_releases_device(self):
        cv, capture = self.cv()
        capture.read.return_value = (False, None)
        with patch("zeroframe.camera.require_opencv", return_value=cv):
            with LatestCamera() as stream:
                with self.assertRaisesRegex(RuntimeError, "frame unavailable"):
                    stream.read_after(0, timeout=1)
        capture.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
