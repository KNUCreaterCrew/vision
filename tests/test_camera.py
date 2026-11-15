from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zeroframe.camera import camera_session, read_frame, require_opencv


class CameraTests(unittest.TestCase):
    def cv(self):
        capture = Mock()
        capture.isOpened.return_value = True
        return SimpleNamespace(
            VideoCapture=Mock(return_value=capture),
            CAP_DSHOW=700, CAP_ANY=0, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
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


if __name__ == "__main__":
    unittest.main()
