from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zeroframe.detection import CLASSES, Detection, SeatTracker, YoloDetector, seat_observations
from zeroframe.roi import ROIConfig, SeatROI, load_config
from zeroframe.state import Status


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config("examples/roi_config.example.json")

    def observations(self, detections, config=None):
        return seat_observations(config or self.config, detections, observed_at=1,
                                 frame_width=640, frame_height=480)

    def test_class_specific_thresholds_and_all_belongings_classes(self):
        for cls in (0, 24, 26, 39, 63, 67, 73):
            threshold = .30 if cls == 0 else .20
            for confidence, accepted in ((threshold, True), (threshold - .001, False)):
                with self.subTest(cls=cls, confidence=confidence):
                    observations = self.observations([Detection(cls, confidence, (100, 120, 200, 300))])
                    observation = observations["A-01"]
                    self.assertEqual(observation.person, accepted and cls == 0)
                    self.assertEqual(observation.belongings, accepted and cls != 0)
                    self.assertEqual(observation.view_clear, accepted)
                    self.assertFalse(observation.empty_verified)
                    self.assertFalse(observations["A-02"].person)

    def test_unrelated_classes_and_unmapped_objects_never_verify_empty(self):
        for detections in ([], [Detection(1, .99, (100, 120, 200, 300))],
                           [Detection(0, .99, (10, 10, 30, 50))]):
            for observation in self.observations(detections).values():
                self.assertFalse(observation.person or observation.belongings or observation.empty_verified)

    def test_unique_person_counts_while_neighbor_with_only_ambiguous_person_is_blocked(self):
        config = ROIConfig("room", 640, 480, (
            SeatROI("one", ((0, 0), (200, 0), (200, 200), (0, 200))),
            SeatROI("two", ((100, 0), (300, 0), (300, 200), (100, 200))),
        ))
        result = self.observations([Detection(0, .9, (120, 0, 180, 100)),
                                    Detection(0, .9, (20, 0, 40, 100))], config)
        self.assertTrue(result["one"].person)
        self.assertTrue(result["one"].view_clear)
        self.assertFalse(result["two"].person)
        self.assertFalse(result["two"].view_clear)

    def test_resolution_and_out_of_frame_boxes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "resolution"):
            seat_observations(self.config, [], observed_at=1, frame_width=320, frame_height=240)
        with self.assertRaisesRegex(ValueError, "inside"):
            self.observations([Detection(0, .9, (100, 120, 800, 300))])

    def test_person_maps_when_bottom_center_is_below_seat_or_cut_off_at_frame_edge(self):
        for bottom in (450, 480):
            with self.subTest(bottom=bottom):
                result = self.observations([Detection(0, .9, (100, 120, 200, bottom))])
                self.assertTrue(result["A-01"].person)
                self.assertTrue(result["A-01"].view_clear)
                self.assertFalse(result["A-02"].person)

    def test_weak_body_overlap_blocks_belongings_only_confirmation(self):
        result = self.observations([
            Detection(0, .9, (100, 0, 200, 149)),
            Detection(63, .9, (100, 120, 200, 300)),
        ])
        self.assertFalse(result["A-01"].person)
        self.assertTrue(result["A-01"].belongings)
        self.assertFalse(result["A-01"].view_clear)

    def test_class_specific_overlap_thresholds_and_frame_side_clipping(self):
        for bbox, seat_id, person, item in (
            ((100, 0, 200, 149.9), "A-01", False, False),
            ((100, 0, 200, 150), "A-01", True, False),
            ((100, 0, 200, 199.9), "A-01", True, False),
            ((100, 0, 200, 200), "A-01", True, True),
            ((0, 120, 200, 300), "A-01", True, True),
            ((500, 120, 640, 300), "A-02", True, True),
        ):
            for cls, accepted in ((0, person), (39, item)):
                with self.subTest(bbox=bbox, cls=cls):
                    obs = self.observations([Detection(cls, .9, bbox)])[seat_id]
                    self.assertEqual(obs.person if cls == 0 else obs.belongings, accepted)
                    self.assertEqual(obs.view_clear, accepted)

    def test_adjacent_seats_require_a_unique_qualifying_overlap(self):
        config = ROIConfig("room", 640, 480, (
            SeatROI("one", ((0, 0), (200, 0), (200, 400), (0, 400))),
            SeatROI("two", ((200, 0), (400, 0), (400, 400), (200, 400))),
        ))
        for cls, signal in ((0, "person"), (39, "belongings")):
            for right, assigned in ((200, True), (249, True), (250, cls != 0), (300, False)):
                with self.subTest(cls=cls, right=right):
                    result = self.observations([Detection(cls, .9, (100, 120, right, 300))], config)
                    self.assertEqual(getattr(result["one"], signal), assigned)
                    self.assertFalse(getattr(result["two"], signal))
                    self.assertEqual(result["one"].view_clear, assigned)
                    self.assertEqual(result["two"].view_clear, assigned)

    def test_detection_rejects_invalid_metadata(self):
        for cls, confidence, bbox in (
            (True, .9, (0, 0, 1, 1)), (-1, .9, (0, 0, 1, 1)),
            (0, float("nan"), (0, 0, 1, 1)), (0, 1.1, (0, 0, 1, 1)),
            (0, True, (0, 0, 1, 1)), (0, .9, (0, 0, 0, 1)),
            (0, .9, (0, -1, 1, 1)), (0, .9, (0, 0, float("inf"), 1)),
        ):
            with self.subTest(cls=cls, confidence=confidence, bbox=bbox), self.assertRaises(ValueError):
                Detection(cls, confidence, bbox)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config("examples/roi_config.example.json")
        self.tracker = SeatTracker(self.config, now=0)

    def update(self, second, detections, *, delay=.1):
        return self.tracker.update(detections, observed_at=second, now=second + delay,
                                   frame_width=640, frame_height=480)

    def test_clear_positives_with_ambiguous_detections_are_order_independent(self):
        box = (100, 120, 200, 300)
        person, bottle = Detection(0, .3, box), Detection(39, .2, box)
        weak_person, weak_item = Detection(0, .29, box), Detection(67, .195, box)
        for boxes, expected in (([person, bottle, weak_item], Status.OCCUPIED),
                                ([bottle, weak_item], Status.AWAY),
                                ([bottle, weak_person], Status.UNKNOWN)):
            for order in (boxes, boxes[::-1]):
                with self.subTest(order=order):
                    self.tracker = SeatTracker(self.config, now=0)
                    for t in range(5):
                        states = self.update(t, order)
                    self.assertEqual(states["A-01"].status, expected)
                    self.assertEqual(states["A-02"].status, Status.UNKNOWN)

    def test_real_pipeline_occupied_then_away_then_unknown_never_empty(self):
        person = Detection(0, .30, (100, 350, 200, 450))
        laptop = Detection(63, .20, (100, 350, 200, 450))
        for second in (0, 1, 2):
            result = self.update(second, [person, laptop])
        self.assertEqual(result["A-01"].status, Status.OCCUPIED)
        self.assertEqual(result["A-01"].last_observed_at, 2)
        for second in range(3, 8):
            result = self.update(second, [laptop])
        self.assertEqual(result["A-01"].status, Status.AWAY)
        for second in range(8, 38):
            result = self.update(second, [])
            self.assertNotEqual(result["A-01"].status, Status.EMPTY)
        self.assertEqual(result["A-01"].status, Status.UNKNOWN)
        self.assertEqual(result["A-02"].status, Status.UNKNOWN)

    def test_low_confidence_cannot_refresh_or_complete_candidate(self):
        for second in (0, 1, 2):
            self.update(second, [Detection(0, .9, (100, 120, 200, 300))])
        for second in range(3, 33):
            result = self.update(second, [Detection(0, .29, (100, 120, 200, 300))])
        self.assertEqual(result["A-01"].status, Status.UNKNOWN)
        self.assertEqual(result["A-01"].last_observed_at, 2)

    def test_slow_inference_is_discarded_and_camera_silence_expires(self):
        person = Detection(0, .9, (100, 120, 200, 300))
        for second in (0, 1, 2):
            self.update(second, [person])
        result = self.update(3, [person], delay=3)
        self.assertEqual(result["A-01"].last_observed_at, 2)
        result = self.tracker.tick(32)
        self.assertEqual(result["A-01"].status, Status.UNKNOWN)

    def test_clipped_upper_body_can_confirm_occupied_without_visible_feet(self):
        person = Detection(0, .9, (100, 120, 200, 480))
        for second in (0, 1, 2):
            result = self.update(second, [person])
        self.assertEqual(result["A-01"].status, Status.OCCUPIED)
        self.assertEqual(result["A-02"].status, Status.UNKNOWN)

    def test_small_seat_with_lower_third_of_seated_person_confirms_occupied(self):
        config = ROIConfig("room", 640, 480, (
            SeatROI("small", ((80, 180), (120, 180), (120, 220), (80, 220))),
            SeatROI("next", ((180, 180), (220, 180), (220, 220), (180, 220))),
        ))
        for bbox, expected in (((20, 40, 160, 400), Status.UNKNOWN),
                               ((80, 100, 120, 220), Status.OCCUPIED),
                               ((80, 99, 120, 220), Status.UNKNOWN)):
            with self.subTest(bbox=bbox):
                tracker = SeatTracker(config, now=0)
                person = Detection(0, .30, bbox)
                for second in (0, 1, 2):
                    states = tracker.update([person], observed_at=second, now=second,
                                            frame_width=640, frame_height=480)
                self.assertEqual(states["small"].status, expected)
                self.assertEqual(states["next"].status, Status.UNKNOWN)

    def test_invalid_completion_times_do_not_modify_states(self):
        before = self.tracker.tick(10)
        for observed_at, now in ((11, 10), (1, 9), (1, float("nan")), (float("inf"), 12)):
            with self.subTest(observed_at=observed_at, now=now), self.assertRaises(ValueError):
                self.tracker.update([], observed_at=observed_at, now=now, frame_width=640, frame_height=480)
            self.assertEqual(self.tracker.tick(10), before)

    def test_late_and_duplicate_results_do_not_refresh_state(self):
        person = Detection(0, .9, (100, 120, 200, 300))
        for second in (0, 1, 2):
            self.update(second, [person])
        for observed_at, now in ((2, 2.5), (1, 2.6)):
            result = self.tracker.update([person], observed_at=observed_at, now=now,
                                         frame_width=640, frame_height=480)
            self.assertEqual(result["A-01"].last_observed_at, 2)


