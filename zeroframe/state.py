"""Seat transitions using monotonic seconds, without camera or model dependencies."""

from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite


class Status(str, Enum):
    OCCUPIED = "OCCUPIED"
    AWAY = "AWAY"
    EMPTY = "EMPTY"
    UNKNOWN = "UNKNOWN"


class Reason(str, Enum):
    OCCLUDED = "OCCLUDED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MODEL_CONFLICT = "MODEL_CONFLICT"
    STALE_OBSERVATION = "STALE_OBSERVATION"


def _validate_seconds(name: str, value: float, *, positive: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")


@dataclass(frozen=True)
class TransitionPolicy: #상태확정에 필요한 시간
    occupied_seconds: float = 2.0
    away_seconds: float = 3.0
    occupied_to_away_seconds: float = 4.0
    empty_seconds: float = 5.0
    max_observation_gap: float = 2.0
    stale_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            _validate_seconds(name, value, positive=True)
        if self.max_observation_gap >= self.stale_seconds:
            raise ValueError("max_observation_gap must be less than stale_seconds")


@dataclass(frozen=True)
class Observation:
    observed_at: float
    person: bool = False
    belongings: bool = False
    view_clear: bool = True
    empty_verified: bool = False

    def __post_init__(self) -> None:
        _validate_seconds("observed_at", self.observed_at)
        for name in ("person", "belongings", "view_clear", "empty_verified"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")


@dataclass(frozen=True)
class SeatState:
    status: Status
    status_since: float
    last_observed_at: float | None = None
    reason: Reason | None = None


class SeatStateMachine:
    """One seat; all inputs must use the same monotonic clock.

    empty_verified is a trusted, same-observation verification signal, never a
    synonym for missing YOLO detections. No model generates it in stage 1.
    """

    def __init__(self, policy: TransitionPolicy | None = None, *, now: float = 0.0):
        _validate_seconds("now", now)
        self.policy = policy if policy is not None else TransitionPolicy()
        self._started_at = now
        self._now = now
        self._last_input_at: float | None = None
        self._candidate: Status | None = None
        self._candidate_since = now
        self._state = SeatState(Status.UNKNOWN, now, reason=Reason.STALE_OBSERVATION)

    @property
    def state(self) -> SeatState:
        return self._state

    def tick(self, now: float) -> SeatState:
        """Advance the clock even when the camera supplies no observation."""
        _validate_seconds("now", now)
        if now < self._now:
            raise ValueError("clock must not move backwards")
        self._now = now
        if (
            self._last_input_at is not None
            and now - self._last_input_at > self.policy.max_observation_gap
        ):
            self._candidate = None
        last_valid = self._state.last_observed_at
        reference = self._started_at if last_valid is None else last_valid
        if now - reference >= self.policy.stale_seconds:
            self._unknown(Reason.STALE_OBSERVATION, now)
        return self._state

    def observe(self, observation: Observation) -> SeatState:
        """Consume a new observation; ignore duplicates and late arrivals."""
        now = observation.observed_at
        if now < self._now or (
            self._last_input_at is not None and now <= self._last_input_at
        ):
            return self._state
        self.tick(now)
        self._last_input_at = now

        reason = None
        if not observation.view_clear:
            reason = Reason.OCCLUDED
        elif observation.empty_verified and (observation.person or observation.belongings):
            reason = Reason.MODEL_CONFLICT
        if reason is not None:
            self._candidate = None
            if self._state.status == Status.UNKNOWN:
                self._unknown(reason, now)
            return self._state

        if observation.person: #사람이면 occupied 물건만 있으면 away
            candidate = Status.OCCUPIED
            delay = self.policy.occupied_seconds
        elif observation.belongings:
            candidate = Status.AWAY
            delay = (
                self.policy.occupied_to_away_seconds
                if self._state.status == Status.OCCUPIED
                else self.policy.away_seconds
            )
        else:
            candidate = Status.EMPTY
            delay = self.policy.empty_seconds

        if candidate != self._candidate:
            self._candidate = candidate
            self._candidate_since = now

        if candidate == Status.EMPTY and not observation.empty_verified:
            if self._state.status == Status.UNKNOWN:
                self._unknown(Reason.LOW_CONFIDENCE, now)
            return self._state

        if self._state.status == candidate or now - self._candidate_since >= delay:
            self._state = SeatState(
                status=candidate,
                status_since=(
                    self._state.status_since if self._state.status == candidate else now
                ),
                last_observed_at=now,
            )
        return self._state

    def _unknown(self, reason: Reason, now: float) -> None:
        self._state = replace(
            self._state,
            status=Status.UNKNOWN,
            status_since=(
                self._state.status_since if self._state.status == Status.UNKNOWN else now
            ),
            reason=reason,
        )
