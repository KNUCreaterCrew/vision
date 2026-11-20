"""Run local YOLO -> ROI -> seat states. No images are saved or sent."""

import argparse
from contextlib import nullcontext
from math import isfinite
import os
import sys
from time import monotonic, sleep

from zeroframe.camera import LatestCamera, require_opencv
from zeroframe.detection import CLASSES, MAX_RESULT_AGE_SECONDS, SeatTracker, YoloDetector, seat_observations
from zeroframe.empty_reference import EmptyReference
from zeroframe.roi import load_config
from zeroframe.state import TransitionPolicy
from zeroframe.transport import load_transport_config, StatusPublisher


WINDOW = "ZeroFrame - local YOLO (Q/ESC to quit)"
DEFAULT_FPS = 0.1
PREVIEW_FPS = 10
COLORS = {"UNKNOWN": (150, 150, 150), "OCCUPIED": (0, 0, 255),
          "AWAY": (0, 180, 255), "EMPTY": (0, 200, 0)}


def draw_preview(cv2, frame, config, detections, states, *, fps, latency_ms, stale, result_age,
                 reference_label, empty_progress):
    for seat in config.seats:
        color = COLORS[states[seat.seat_id].status.value]
        points = seat.polygon
        for a, b in zip(points, points[1:] + points[:1]):
            cv2.line(frame, a, b, color, 2)
        label = f"{seat.seat_id}: {states[seat.seat_id].status.value}"
        if seat.seat_id in empty_progress:
            label += f" (empty {empty_progress[seat.seat_id]})"
        cv2.putText(frame, label, points[0],
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    for detection in detections:
        x1, y1, x2, y2 = map(int, detection.bbox)
        color = (255, 200, 0) if detection.accepted else (150, 150, 150)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
        cv2.putText(frame, f"{CLASSES.get(detection.class_id, 'other')} {detection.confidence:.2f}",
                    (x1, max(15, y1)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    text = (f"YOLO {fps:.2f} FPS | predict {latency_ms:.0f} ms | "
            f"last check {result_age:.1f}s ago{' (STALE: discarded)' if stale else ''}")
    cv2.putText(frame, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.putText(frame, reference_label, (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.imshow(WINDOW, frame)


def run(config_path, model_path, *, camera=0, fps=DEFAULT_FPS, preview=False, transport_config=None,
        confirm_empty_at_start=False):
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not isfinite(fps) or not 0.1 <= fps <= 3:
        raise ValueError("fps must be between 0.1 and 3")
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
    interval = 1 / fps
    # Allow scheduling jitter while breaking continuity after a missed 10-second check.
    policy = TransitionPolicy(max_observation_gap=max(TransitionPolicy.max_observation_gap, 1.5 * interval),
                              required_empty_checks=3)
    tracker = SeatTracker(config, now=started_at, policy=policy)
    states = {seat_id: machine.state for seat_id, machine in tracker.machines.items()}
    publisher = StatusPublisher(transport, config.room_id, states, started_at=started_at) if transport else None
    previous_at = float("-inf")
    next_analysis = started_at
    next_preview = started_at
    next_report = started_at
    count = 0
    frame = sample = None
    window_open = False
    reference = None
    empty_progress = {}
    enroll = confirm_empty_at_start
    enrollment_deadline = started_at + 15
    reference_label = "All seats visibly empty? Press E to set reference"
    print(f"Local YOLO: one check every {interval:g}s; empty verification needs a confirmed reference.")
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
                if preview:
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        break
                    if key in (ord("e"), ord("E")) and reference is None:
                        enroll = True
                        enrollment_deadline = now + MAX_RESULT_AGE_SECONDS
                        previous_at = max(previous_at, now)
                        next_analysis = now
                next_frame = min(next_preview, next_analysis) if preview else next_analysis
                if now < next_frame:
                    remaining = next_frame - now
                    sleep(min(0.02, remaining) if preview else remaining)
                    continue
                sample = stream.read_after(previous_at)
                if sample is None:
                    if enroll and now >= enrollment_deadline:
                        enroll = False
                        reference_label = "No camera frame. Clear all seats and retry E."
                        print(reference_label, flush=True)
                    if now < next_analysis:
                        continue
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
                if now >= next_analysis:
                    inference_start = monotonic()
                    detections = detector.detect(frame)
                    finished_at = monotonic()
                    latency_ms = (finished_at - inference_start) * 1000
                    matches = None
                    if enroll:
                        enroll = False
                        try:
                            if finished_at > enrollment_deadline:
                                raise ValueError("confirmation expired")
                            if finished_at - observed_at > MAX_RESULT_AGE_SECONDS:
                                enroll = confirm_empty_at_start and not preview
                                raise ValueError("stale frame")
                            observations = seat_observations(config, detections, observed_at=observed_at,
                                                             frame_width=width, frame_height=height)
                            reference = EmptyReference(config, frame, observations)
                            finished_at = monotonic()
                            if finished_at - observed_at > MAX_RESULT_AGE_SECONDS:
                                reference.close()
                                reference = None
                                raise ValueError("stale frame")
                            reference_label = "Empty reference active"
                        except (ValueError, OSError, RuntimeError) as error:
                            reference_label = (f"Waiting for fresh reference: {error}" if enroll else
                                               f"Reference rejected: {error}. Retry with all seats clear.")
                        print(reference_label, flush=True)
                    elif reference is not None and finished_at - observed_at <= MAX_RESULT_AGE_SECONDS:
                        matches = reference.compare(frame)
                        finished_at = monotonic()
                    stale = finished_at - observed_at > MAX_RESULT_AGE_SECONDS
                    states = tracker.update(detections, observed_at=observed_at, now=finished_at,
                                            frame_width=width, frame_height=height, empty_matches=matches)
                    if reference is not None:
                        empty_progress = {
                            seat_id: f"{machine.verified_empty_checks}/{policy.required_empty_checks}"
                            for seat_id, machine in tracker.machines.items() if states[seat_id].status.value == "UNKNOWN"
                        }
                    last_analysis_at = observed_at
                    count += 1
                    if publisher is not None:
                        publisher.update(states, inference_count=count)
                    measured_fps = count / max(finished_at - started_at, 0.001)
                    if finished_at >= next_report:
                        seat_log = []
                        for seat_id, state in states.items():
                            detail = f"{seat_id}={state.status.value}"
                            if state.reason:
                                detail += f"({state.reason.value})"
                            if matches is not None:
                                reference_result = {True: "match", None: "marginal", False: "different"}
                                detail += f"[ref={reference_result[matches[seat_id]]}]"
                            if seat_id in empty_progress:
                                detail += f"[empty={empty_progress[seat_id]}]"
                            seat_log.append(detail)
                        print(f"analysis={measured_fps:.2f} FPS predict={latency_ms:.0f}ms "
                              f"stale={stale} | " + " ".join(seat_log), flush=True)
                        next_report = finished_at + 1
                    next_analysis = max(inference_start + interval, finished_at)
                if preview:
                    displayed_at = monotonic()
                    draw_preview(cv2, frame, config, detections, states,
                                 fps=measured_fps, latency_ms=latency_ms, stale=stale,
                                 result_age=displayed_at - last_analysis_at, reference_label=reference_label,
                                 empty_progress=empty_progress)
                    next_preview = displayed_at + 1 / PREVIEW_FPS
                frame = None
    except cv2.error as error:
        raise RuntimeError("OpenCV failed; check the local display/camera or omit --preview.") from error
    finally:
        frame = sample = None
        if reference is not None:
            reference.close()
        detector.release_frames()
        if window_open:
            cv2.destroyAllWindows()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="roi_config.json", help="calibrated coordinate JSON")
    parser.add_argument("--model", default="local/yolo11n.pt", help="existing trusted local .pt/.onnx/NCNN model")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS,
                        help="target analysis FPS (0.1-3, default 0.1: one check every 10 seconds)")
    parser.add_argument("--preview", action="store_true", help="show frames on the local desktop only")
    parser.add_argument("--confirm-empty-at-start", action="store_true",
                        help="confirm ALL calibrated seats are clear of people and belongings for the first frame")
    parser.add_argument("--transport-config", help="backend settings JSON; defaults to repository transport_config.json")
    args = parser.parse_args(argv)
    try:
        run(args.config, args.model, camera=args.camera, fps=args.fps, preview=args.preview,
            transport_config=args.transport_config, confirm_empty_at_start=args.confirm_empty_at_start)
    except KeyboardInterrupt:
        print("Stopped; camera released. No images saved.")
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"Vision error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
