"""Metadata-only status reports, with one worker and a latest-snapshot buffer."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
from math import isfinite
import os
from pathlib import Path
from threading import Condition, Thread
from time import monotonic
from urllib.parse import quote, urlsplit

from .roi import validate_ids
from .state import Reason, SeatState, Status, _validate_seconds


ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_SECONDS = 10.0
STALE_SECONDS = 30.0


@dataclass(frozen=True)
class TransportConfig:
    backend_url: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 2.0

    def __post_init__(self):
        if not isinstance(self.backend_url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in self.backend_url):
            raise ValueError("backend_url must be an HTTP(S) base URL without whitespace")
        try:
            parts = urlsplit(self.backend_url)
            port = parts.port
            valid = (parts.scheme in ("http", "https") and parts.hostname
                     and not parts.username and not parts.password and not parts.query and not parts.fragment
                     and (port is None or 1 <= port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("backend_url must be a base URL without credentials, query, or fragment")
        if parts.scheme == "http":
            try:
                loopback = ipaddress.ip_address(parts.hostname).is_loopback
            except ValueError:
                loopback = parts.hostname == "localhost"
            if not loopback:
                raise ValueError("Use HTTPS for backend transmission; HTTP is allowed only on loopback")
        if (not isinstance(self.api_key, str) or not self.api_key
                or any(not 33 <= ord(c) <= 126 for c in self.api_key)):
            raise ValueError("api_key must be a nonempty printable ASCII token without whitespace")
        _validate_seconds("timeout_seconds", self.timeout_seconds, positive=True)
        if self.timeout_seconds > 5:
            raise ValueError("timeout_seconds must not exceed 5 seconds")


def load_transport_config(path=None):
    """Optional repository-local JSON; environment values override its credentials."""
    config_path = Path(path) if path is not None else ROOT / "transport_config.json"
    values = {}
    if config_path.exists() or path is not None:
        with config_path.open(encoding="utf-8") as source:
            try:
                values = json.load(source)
            except json.JSONDecodeError:
                raise ValueError("Invalid transport configuration JSON") from None
        if not isinstance(values, dict) or set(values) - {"backend_url", "api_key", "timeout_seconds"}:
            raise ValueError("Transport settings allow only backend_url, api_key, timeout_seconds")
    for environment, key in (("ZEROFRAME_BACKEND_URL", "backend_url"), ("EDGE_NODE_SECRET_KEY", "api_key")):
        if environment in os.environ:
            values[key] = os.environ[environment]
    if not values and path is None:
        return None
    if not {"backend_url", "api_key"} <= values.keys():
        raise ValueError("Set both backend_url and api_key for backend transmission")
    return TransportConfig(**values)


@dataclass(frozen=True)
class ApiClock:
    monotonic_origin: float
    utc_origin: datetime

    def __post_init__(self):
        _validate_seconds("monotonic_origin", self.monotonic_origin)
        if not isinstance(self.utc_origin, datetime) or self.utc_origin.utcoffset() is None:
            raise ValueError("utc_origin must be a timezone-aware datetime")

    def timestamp(self, when):
        _validate_seconds("timestamp", when)
        utc = self.utc_origin.astimezone(timezone.utc) + timedelta(seconds=when - self.monotonic_origin)
        return utc.isoformat(timespec="milliseconds")


def effective_states(states, *, now):
    """Expire snapshots even if a camera/model call has stopped producing updates."""
    result = {}
    _validate_seconds("now", now)
    for seat_id, state in states.items():
        if not isinstance(state, SeatState) or not isinstance(state.status, Status):
            raise ValueError("Only SeatState metadata can be reported")
        if state.reason is not None and not isinstance(state.reason, Reason):
            raise ValueError("State reason must be a known enum")
        for value in (state.status_since, state.last_observed_at):
            if value is not None:
                _validate_seconds("state timestamp", value)
                if value > now:
                    raise ValueError("Cannot report a future state or observation")
        reference = state.last_observed_at if state.last_observed_at is not None else state.status_since
        if now - reference >= STALE_SECONDS:
            state = replace(state, status=Status.UNKNOWN, reason=Reason.STALE_OBSERVATION)
        result[seat_id] = state
    return result


def build_report(room_id, states, *, now, started_at, inference_count, api_clock):
    validate_ids(room_id, list(states))
    _validate_seconds("started_at", started_at)
    if now < started_at or isinstance(inference_count, bool) or not isinstance(inference_count, int) or inference_count < 0:
        raise ValueError("Invalid report interval or inference count")
    seats = []
    for seat_id, state in effective_states(states, now=now).items():
        seat = {"seat_id": seat_id, "status": state.status.value, "confidence": None}
        if state.status == Status.UNKNOWN:
            seat["decision_source"] = "FALLBACK"
            if state.reason is not None:
                seat["reason"] = state.reason.value
        elif state.status in (Status.OCCUPIED, Status.AWAY):
            seat["decision_source"] = "YOLO"
        elif state.vlm_verified:
            seat["decision_source"] = "YOLO_VLM"
        if state.last_observed_at is not None:
            seat["last_observed_at"] = api_clock.timestamp(state.last_observed_at)
        seats.append(seat)
    return {"room_id": room_id, "timestamp": api_clock.timestamp(now),
            "inference_fps": inference_count / max(now - started_at, 0.001),
            "saved_frames_count": 0, "seats": seats}


def report_signature(report):
    # Observation freshness and measured FPS belong in the periodic full report.
    return tuple((seat["seat_id"], seat["status"], seat.get("reason")) for seat in report["seats"])


def validate_report(report):
    fields = {"room_id", "timestamp", "inference_fps", "saved_frames_count", "seats"}
    if not isinstance(report, dict) or set(report) != fields:
        raise ValueError("Invalid report fields")
    if not isinstance(report["seats"], list) or not report["seats"]:
        raise ValueError("Seats must be a nonempty list")
    ids = []
    for seat in report["seats"]:
        if (not isinstance(seat, dict) or not {"seat_id", "status", "confidence"} <= seat.keys()
                or set(seat) - {"seat_id", "status", "confidence", "decision_source", "last_observed_at", "reason"}):
            raise ValueError("Invalid seat metadata")
        ids.append(seat["seat_id"])
        if seat["status"] not in ("OCCUPIED", "AWAY", "EMPTY", "UNKNOWN"):
            raise ValueError("Invalid status")
        confidence = seat["confidence"]
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                       or not isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("Invalid confidence")
        if "decision_source" in seat and seat["decision_source"] not in ("YOLO", "YOLO_VLM", "FALLBACK"):
            raise ValueError("Invalid decision_source")
        if "reason" in seat and seat["reason"] not in (
            "OCCLUDED", "LOW_CONFIDENCE", "MODEL_CONFLICT", "VLM_TIMEOUT", "STALE_OBSERVATION", "EDGE_OFFLINE"
        ):
            raise ValueError("Invalid reason")
        if "last_observed_at" in seat:
            _timestamp(seat["last_observed_at"])
    validate_ids(report["room_id"], ids)
    _timestamp(report["timestamp"])
    fps = report["inference_fps"]
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not isfinite(fps) or fps < 0:
        raise ValueError("Invalid inference_fps")
    count = report["saved_frames_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("Invalid saved_frames_count")


def _timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
        raise ValueError("Expected timezone-aware ISO timestamp")


def post_report(config, report):
    """Direct HTTP(S), no redirects/proxy environment, and no credential logging."""
    validate_report(report)
    parts = urlsplit(config.backend_url)
    path = (parts.path.rstrip("/") + "/api/v1/rooms/"
            + quote(report["room_id"], safe="") + "/seats/status")
    connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parts.hostname, parts.port, timeout=config.timeout_seconds)
    body = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    try:
        connection.request("POST", path, body=body, headers={
            "Content-Type": "application/json", "X-Edge-Api-Key": config.api_key,
        })
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Backend HTTP {response.status}")
        data = response.read(65537)
        if len(data) > 65536:
            raise RuntimeError("Backend response too large")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError):
            raise RuntimeError("Invalid backend acknowledgement") from None
        if (not isinstance(result, dict) or result.get("ok") is not True
                or isinstance(result.get("updated_count"), bool)
                or not isinstance(result.get("updated_count"), int) or result["updated_count"] < 0
                or not isinstance(result.get("server_time"), str)):
            raise RuntimeError("Invalid backend acknowledgement")
        try:
            _timestamp(result["server_time"])
        except ValueError:
            raise RuntimeError("Invalid backend acknowledgement") from None
    finally:
        connection.close()


class StatusPublisher:
    """One in-flight report and one latest metadata snapshot; no history queue."""

    def __init__(self, config, room_id, states, *, started_at, clock=monotonic, api_clock=None, sender=post_report):
        validate_ids(room_id, list(states))
        self.config = config
        self.room_id = room_id
        self.started_at = started_at
        self.clock = clock
        self.api_clock = api_clock or ApiClock(started_at, datetime.now(timezone.utc))
        self.sender = sender
        self._condition = Condition()
        self._stopped = False
        self._thread = None
        self._latest = (dict(states), 0)
        self._seat_ids = frozenset(states)
        self._last_signature = None
        self._last_sent_at = None
        self._next_attempt_at = started_at
        self._failures = 0
        self.sent_count = 0
        self.failure_count = 0

    def update(self, states, *, inference_count):
        if frozenset(states) != self._seat_ids or any(not isinstance(value, SeatState) for value in states.values()):
            raise ValueError("Publisher accepts only its calibrated seats and SeatState metadata")
        if isinstance(inference_count, bool) or not isinstance(inference_count, int) or inference_count < 0:
            raise ValueError("inference_count must be a nonnegative integer")
        with self._condition:
            self._latest = (dict(states), inference_count)
            self._condition.notify_all()

    def send_due(self, now):
        """One worker step; explicit time also supports deterministic scheduling tests."""
        with self._condition:
            if self._stopped or now < self._next_attempt_at:
                return False
            states, count = self._latest
        report = build_report(self.room_id, states, now=now, started_at=self.started_at,
                              inference_count=count, api_clock=self.api_clock)
        signature = report_signature(report)
        if (signature == self._last_signature and self._last_sent_at is not None
                and now - self._last_sent_at < HEARTBEAT_SECONDS):
            return False
        try:
            self.sender(self.config, report)
        except Exception:
            # Never print arbitrary HTTP errors/bodies: they could include the API key.
            self.failure_count += 1
            self._failures += 1
            self._next_attempt_at = max(now, self.clock()) + min(2 ** min(self._failures - 1, 4), 10)
            if self._failures == 1:
                print("Backend transmission failed; analysis continues. Retrying the latest state.", flush=True)
            return False
        self.sent_count += 1
        self._last_signature = signature
        self._last_sent_at = now
        self._next_attempt_at = now
        if self._failures:
            print("Backend connection recovered; latest state sent.", flush=True)
        self._failures = 0
        return True

    def _work(self):
        while True:
            with self._condition:
                if self._stopped:
                    return
            self.send_due(self.clock())
            with self._condition:
                if not self._stopped:
                    self._condition.wait(timeout=0.25)

    def __enter__(self):
        self._thread = Thread(target=self._work, name="zeroframe-status-publisher", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=self.config.timeout_seconds + 0.5)
        with self._condition:
            self._latest = ({}, 0)
