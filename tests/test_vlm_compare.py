import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from zeroframe.detection import Detection, SeatTracker
from zeroframe.roi import load_config
from zeroframe.state import Status, TransitionPolicy
from zeroframe.vlm_compare import HEADER, VLMComparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.ids = ("A", "B")
        self.runner = VLMComparison(SimpleNamespace(seats=[SimpleNamespace(seat_id=s) for s in self.ids]))
        self.runner.channel = Mock()
        self.runner.ready = True
        self.states = {s: SimpleNamespace(status=Status.UNKNOWN) for s in self.ids}

    def test_one_frame_in_flight_rotation_and_original_observation(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("optional Pillow unavailable")

        def seat_image(frame, config, seat, *, lighting_lut):
            image = Image.new("RGB", (320, 160), "gray")
            image.info["seat_visibility"] = {"usable": True}
            return image

        with patch("zeroframe.vlm_compare.protect_camera_process"), \
             patch("zeroframe.vlm_compare.seat_image", side_effect=seat_image) as crop, \
             patch("zeroframe.vlm_compare.monotonic", return_value=103), patch("builtins.print"):
            tone_curve = object()
            self.runner.submit(object(), self.states, 8, 100, reviews={"A": False},
                               lighting_luts={"A": tone_curve})
            self.runner.submit(object(), self.states, 9, 102)
            crop.assert_called_once()
            self.assertIs(crop.call_args.kwargs["lighting_lut"], tone_curve)
            header, pixels = self.runner.channel.sendmsg.call_args.args[0]
            self.assertEqual(HEADER.unpack(header), (8, 256, 128))
            self.assertEqual(len(pixels), 256 * 128 * 3)
            self.runner.channel.recv.return_value = json.dumps([8, "EMPTY", 2.7, None]).encode()
            self.assertEqual(self.runner.poll(8), ("A", 100, Status.EMPTY))
            self.assertIn("#8 A | seat UNKNOWN | VLM EMPTY", self.runner.label)
            self.assertEqual(self.states["A"].status, Status.UNKNOWN)
            self.runner.submit(object(), self.states, 9, 103)
            self.assertEqual(crop.call_args.args[-1], "B")
            self.assertIsNone(crop.call_args.kwargs["lighting_lut"])

    def test_old_frame_wrong_id_and_expired_result_are_discarded(self):
        for result_id, current, now in [(4, 5, 102), (3, 4, 102), (4, 4, 116)]:
            with self.subTest(result_id=result_id, current=current, now=now), \
                 patch("zeroframe.vlm_compare.monotonic", return_value=now), patch("builtins.print"):
                self.runner.pending = ("A", 4, 100, "AWAY")
                self.runner.channel.recv.return_value = json.dumps([result_id, "EMPTY", 2, None]).encode()
                self.assertIsNone(self.runner.poll(current))
                self.assertNotIn("EMPTY", self.runner.label)
                self.assertIsNone(self.runner.pending)

    def test_visibility_abstains_without_sending_pixels(self):
        image = Mock()
        image.info = {"seat_visibility": {"usable": False, "reason": "TOO_DARK"}}
        from contextlib import nullcontext
        with patch("zeroframe.vlm_compare.protect_camera_process"), \
             patch("zeroframe.vlm_compare.seat_image", return_value=nullcontext(image)), patch("builtins.print"):
            self.runner.submit(object(), self.states, 4, 100)
        self.runner.channel.sendmsg.assert_not_called()
        self.assertIn("VLM UNKNOWN (TOO_DARK)", self.runner.label)

    def test_deadline_reaps_worker_and_disables_comparison(self):
        self.runner.process = process = Mock()
        process.is_alive.side_effect = [True, True, False]
        self.runner.ready = False
        self.runner.deadline = 115
        self.runner.channel.recv.side_effect = BlockingIOError
        with patch("zeroframe.vlm_compare.monotonic", return_value=115), patch("builtins.print"):
            self.runner.poll(4)
        process.kill.assert_called_once()
        process.close.assert_called_once()
        self.assertIsNone(self.runner.channel)
        self.assertIsNone(self.runner.pending)

    def test_pending_second_check_has_priority_then_remaining_candidates_rotate(self):
        with patch("zeroframe.vlm_compare.protect_camera_process"), patch.object(self.runner, "_submit") as send:
            for number, reviews in enumerate(({}, {"A": False, "B": True}, {"A": False, "B": False})):
                self.runner.submit(None, self.states, number, number, reviews=reviews)
            self.assertEqual([call.args[-1] for call in send.call_args_list], ["B", "A"])

    def test_conflicts_gaps_and_superseded_votes_require_a_new_pair(self):
        config = load_config("examples/roi_config.example.json")
        policy = TransitionPolicy(max_observation_gap=15, required_empty_checks=3)
        for conflict in ("person", "bottle", "ambiguous", "strong_change", "no_reference", "stale",
                         "slow_vote", "no_reply", "vlm_away", "observation_gap"):
            with self.subTest(conflict=conflict):
                tracker = SeatTracker(config, now=0, policy=policy)
                machine = tracker.machines["A-01"]

                def update(t, objects=(), match=None, delay=.2, reference=True):
                    tracker.update(objects, observed_at=t, now=t+delay, frame_width=640, frame_height=480,
                                   empty_matches={"A-01": match, "A-02": True} if reference else None)

                update(0)
                self.assertFalse(machine.review_vlm(Status.EMPTY, 0, 3))
                boxes = {"person": [Detection(0, .9, (100, 110, 180, 280))],
                         "bottle": [Detection(39, .35, (100, 110, 180, 280))],
                         "ambiguous": [Detection(0, .9, (220, 110, 380, 280))]}
                update(10, boxes.get(conflict, []), False if conflict == "strong_change" else None,
                       delay=2.5 if conflict == "stale" else .2, reference=conflict != "no_reference")
                if conflict == "slow_vote":
                    self.assertFalse(machine.review_vlm(Status.EMPTY, 10, 25))
                elif conflict not in ("no_reply", "observation_gap"):
                    label = Status.AWAY if conflict == "vlm_away" else Status.EMPTY
                    self.assertFalse(machine.review_vlm(label, 10, 13))
                t = 30 if conflict in ("observation_gap", "slow_vote") else 20
                update(t)
                self.assertFalse(machine.review_vlm(Status.EMPTY, 10, t+1))
                self.assertFalse(machine.review_vlm(Status.EMPTY, t, t+3))
                self.assertFalse(machine.review_vlm(Status.EMPTY, t, t+4))
                update(t+10, match=True)
                self.assertTrue(machine.review_vlm(Status.EMPTY, t+10, t+13))
                self.assertEqual(machine.state.status, Status.EMPTY)
                update(t+20)
                self.assertTrue(machine.review_vlm(Status.EMPTY, t+20, t+23))
                self.assertEqual(machine.state.last_observed_at, t+20)
                if conflict == "vlm_away":
                    update(t+30)
                    self.assertTrue(machine.review_vlm(Status.AWAY, t+30, t+33))
                    self.assertEqual(machine.state.status, Status.UNKNOWN)
                self.assertEqual(machine.tick(t+50).status, Status.UNKNOWN)
