"""Local-camera access only; frames are never encoded, saved, or transmitted."""

import sys
from contextlib import contextmanager


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
