"""Local-camera access only; frames are never encoded, saved, or transmitted."""

import sys
from contextlib import contextmanager
from threading import Condition, Event, Thread
from time import monotonic


def require_opencv():
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError(
            "OpenCV is unavailable. Windows: install requirements.txt in a CPython venv. "
            "Raspberry Pi OS: install python3-opencv and use a --system-site-packages venv. "
            "See README.md for OS-specific setup."
        ) from error
    return cv2


@contextmanager
def camera_session(index: int = 0):
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("camera index must be a nonnegative integer")
    cv2 = require_opencv()
    if sys.platform == "win32":
        backend = cv2.CAP_DSHOW
    elif sys.platform.startswith("linux"):
        backend = cv2.CAP_V4L2
    else:
        backend = cv2.CAP_ANY
    capture = cv2.VideoCapture(index, backend)
    try:
        if not capture.isOpened():
            raise RuntimeError(
                f"Cannot open camera {index}. Check camera permission, other apps, or --camera."
            )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        yield capture
    finally:
        capture.release()


def read_frame(capture):
    ok, frame = capture.read()
    if not ok or frame is None or frame.size == 0:
        raise RuntimeError("Camera frame unavailable; check permission and reconnect the camera.")
    return frame


class LatestCamera:
    """Continuously drain a local camera, retaining only the newest frame in RAM."""

    def __init__(self, index=0):
        self.index = index
        self._condition = Condition()
        self._stop = Event()
        self._latest = None
        self._error = None

    def __enter__(self):
        self._thread = Thread(target=self._capture, daemon=True)
        self._thread.start()
        return self

    def _capture(self):
        frame = None
        try:
            with camera_session(self.index) as capture:
                while not self._stop.is_set():
                    frame = read_frame(capture)
                    captured_at = monotonic()
                    with self._condition:
                        self._latest = (captured_at, frame)
                        self._condition.notify_all()
                    frame = None
        except Exception as error:
            with self._condition:
                self._error = str(error)
                self._condition.notify_all()
        finally:
            frame = None
            with self._condition:
                self._latest = None

    def read_after(self, previous_at, timeout=0.1):
        with self._condition:
            self._condition.wait_for(
                lambda: self._error is not None or (
                    self._latest is not None and self._latest[0] > previous_at
                ), timeout=timeout,
            )
            if self._error is not None:
                raise RuntimeError(self._error)
            if self._latest is not None and self._latest[0] > previous_at:
                return self._latest
            return None

    def __exit__(self, *exc):
        self._stop.set()
        # A failed device driver can block read(); do not wait forever on shutdown.
        self._thread.join(timeout=2)
        with self._condition:
            self._latest = None
        if self._thread.is_alive():
            raise RuntimeError("Camera driver did not stop; close this process before reopening the camera.")
