"""Run local YOLO -> ROI -> seat states. No images are saved or sent."""

import argparse
from contextlib import nullcontext
from math import isfinite
import os
import sys
from time import monotonic, sleep

from zeroframe.camera import LatestCamera, require_opencv
from zeroframe.detection import CLASSES, SeatTracker, YoloDetector
from zeroframe.roi import load_config
from zeroframe.transport import load_transport_config, StatusPublisher


WINDOW = "ZeroFrame - local YOLO (Q/ESC to quit)"
COLORS = {"UNKNOWN": (150, 150, 150), "OCCUPIED": (0, 0, 255),
          "AWAY": (0, 180, 255), "EMPTY": (0, 200, 0)}


def draw_preview(cv2, frame, config, detections, states, *, fps, latency_ms, stale):
    for seat in config.seats:
        color = COLORS[states[seat.seat_id].status.value]
        points = seat.polygon
        for a, b in zip(points, points[1:] + points[:1]):
            cv2.line(frame, a, b, color, 2)
        cv2.putText(frame, f"{seat.seat_id}: {states[seat.seat_id].status.value}", points[0],
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    for detection in detections:
        x1, y1, x2, y2 = map(int, detection.bbox)
        color = (255, 200, 0) if detection.accepted else (150, 150, 150)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
        cv2.putText(frame, f"{CLASSES.get(detection.class_id, 'other')} {detection.confidence:.2f}",
                    (x1, max(15, y1)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    text = f"analysis {fps:.2f} FPS | predict {latency_ms:.0f} ms | {'STALE: discarded' if stale else 'RAM only'}"
    cv2.putText(frame, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.imshow(WINDOW, frame)


def run(config_path, model_path, *, camera=0, fps=2.0, preview=False, transport_config=None):
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not isfinite(fps) or not 1 <= fps <= 3:
        raise ValueError("fps must be between 1 and 3")
    if isinstance(camera, bool) or not isinstance(camera, int) or camera < 0:
        raise ValueError("camera index must be a nonnegative integer")
    config = load_config(config_path)
    if preview and sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        raise RuntimeError("Preview needs a local Linux desktop; omit --preview for headless use.")
    transport = load_transport_config(transport_config)
    detector = YoloDetector(model_path)
    cv2 = require_opencv()
    started_at = monotonic()
    tracker = SeatTracker(config, now=started_at)
    initial_states = {seat_id: machine.state for seat_id, machine in tracker.machines.items()}
    publisher = StatusPublisher(transport, config.room_id, initial_states, started_at=started_at) if transport else None
    previous_at = float("-inf")
    next_analysis = started_at
    next_report = started_at
    count = 0
    frame = sample = None
    window_open = False
    print("Local YOLO: camera frames stay in RAM; no EMPTY verification.")
    print("Backend transmission: metadata only, change reports + 10s heartbeat." if publisher else
          "Backend transmission disabled: no transport_config.json or backend environment settings.")
    print("Q/ESC closes preview; Ctrl+C stops headless mode.")
    try:
        if preview:
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
            window_open = True
        with publisher if publisher is not None else nullcontext(), LatestCamera(camera) as stream:
            while True:
                now = monotonic()
                if preview and (cv2.waitKey(1) & 0xFF in (27, ord("q"))
                                or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1):
                    break
                if now < next_analysis:
                    sleep(min(0.02, next_analysis - now))
                    continue
                sample = stream.read_after(previous_at)
                if sample is None:
                    states = tracker.tick(monotonic())
                    if publisher is not None:
                        publisher.update(states, inference_count=count)
                    if monotonic() >= next_report:
                        print("Waiting for camera | " + " ".join(f"{s}={v.status.value}" for s, v in states.items()), flush=True)
                        next_report = monotonic() + 1
                    continue
                observed_at, frame = sample
                sample = None
                previous_at = observed_at
                height, width = frame.shape[:2]
                if (width, height) != (config.frame_width, config.frame_height):
                    raise ValueError("frame resolution changed; recalibrate with this camera")
                inference_start = monotonic()
                detections = detector.detect(frame)
                finished_at = monotonic()
                latency_ms = (finished_at - inference_start) * 1000
                stale = finished_at - observed_at > 2
                states = tracker.update(detections, observed_at=observed_at, now=finished_at,
                                        frame_width=width, frame_height=height)
                count += 1
                if publisher is not None:
                    publisher.update(states, inference_count=count)
                measured_fps = count / max(finished_at - started_at, 0.001)
                if finished_at >= next_report:
                    print(f"analysis={measured_fps:.2f} FPS predict={latency_ms:.0f}ms "
                          f"stale={stale} | " + " ".join(f"{s}={v.status.value}" for s, v in states.items()), flush=True)
                    next_report = finished_at + 1
                if preview:
                    draw_preview(cv2, frame, config, detections, states,
                                 fps=measured_fps, latency_ms=latency_ms, stale=stale)
                frame = None
                next_analysis = max(inference_start + 1 / fps, finished_at)
    except cv2.error as error:
        raise RuntimeError("OpenCV failed; check the local display/camera or omit --preview.") from error
    finally:
        frame = sample = None
        detector.release_frames()
        if window_open:
            cv2.destroyAllWindows()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="roi_config.json", help="calibrated coordinate JSON")
    parser.add_argument("--model", default="local/yolo11n.pt", help="existing trusted local .pt/.onnx/NCNN model")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--fps", type=float, default=2, help="target analysis FPS (1-3), not guaranteed throughput")
    parser.add_argument("--preview", action="store_true", help="show frames on the local desktop only")
    parser.add_argument("--transport-config", help="backend settings JSON; defaults to repository transport_config.json")
    args = parser.parse_args(argv)
    try:
        run(args.config, args.model, camera=args.camera, fps=args.fps, preview=args.preview,
            transport_config=args.transport_config)
    except KeyboardInterrupt:
        print("Stopped; camera released. No images saved.")
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"Vision error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
