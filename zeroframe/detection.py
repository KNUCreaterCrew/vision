"""Local YOLO inference and conservative, image-free seat observations."""

from dataclasses import dataclass, replace
from math import isfinite
import os
from pathlib import Path

from .state import Observation, SeatStateMachine, Status, TransitionPolicy, _validate_seconds


CLASSES = {
    0: "person", 24: "backpack", 26: "handbag", 39: "bottle",
    63: "laptop", 67: "cell phone", 73: "book",
}
MAX_RESULT_AGE_SECONDS = 2.0
ITEM_LABELS = {24: "백팩", 26: "핸드백", 39: "물병", 63: "노트북", 67: "휴대폰", 73: "책"}


@dataclass(frozen=True)
class Detection:
    class_id: int
    confidence: float
    bbox: tuple[float, float, float, float]

    def __post_init__(self):
        if isinstance(self.class_id, bool) or not isinstance(self.class_id, int) or self.class_id < 0:
            raise ValueError("class_id must be a nonnegative integer")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be finite and between 0 and 1")
        if not isinstance(self.bbox, (tuple, list)) or len(self.bbox) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
            for v in self.bbox
        ):
            raise ValueError("bbox must contain four finite coordinates")
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 and 0 <= y1 < y2):
            raise ValueError("bbox must be nonnegative with positive area")
        object.__setattr__(self, "bbox", tuple(self.bbox))

    @property
    def accepted(self):
        return self.class_id in CLASSES and self.confidence >= (
            0.30 if self.class_id == 0 else 0.20
        )


def seat_observations(config, detections, *, observed_at, frame_width, frame_height): #탐지 결과를 좌석 매핑 및 사람, 물건신호로 분류
    if (frame_width, frame_height) != (config.frame_width, config.frame_height):
        raise ValueError("frame resolution changed; recalibrate instead of silently scaling ROIs")
    people, belongings = set(), set()
    unclear_people, unclear_belongings = set(), set()
    for detection in detections:
        if detection.class_id not in CLASSES:
            continue
        overlaps = config.overlapping_seat_ratios(
            detection.bbox, frame_width=frame_width, frame_height=frame_height,
            require_upper_center=detection.class_id == 0,
        )
        minimum_overlap = 1 / 3 if detection.class_id == 0 else 1 / 2
        candidates = [seat_id for seat_id, fraction in overlaps.items() if fraction >= minimum_overlap]
        if len(candidates) != 1 or not detection.accepted:
            unclear = unclear_people if detection.class_id == 0 else unclear_belongings
            unclear.update(overlaps)
            continue
        assigned = people if detection.class_id == 0 else belongings
        assigned.add(candidates[0])

    observations = {}
    for seat in config.seats:
        seat_id = seat.seat_id
        person = seat_id in people
        item = seat_id in belongings
        clear = person or (seat_id not in unclear_people and (item or seat_id not in unclear_belongings))
        observations[seat_id] = Observation(observed_at, person=person, belongings=item, view_clear=clear)
    return observations


