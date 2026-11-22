import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import run_vision

from zeroframe.detection import Detection, SeatTracker, seat_observations
from zeroframe.empty_reference import EmptyReference
from zeroframe.roi import ROIConfig, SeatROI
from zeroframe.state import Status, TransitionPolicy


class EmptyReferenceTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV/NumPy unavailable")

        self.config = ROIConfig("room", 160, 80, (
            SeatROI("A", ((0, 0), (79, 0), (79, 79), (0, 79))),
            SeatROI("B", ((80, 0), (159, 0), (159, 79), (80, 79))),
        ))
        self.frame = np.repeat(np.indices((80, 160))[0][:, :, None], 3, axis=2).astype(np.uint8) + 65
        self.clear = seat_observations(self.config, [], observed_at=0, frame_width=160, frame_height=80)

    def enroll(self):
        reference = EmptyReference(self.config, self.frame, self.clear)
        self.addCleanup(reference.close)
        return reference

    def test_one_miss_in_visible_changed_seat_counts_but_dark_seat_resets_votes(self):
        reference = self.enroll()
        changed = self.frame.copy()
        changed[18:58, 18:58] = [150, 40, 160]
        box = (18, 18, 58, 58)
        for cls, expected in ((0, Status.OCCUPIED), (39, Status.AWAY)):
            for middle, usable in ((changed, True), (self.frame * 0, False)):
                with self.subTest(cls=cls, usable=usable):
                    tracker = SeatTracker(self.config, now=0, policy=TransitionPolicy(max_observation_gap=15))
                    hit = [Detection(cls, .5, box)]
                    visible = set()
                    for t, frame, boxes in ((0, changed, hit), (10, middle, []), (20, changed, hit)):
                        matches = reference.compare(frame, visible_seats=visible)
                        self.assertIs(matches["A"], False)
                        states = tracker.update(boxes, observed_at=t, now=t+.1, frame_width=160,
                                                frame_height=80, empty_matches=matches, visible_seats=visible)
                        if t == 10:
                            self.assertEqual("A" in visible, usable)
                            self.assertIsNone(states["A"].last_observed_at)
                    self.assertEqual(states["A"].status, expected if usable else Status.UNKNOWN)

    def test_lights_off_reviews_enhanced_live_seat_and_excludes_object(self):
        import numpy as np
        from zeroframe.vlm import seat_image

        reference = self.enroll()
        # Original contrast and color cast are substantially different after lights off.
        dim = np.rint(self.frame * [.12, .10, .09]).astype(np.uint8)
        dim[24:52, 100:118] = [4, 1, 2]
        self.assertEqual(reference.compare(dim), {"A": False, "B": False})
        curves = {}
        matches = reference.compare(dim, lighting_luts=curves)
        self.assertEqual(matches, {"A": None, "B": False})
        self.assertEqual(set(curves), {"A"})
        tracker = SeatTracker(self.config, now=0, policy=TransitionPolicy(max_observation_gap=15))
        for t in (0, 10):
            tracker.update([], observed_at=t, now=t+.1, frame_width=160, frame_height=80,
                           empty_matches=matches)
            self.assertEqual(tracker.machines["A"].review_vlm(Status.EMPTY, t, t+3), t == 10)
            self.assertFalse(tracker.machines["B"].review_vlm(Status.EMPTY, t, t+3))
        self.assertTrue(tracker.machines["A"].state.vlm_verified)
        self.assertEqual(tracker.machines["B"].state.status, Status.UNKNOWN)
        try:
            import PIL
        except ImportError:
            self.skipTest("optional Pillow unavailable")
        original = dim.copy()
        with seat_image(dim, self.config, "A", lighting_lut=curves["A"]) as image:
            self.assertTrue(image.info["seat_visibility"]["usable"])
            self.assertGreater(np.asarray(image).mean(), 65)
            self.assertFalse(np.array_equal(np.asarray(image), self.frame[:, :80]))
        np.testing.assert_array_equal(dim, original)
        self.assertEqual(reference.compare(dim * 0, lighting_luts=curves), {"A": False, "B": False})
        self.assertFalse(curves)

    def test_black_flat_noisy_or_unrelated_dark_view_cannot_trigger_lighting_review(self):
        import numpy as np

        reference = self.enroll()
        rng = np.random.default_rng(42)
        for view in (self.frame * 0, np.full_like(self.frame, 14),
                     rng.integers(2, 22, self.frame.shape, dtype=np.uint8),
                     (self.frame[::-1] * .18).astype(np.uint8)):
            curves = {}
            self.assertEqual(reference.compare(view, lighting_luts=curves), {"A": False, "B": False})
            self.assertEqual(curves, {})

    def test_local_object_partial_and_full_cover(self):
        reference = self.enroll()
        frame = self.frame.copy()
        frame[20:24, 20:24] = [255, 0, 0]
        self.assertEqual(reference.compare(frame), {"A": False, "B": True})
        frame[:, :80] = 0
        self.assertEqual(reference.compare(frame), {"A": False, "B": True})
        frame[:] = 0
        self.assertEqual(reference.compare(frame), {"A": False, "B": False})
        self.assertEqual(reference.compare(self.frame + 2), {"A": True, "B": True})

    def test_bottle_and_camera_exposure_change_leave_neighbor_empty(self):
        import numpy as np

        # Leave visible background around both seats, like the mounted camera.
        config = ROIConfig("room", 160, 80, (
            SeatROI("A", ((8, 15), (68, 15), (68, 64), (8, 64))),
            SeatROI("B", ((89, 15), (149, 15), (149, 64), (89, 64))),
        ))
        clear = seat_observations(config, [], observed_at=0, frame_width=160, frame_height=80)
        reference = EmptyReference(config, self.frame, clear)
        self.addCleanup(reference.close)
        tracker = SeatTracker(config, now=0, policy=TransitionPolicy(
            required_empty_checks=3, max_observation_gap=15))

        def update(second, frame, detections=()):
            return tracker.update(detections, observed_at=second, now=second + .1,
                                  frame_width=160, frame_height=80, empty_matches=reference.compare(frame))

        for second in (0, 10, 20):
            states = update(second, self.frame)
        self.assertEqual([s.status for s in states.values()], [Status.EMPTY, Status.EMPTY])
        noise = np.random.default_rng(42).normal(0, 5, self.frame.shape)
        frame = (self.frame + np.array([20, 24, 22]) + noise).clip(0, 255).astype(np.uint8)
        frame[28:55, 29:41] = [170, 160, 35]
        bottle = Detection(39, .8, (29, 28, 41, 55))
        self.assertEqual(reference.compare(frame), {"A": False, "B": True})
        for second, expected in ((30, Status.UNKNOWN), (40, Status.AWAY)):
            states = update(second, frame, [bottle])
            self.assertEqual(states["A"].status, expected)
            self.assertEqual(states["B"].status, Status.EMPTY)
            self.assertEqual(states["B"].status_since, 20)
            self.assertEqual(states["B"].last_observed_at, second)
        frame = self.frame.copy()
        frame[15:65, 8:69] += 24
        self.assertEqual(reference.compare(frame), {"A": True, "B": True})
        frame[15:65, 8:69, 2] += 55
        self.assertEqual(reference.compare(frame), {"A": False, "B": True})

    def test_seat_one_holds_marginal_view_while_seat_two_has_a_bottle(self):
        reference = self.enroll()
        tracker = SeatTracker(self.config, now=0, policy=TransitionPolicy(
            required_empty_checks=3, max_observation_gap=15))
        bottle = Detection(39, .8, (100, 30, 118, 58))
        reaching = Detection(0, .8, (60, 12, 145, 58))

        def update(t, frame, detections):
            states = tracker.update(detections, observed_at=t, now=t + .1, frame_width=160,
                                    frame_height=80, empty_matches=reference.compare(frame))
            return states["A"], states["B"]

        for t in (0, 10, 20):
            a, b = update(t, self.frame, [])
        self.assertEqual((a.status, b.status), (Status.EMPTY, Status.EMPTY))

        partial_shadow = self.frame.copy()
        partial_shadow[:22, :80] += 42  # Moderate brightness change on 27.5% of A.
        partial_shadow[30:58, 100:118] = [190, 65, 30]
        self.assertEqual(reference.compare(partial_shadow), {"A": None, "B": False})
        glint = self.frame.copy()
        glint[10:14, 15:20] += 80
        self.assertEqual(reference.compare(glint), {"A": None, "B": True})
        a, b = update(30, partial_shadow, [bottle, reaching])
        self.assertEqual((a.status, a.last_observed_at, b.status), (Status.EMPTY, 20, Status.UNKNOWN))

        mild_shadow = self.frame.copy()
        mild_shadow[:12, :80] += 42  # Positive visual match despite a 15% shadow.
        mild_shadow[30:58, 100:118] = [190, 65, 30]
        self.assertEqual(reference.compare(mild_shadow), {"A": True, "B": False})
        for t in (40, 50):
            a, b = update(t, mild_shadow, [bottle])
            self.assertEqual((a.status, a.status_since, a.last_observed_at), (Status.EMPTY, 20, t))
        self.assertEqual(b.status, Status.AWAY)
        for t in (60, 70, 80, 90):
            a, b = update(t, partial_shadow, [bottle])
            self.assertEqual(a.last_observed_at, 50)
            self.assertEqual(a.status, Status.EMPTY if t < 80 else Status.UNKNOWN)
            self.assertEqual(b.status, Status.AWAY)
        for t in (100, 110, 120):
            a, b = update(t, mild_shadow, [bottle])
            self.assertEqual(a.status, Status.EMPTY if t == 120 else Status.UNKNOWN)
            self.assertEqual(b.status, Status.AWAY)

    def test_reference_rejects_dark_or_blocked_seats(self):
        with self.assertRaisesRegex(ValueError, "TOO_DARK"):
            EmptyReference(self.config, self.frame * 0, self.clear)
        for confidence in (.195, .85):
            detections = [Detection(67, confidence, (90, 30, 100, 40))]
            observations = seat_observations(self.config, detections, observed_at=0,
                                             frame_width=160, frame_height=80)
            with self.assertRaisesRegex(ValueError, "B:"):
                EmptyReference(self.config, self.frame, observations)

    def test_room_wide_dimming_recovers_two_seats_but_preserves_local_changes(self):
        import numpy as np

        config = ROIConfig("room", 160, 80, (
            SeatROI("A", ((8, 14), (69, 14), (69, 65), (8, 65))),
            SeatROI("B", ((89, 14), (150, 14), (150, 65), (89, 65))),
        ))
        clear = seat_observations(config, [], observed_at=0, frame_width=160, frame_height=80)
        reference = EmptyReference(config, self.frame, clear)
        self.addCleanup(reference.close)
        dim = np.rint(self.frame * [.52, .59, .65]).astype(np.uint8)
        self.assertNotIn(True, reference.compare(dim).values())
        tracker = SeatTracker(config, now=0, policy=TransitionPolicy(required_empty_checks=3, max_observation_gap=15))
        for t, frame, expected in ((0, self.frame, Status.UNKNOWN), (10, self.frame, Status.UNKNOWN),
                                   (20, self.frame, Status.EMPTY), (30, dim * 0, Status.UNKNOWN),
                                   (40, dim, Status.UNKNOWN), (50, dim, Status.UNKNOWN), (60, dim, Status.EMPTY)):
            matches = reference.compare(frame, lighting_luts={})
            states = tracker.update([], observed_at=t, now=t+.1, frame_width=160, frame_height=80,
                                    empty_matches=matches)
            self.assertEqual([s.status for s in states.values()], [expected, expected])
        dim[25:43, 100:114] = [105, 20, 35]
        matches = reference.compare(dim, lighting_luts={})
        self.assertEqual(matches, {"A": True, "B": False})
        local = self.frame.copy()
        local[14:66, 8:70] = dim[14:66, 8:70]
        self.assertIsNot(reference.compare(local, lighting_luts={})["A"], True)

    def test_neighbor_recovers_after_three_tolerant_matches_while_bottle_stays(self):
        import cv2
        import numpy as np

        # Desk edges make a one-pixel camera movement visible across both seats.
        self.frame[:, 12::18] = 205
        reference = self.enroll()
        tracker = SeatTracker(self.config, now=0, policy=TransitionPolicy(
            required_empty_checks=3, max_observation_gap=15))
        bottle = Detection(39, .8, (24, 28, 38, 56))
        shifted = cv2.warpAffine(self.frame, np.float32([[1, 0, 1], [0, 1, 0]]),
                                 (160, 80), borderMode=cv2.BORDER_REPLICATE)
        shifted[28:56, 24:38] = (140, 85, 20)
        # A small, moderate reflection persists on the empty desk.
        shifted[32:42, 96:106] += 28
        self.assertEqual(reference.compare(shifted), {"A": False, "B": True})

        def update(t, frame, detections):
            return tracker.update(detections, observed_at=t, now=t + .1, frame_width=160,
                                  frame_height=80, empty_matches=reference.compare(frame))

        for t in (0, 10, 20):
            states = update(t, self.frame, [])
        self.assertEqual(states["B"].status, Status.EMPTY)
        self.assertEqual(update(30, shifted, [bottle])["B"].status, Status.EMPTY)
        covered = shifted.copy()
        covered[:, 80:] = 0
        for t, frame, checks in ((40, covered, 0), (50, shifted, 1), (60, shifted, 2),
                                 (70, covered, 0), (80, shifted, 1), (90, shifted, 2),
                                 (100, shifted, 3)):
            states = update(t, frame, [bottle])
            self.assertEqual(states["B"].status, Status.EMPTY if checks == 3 else Status.UNKNOWN, t)
            self.assertEqual(states["A"].status, Status.AWAY)
            self.assertEqual(tracker.machines["B"].verified_empty_checks, checks)

    def test_three_fresh_matches_no_conflict_and_conservative_recovery(self):
        reference = self.enroll()
        tracker = SeatTracker(self.config, now=0, policy=TransitionPolicy(
            required_empty_checks=3, max_observation_gap=15))

        def update(t, frame, detections=(), latency=.1):
            return tracker.update(detections, observed_at=t, now=t + latency, frame_width=160,
                                  frame_height=80, empty_matches=reference.compare(frame))["A"].status

        phone = Detection(67, .8, (30, 30, 40, 40))
        self.assertEqual(update(0, self.frame), Status.UNKNOWN)
        self.assertEqual(update(10, self.frame), Status.UNKNOWN)
        self.assertEqual(update(20, self.frame, latency=3), Status.UNKNOWN)
        self.assertEqual(update(30, self.frame), Status.UNKNOWN)
        self.assertEqual(update(40, self.frame), Status.UNKNOWN)
        self.assertEqual(update(50, self.frame), Status.EMPTY)
        self.assertEqual(update(60, self.frame * 0), Status.UNKNOWN)
        self.assertEqual(update(70, self.frame, [phone]), Status.UNKNOWN)
        self.assertEqual(update(80, self.frame, [phone]), Status.AWAY)
        self.assertEqual(update(90, self.frame), Status.AWAY)
        self.assertEqual(update(100, self.frame), Status.AWAY)
        self.assertEqual(update(110, self.frame), Status.EMPTY)
        self.assertEqual(update(120, self.frame, [phone]), Status.UNKNOWN)

    def test_preview_key_and_headless_confirmation_publish_verified_states(self):
        for preview in (True, False):
            with self.subTest(preview=preview):
                now = 0.0

                def advance(seconds):
                    nonlocal now
                    now += seconds

                def read(previous_at):
                    if now >= 50:
                        raise KeyboardInterrupt
                    advance(.01)
                    return now, self.frame.copy() if now < 40 else self.frame * 0

                camera = MagicMock()
                camera.__enter__.return_value.read_after.side_effect = read
                keys = iter([ord("e")])
                cv = SimpleNamespace(error=RuntimeError, WINDOW_AUTOSIZE=1, WND_PROP_VISIBLE=1,
                                     namedWindow=Mock(), destroyAllWindows=Mock(), getWindowProperty=lambda *_: 1,
                                     waitKey=lambda _: next(keys, -1))
                publisher = MagicMock()
                with patch.object(run_vision, "LatestCamera", return_value=camera), \
                     patch.object(run_vision, "load_config", return_value=self.config), \
                     patch.object(run_vision, "YoloDetector", return_value=Mock(detect=lambda _: [])), \
                     patch.object(run_vision, "require_opencv", return_value=cv), \
                     patch.object(run_vision, "load_transport_config", return_value=Mock()), \
                     patch.object(run_vision, "StatusPublisher", return_value=publisher), \
                     patch.object(run_vision, "draw_preview"), patch.dict("os.environ", {"DISPLAY": ":test"}), \
                     patch.object(run_vision, "monotonic", side_effect=lambda: now), \
                     patch.object(run_vision, "sleep", side_effect=advance), redirect_stdout(StringIO()):
                    self.assertEqual(run_vision.main(["--preview"] if preview else ["--confirm-empty-at-start"]), 0)
                self.assertEqual([call.args[0]["A"].status for call in publisher.update.call_args_list],
                                 [Status.UNKNOWN] * 3 + [Status.EMPTY, Status.UNKNOWN])