class DetectorTests(unittest.TestCase):
    def fake_package(self):
        tensor = lambda values: SimpleNamespace(cpu=lambda: SimpleNamespace(tolist=lambda: values))
        boxes = SimpleNamespace(xyxy=tensor([[100., 120., 200., 300.]]),
                                cls=tensor([0.]), conf=tensor([.8]))
        result = SimpleNamespace(boxes=boxes, names=CLASSES.copy())
        predictor = SimpleNamespace(batch=object(), dataset=object(), results=[result], plotted_img=object())
        model = SimpleNamespace(predict=Mock(return_value=[result]), predictor=predictor)
        package = SimpleNamespace(__version__="8.3.253", YOLO=Mock(return_value=model),
                                  settings=SimpleNamespace(update=Mock()))
        class Frame:
            dtype = "uint8"
            ndim = 3
            shape = (480, 640, 3)
        numpy = SimpleNamespace(ndarray=Frame, uint8="uint8")
        return package, model, result, numpy, Frame()

    def test_local_model_only_no_runtime_downloads(self):
        for path in ("missing.pt", "https://example.com/model.pt", "model.yaml", "."):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "existing local"):
                YoloDetector(path)

    def test_incomplete_ncnn_and_unreviewed_runtime_versions_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "yolo11n_ncnn_model"
            path.mkdir()
            with self.assertRaisesRegex(ValueError, "complete"):
                YoloDetector(path)
            for name in ("model.ncnn.param", "model.ncnn.bin", "metadata.yaml"):
                (path / name).touch()
            package, model, result, numpy, frame = self.fake_package()
            package.__version__ = "0.0.0"
            with patch.dict("sys.modules", {"ultralytics": package}), \
                 patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(RuntimeError, "8.3.253"):
                YoloDetector(path)
            package.YOLO.assert_not_called()

    def test_adapter_passes_ram_frame_disables_outputs_and_clears_buffers(self):
        package, model, result, numpy, frame = self.fake_package()
        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "yolo11n.pt"
            model_path.touch()
            with patch.dict("sys.modules", {"ultralytics": package, "numpy": numpy}), \
                 patch.dict("os.environ", {}, clear=True):
                detector = YoloDetector(model_path)
                import os
                self.assertEqual(os.environ["YOLO_OFFLINE"], "true")
                self.assertEqual(os.environ["YOLO_AUTOINSTALL"], "false")
                detections = detector.detect(frame)
                self.assertEqual(detections, (Detection(0, .8, (100., 120., 200., 300.)),))
        args = model.predict.call_args.kwargs
        self.assertIs(args["source"], frame)
        for option in ("save", "save_txt", "save_crop", "save_frames", "visualize", "show"):
            self.assertIs(args[option], False)
        self.assertLess(args["conf"], .20)
        self.assertEqual(args["device"], "cpu")
        self.assertEqual(args["classes"], list(CLASSES))
        package.settings.update.assert_called_once_with({"sync": False, "hub": False})
        for attribute in ("batch", "dataset", "results", "plotted_img"):
            self.assertIsNone(getattr(model.predictor, attribute))

    def test_prediction_failure_and_wrong_classes_also_release_buffers(self):
        for failure in ("predict", "classes"):
            package, model, result, numpy, frame = self.fake_package()
            if failure == "predict":
                model.predict.side_effect = RuntimeError("inference failed")
            else:
                result.names = {0: "chair"}
            detector = object.__new__(YoloDetector)
            detector.model = model
            with patch.dict("sys.modules", {"numpy": numpy}), self.assertRaises((RuntimeError, ValueError)):
                detector.detect(frame)
            self.assertIsNone(model.predictor.dataset)
            self.assertIsNone(model.predictor.results)

    def test_paths_cannot_be_used_as_inference_sources(self):
        package, model, result, numpy, frame = self.fake_package()
        detector = object.__new__(YoloDetector)
        detector.model = model
        with patch.dict("sys.modules", {"numpy": numpy}), self.assertRaisesRegex(ValueError, "BGR"):
            detector.detect("https://example.com/image.jpg")
        model.predict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
