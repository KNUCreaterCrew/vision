import unittest
from dataclasses import FrozenInstanceError, fields

from zeroframe.state import (
    Observation,
    Reason,
    SeatStateMachine,
    Status,
    TransitionPolicy,
)


def feed(machine, times, **signals):
    for now in times:
        machine.observe(Observation(now, **signals))
    return machine.state


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.machine = SeatStateMachine()

    def test_initial_state_is_unknown_and_snapshot_is_immutable(self):
        state = self.machine.state
        self.assertEqual(state.status, Status.UNKNOWN)
        self.assertIsNone(state.last_observed_at)
        with self.assertRaises(FrozenInstanceError):
            state.status = Status.EMPTY

    def test_occupied_requires_two_seconds_and_person_has_priority(self):
        feed(self.machine, [0, 1, 1.99], person=True, belongings=True)
        self.assertEqual(self.machine.state.status, Status.UNKNOWN)
        state = feed(self.machine, [2], person=True, belongings=True)
        self.assertEqual(state.status, Status.OCCUPIED)
        self.assertEqual(state.status_since, 2)

    def test_unknown_to_away_requires_three_seconds(self):
        feed(self.machine, [0, 1, 2, 2.99], belongings=True)
        self.assertEqual(self.machine.state.status, Status.UNKNOWN)
        self.assertEqual(feed(self.machine, [3], belongings=True).status, Status.AWAY)

    def test_occupied_to_away_requires_four_seconds(self):
        feed(self.machine, [0, 1, 2], person=True)
        feed(self.machine, [3, 4, 5, 6, 6.99], belongings=True)
        self.assertEqual(self.machine.state.status, Status.OCCUPIED)
        state = feed(self.machine, [7], belongings=True)
        self.assertEqual(state.status, Status.AWAY)
        self.assertEqual(state.status_since, 7)

    def test_returning_person_can_replace_away(self):
        feed(self.machine, [0, 1, 2, 3], belongings=True)
        feed(self.machine, [4, 5], person=True)
        self.assertEqual(self.machine.state.status, Status.AWAY)
        self.assertEqual(feed(self.machine, [6], person=True).status, Status.OCCUPIED)

    def test_no_detections_alone_never_confirm_empty(self):
        feed(self.machine, range(61))
        self.assertEqual(self.machine.state.status, Status.UNKNOWN)
        self.assertIsNone(self.machine.state.last_observed_at)

    def test_empty_requires_sustained_absence_and_current_verification(self):
        feed(self.machine, range(5))
        state = feed(self.machine, [5], empty_verified=True)
        self.assertEqual(state.status, Status.EMPTY)
        self.assertEqual(state.last_observed_at, 5)

    def test_one_early_verification_does_not_confirm_empty_later(self):
        feed(self.machine, [0], empty_verified=True)
        feed(self.machine, range(1, 10))
        self.assertEqual(self.machine.state.status, Status.UNKNOWN)

    def test_occupied_and_away_need_verification_to_become_empty(self):
        for signals in ({"person": True}, {"belongings": True}):
            with self.subTest(signals=signals):
                machine = SeatStateMachine()
                original = feed(machine, range(4), **signals).status
                feed(machine, range(4, 9))
                self.assertEqual(machine.state.status, original)
                self.assertEqual(
                    feed(machine, [9], empty_verified=True).status, Status.EMPTY
                )

    def test_ambiguous_frames_do_not_refresh_confirmed_state(self):
        feed(self.machine, [0, 1, 2], person=True)
        feed(self.machine, range(3, 32))
        self.assertEqual(self.machine.state.status, Status.OCCUPIED)
        self.assertEqual(self.machine.state.last_observed_at, 2)
        self.assertEqual(feed(self.machine, [32]).status, Status.UNKNOWN)

    def test_camera_silence_expires_at_thirty_seconds_and_recovers(self):
        feed(self.machine, [0, 1, 2], person=True)
        self.assertEqual(self.machine.tick(31.999).status, Status.OCCUPIED)
        state = self.machine.tick(32)
        self.assertEqual(state.status, Status.UNKNOWN)
        self.assertEqual(state.reason, Reason.STALE_OBSERVATION)
        self.assertEqual(state.last_observed_at, 2)
        self.assertEqual(state.status_since, 32)
        self.assertEqual(feed(self.machine, [33, 34], person=True).status, Status.UNKNOWN)
        self.assertEqual(feed(self.machine, [35], person=True).status, Status.OCCUPIED)

    def test_matching_observations_refresh_time_without_changing_status_since(self):
        feed(self.machine, [0, 1, 2], person=True)
        state = feed(self.machine, [3, 4, 5], person=True)
        self.assertEqual(state.status_since, 2)
        self.assertEqual(state.last_observed_at, 5)
        self.assertEqual(self.machine.tick(34).status, Status.OCCUPIED)

    def test_clock_ticks_are_not_new_observations(self):
        feed(self.machine, [0], person=True)
        self.assertEqual(self.machine.tick(2).status, Status.UNKNOWN)
        self.assertEqual(feed(self.machine, [3, 4], person=True).status, Status.UNKNOWN)
        self.assertEqual(feed(self.machine, [5], person=True).status, Status.OCCUPIED)

    def test_irregular_samples_use_elapsed_time_not_frame_count(self):
        feed(self.machine, [0, 0.2, 0.7, 1.8], person=True)
        self.assertEqual(self.machine.state.status, Status.UNKNOWN)
        self.assertEqual(feed(self.machine, [2.1], person=True).status, Status.OCCUPIED)

    def test_gap_resets_empty_confirmation_window(self):
        feed(self.machine, [0, 1])
        self.assertEqual(feed(self.machine, [5], empty_verified=True).status, Status.UNKNOWN)
        feed(self.machine, [6, 7, 8, 9])
        self.assertEqual(feed(self.machine, [10], empty_verified=True).status, Status.EMPTY)

    def test_occlusion_resets_candidate_and_prevents_empty(self):
        feed(self.machine, [0, 1], person=True)
        state = feed(self.machine, [1.5], view_clear=False, empty_verified=True)
        self.assertEqual(state.reason, Reason.OCCLUDED)
        self.assertEqual(feed(self.machine, [2, 3], person=True).status, Status.UNKNOWN)
        self.assertEqual(feed(self.machine, [4], person=True).status, Status.OCCUPIED)

    def test_conflicting_verification_is_not_trusted(self):
        for signals in ({"person": True}, {"belongings": True}):
            with self.subTest(signals=signals):
                machine = SeatStateMachine()
                state = feed(machine, range(6), empty_verified=True, **signals)
                self.assertEqual(state.status, Status.UNKNOWN)
                self.assertEqual(state.reason, Reason.MODEL_CONFLICT)
                self.assertIsNone(state.last_observed_at)

    def test_duplicate_and_late_observations_do_not_overwrite_state(self):
        feed(self.machine, [0, 1, 2], person=True)
        before = self.machine.state
        feed(self.machine, [2, 1], empty_verified=True)
        self.assertEqual(self.machine.state, before)
        self.machine.tick(5)
        feed(self.machine, [4], person=True)
        self.assertEqual(self.machine.state, before)

    def test_empty_is_not_kept_fresh_by_missing_detections(self):
        feed(self.machine, range(5))
        feed(self.machine, [5], empty_verified=True)
        feed(self.machine, range(6, 35))
        self.assertEqual(self.machine.state.status, Status.EMPTY)
        self.assertEqual(self.machine.state.last_observed_at, 5)
        self.assertEqual(feed(self.machine, [35]).status, Status.UNKNOWN)

    def test_unknown_reason_changes_do_not_reset_status_since(self):
        feed(self.machine, [0], view_clear=False)
        state = feed(self.machine, [1])
        self.assertEqual(state.reason, Reason.LOW_CONFIDENCE)
        self.assertEqual(state.status_since, 0)

    def test_policy_settings_are_used(self):
        machine = SeatStateMachine(TransitionPolicy(occupied_seconds=1, stale_seconds=5))
        self.assertEqual(feed(machine, [0, 1], person=True).status, Status.OCCUPIED)
        self.assertEqual(machine.tick(6).status, Status.UNKNOWN)


class ValidationTests(unittest.TestCase):
    def test_policy_rejects_invalid_seconds_for_every_field(self):
        for field in fields(TransitionPolicy):
            for value in (0, -1, float("nan"), float("inf"), True, "2", None):
                with self.subTest(field=field.name, value=value):
                    with self.assertRaises(ValueError):
                        TransitionPolicy(**{field.name: value})
        with self.assertRaises(ValueError):
            TransitionPolicy(max_observation_gap=30)

    def test_observation_and_clock_reject_invalid_times(self):
        for value in (-1, float("nan"), float("inf"), True, "2", None):
            for factory in (Observation, SeatStateMachine().tick):
                with self.subTest(value=value, factory=factory):
                    with self.assertRaises(ValueError):
                        factory(value)
            with self.assertRaises(ValueError):
                SeatStateMachine(now=value)

    def test_observation_rejects_non_boolean_signals(self):
        for name in ("person", "belongings", "view_clear", "empty_verified"):
            for value in (1, "false", None):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        Observation(0, **{name: value})

    def test_clock_cannot_move_backwards(self):
        machine = SeatStateMachine(now=10)
        with self.assertRaises(ValueError):
            machine.tick(9)


if __name__ == "__main__":
    unittest.main()