class SeatTracker:
    """Reject old inference using capture time, then advance freshness to completion time."""

    def __init__(self, config, *, now, policy: TransitionPolicy | None = None):
        self.config = config
        self._now = now
        self.machines = {seat.seat_id: SeatStateMachine(policy, now=now) for seat in config.seats}

    def tick(self, now):
        states = {seat_id: machine.tick(now) for seat_id, machine in self.machines.items()}
        self._now = now
        return states

    def update(self, detections, *, observed_at, now, frame_width, frame_height, empty_matches=None,
               visible_seats=()):
        _validate_seconds("now", now)
        _validate_seconds("observed_at", observed_at)
        if now < self._now:
            raise ValueError("clock must not move backwards")
        if observed_at > now:
            raise ValueError("capture time cannot be after completion time")
        observations = seat_observations(
            self.config, detections, observed_at=observed_at,
            frame_width=frame_width, frame_height=frame_height,
        )
        if now - observed_at <= MAX_RESULT_AGE_SECONDS:
            for seat_id, machine in self.machines.items():
                obs = observations[seat_id]
                marginal_reference = False
                visible_change = False
                if empty_matches is not None and not (obs.person or obs.belongings):
                    match = empty_matches[seat_id]
                    if obs.view_clear:
                        marginal_reference = match is None
                        visible_change = match is False and seat_id in visible_seats
                        obs = replace(obs, view_clear=match is not False, empty_verified=match is True)
                    elif match is not False and machine.state.status == Status.EMPTY:
                        # Ambiguous YOLO overlap without a clear visual change may hold
                        # its last EMPTY until expiry, but cannot verify or refresh it.
                        obs = replace(obs, view_clear=True)
                machine.observe(obs, marginal_reference=marginal_reference, visible_change=visible_change)
        else:
            for machine in self.machines.values():
                machine.cancel_vlm_review()
        return self.tick(now)


class YoloDetector:
    """A trusted local COCO model only; no runtime downloads or image outputs."""

    def __init__(self, model_path, *, item_ids=tuple(ITEM_LABELS)):
        selected = set(item_ids)
        if not selected.issubset(ITEM_LABELS):
            raise ValueError("Unknown belongings class")
        self.class_ids = [0, *(item for item in ITEM_LABELS if item in selected)]
        path = Path(model_path)
        if not (
            (path.is_file() and path.suffix.lower() in (".pt", ".onnx"))
            or (path.is_dir() and path.name.endswith("_ncnn_model")
                and all((path / name).is_file() for name in
                        ("model.ncnn.param", "model.ncnn.bin", "metadata.yaml")))
        ):
            raise ValueError("Provide an existing local .pt, .onnx, or complete *_ncnn_model. See README.md.")
        os.environ["YOLO_OFFLINE"] = "true"
        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ["YOLO_CONFIG_DIR"] = str(Path("local/ultralytics").resolve())
        try:
            import ultralytics
        except ImportError as error:
            raise RuntimeError("YOLO dependencies unavailable. See requirements-yolo.txt and README.md.") from error
        if ultralytics.__version__ != "8.3.253":
            raise RuntimeError("Use ultralytics==8.3.253; buffer cleanup is verified against that version's source.")
        ultralytics.settings.update({"sync": False, "hub": False})
        self.model = ultralytics.YOLO(str(path.resolve()), task="detect")

    def detect(self, frame):
        # Reject paths/URLs: the only inference source is a RAM-resident BGR frame.
        import numpy as np

        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3
                or frame.shape[2] != 3 or min(frame.shape[:2]) <= 0):
            raise ValueError("YOLO requires a uint8 BGR camera frame")
        results = None
        try:
            results = self.model.predict(
                source=frame, device="cpu", imgsz=640, rect=False,
                # Keep exact 0.20 scores through YOLO's strict confidence prefilter.
                classes=self.class_ids, conf=0.19, stream=False,
                save=False, save_txt=False, save_crop=False, save_frames=False,
                visualize=False, show=False, verbose=False, augment=False,
            )
            if len(results) != 1 or any(results[0].names.get(k) != v for k, v in CLASSES.items()):
                raise ValueError("YOLO model must use the expected COCO detection classes")
            boxes = results[0].boxes
            if boxes is None:
                raise ValueError("YOLO result has no detection boxes")
            return tuple(Detection(int(cls), float(conf), tuple(bbox)) for bbox, cls, conf in zip(
                boxes.xyxy.cpu().tolist(), boxes.cls.cpu().tolist(), boxes.conf.cpu().tolist(), strict=True
            ))
        finally:
            results = None
            self.release_frames()

    def release_frames(self):
        # Ultralytics 8.3.253 retains the last originals in these predictor fields.
        predictor = self.model.predictor
        if predictor is not None:
            predictor.batch = predictor.results = predictor.dataset = predictor.plotted_img = None
