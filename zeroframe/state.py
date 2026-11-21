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
    required_empty_checks: int = 1

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            _validate_seconds(name, value, positive=True)
        if not isinstance(self.required_empty_checks, int):
            raise ValueError("required_empty_checks must be an integer")
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
    vlm_verified: bool = False


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
        self._verified_checks = 0
        self.vlm_review_at = None
        self.vlm_first_empty_at = None
        self._state = SeatState(Status.UNKNOWN, now, reason=Reason.STALE_OBSERVATION)

    @property
    def state(self) -> SeatState:
        return self._state

    @property
    def verified_empty_checks(self) -> int:
        return self._verified_checks

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
            self._verified_checks = 0
            self.cancel_vlm_review()
        last_valid = self._state.last_observed_at
        reference = self._started_at if last_valid is None else last_valid
        if now - reference >= self.policy.stale_seconds:
            if self._state.status != Status.UNKNOWN:
                self.cancel_vlm_review()
            self._unknown(Reason.STALE_OBSERVATION, now)
        return self._state

    def cancel_vlm_review(self):
        self.vlm_review_at = self.vlm_first_empty_at = None

    def review_vlm(self, label: Status, captured: float, now: float) -> bool:
        """Consume one reply for the latest reviewable observation of this seat."""
        self.tick(now)
        if self.vlm_review_at != captured or not 0 <= now - captured < 15:
            return False
        self.vlm_review_at = None
        if label != Status.EMPTY:
            self.vlm_first_empty_at = None
            if label in (Status.OCCUPIED, Status.AWAY) and self.state.status == Status.EMPTY:
                self._unknown(Reason.MODEL_CONFLICT, now)
                return True
            return False
        previous = self.vlm_first_empty_at
        self.vlm_first_empty_at = captured
        if self.state.status != Status.EMPTY and (
            previous is None or captured - self._candidate_since < self.policy.empty_seconds
        ):
            return False
        self.vlm_first_empty_at = None
        since = self.state.status_since if self.state.status == Status.EMPTY else now
        self._state = SeatState(Status.EMPTY, since, captured, vlm_verified=True)
        return True

    def observe(self, observation: Observation, *, marginal_reference=False) -> SeatState:
        """Consume a new observation; ignore duplicates and late arrivals."""
        now = observation.observed_at
        if now < self._now or (
            self._last_input_at is not None and now <= self._last_input_at
        ):
            return self._state
        self.tick(now)
        follow_up = self.vlm_first_empty_at is not None and self.vlm_first_empty_at == self._last_input_at
        reviewable = (marginal_reference or (follow_up and observation.empty_verified)) and (
            observation.view_clear and not observation.person and not observation.belongings
        )
        if not reviewable or not follow_up:
            self.vlm_first_empty_at = None
        self.vlm_review_at = now if reviewable else None
        self._last_input_at = now

        reason = None
        if not observation.view_clear:
            reason = Reason.OCCLUDED
        elif observation.empty_verified and (observation.person or observation.belongings):
            reason = Reason.MODEL_CONFLICT
        if reason or observation.person or observation.belongings or not observation.empty_verified:
            self._verified_checks = 0
        else:
            self._verified_checks = min(self.policy.required_empty_checks, self._verified_checks + 1)
        if self._state.status == Status.EMPTY and (reason or observation.person or observation.belongings):
            self._unknown(reason or Reason.LOW_CONFIDENCE, now)
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

        if candidate == Status.EMPTY:
            needs_confirmation = self._state.status != Status.EMPTY
            if not observation.empty_verified or (
                needs_confirmation and self._verified_checks < self.policy.required_empty_checks
            ):
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
            vlm_verified=False,
        )
