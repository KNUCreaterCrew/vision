from contextlib import nullcontext
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import probe_vlm
from zeroframe.roi import ROIConfig, SeatROI
from zeroframe.state import Status
from zeroframe.vlm import LocalSeatVLM, image_visibility, parse_choice, parse_label, protect_camera_process, seat_image
from zeroframe.vlm_eval import summarize_results


class LabelTests(unittest.TestCase):
    def test_only_exact_labels_are_accepted(self):
        for label in Status:
            self.assertEqual(parse_label(f"  {label.value}\n")[0], label)

    def test_prose_conflicts_and_nonstring_never_verify_empty(self):
        for text in ("EMPTY or OCCUPIED", "Not EMPTY", "EMPTY.", "", None, "empty", "<image>"):
            with self.subTest(text=text):
                self.assertEqual(parse_label(text), (Status.UNKNOWN, "INVALID_OUTPUT"))

    def test_token_limit_cannot_turn_partial_answer_into_empty(self):
        self.assertEqual(parse_label("EMPTY", complete=False), (Status.UNKNOWN, "TRUNCATED_OUTPUT"))

    def test_missing_local_model_fails_before_dependency_import_or_download(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "prepare_vlm.py"):
                LocalSeatVLM(directory)

    def test_generation_decodes_only_answer_not_prompt_and_requires_eos(self):
        model = object.__new__(LocalSeatVLM)
        model.prompt = "prompt includes EMPTY but is not an answer"
        model.profile = "labels"
        model.visibility_guard = False
        model.torch = SimpleNamespace(inference_mode=nullcontext)
        processor = Mock(return_value={"input_ids": SimpleNamespace(shape=(1, 100))})
        processor.apply_chat_template.return_value = "rendered prompt"
        model.processor = processor
        model.model = Mock()
        model.model.generation_config.eos_token_id = [2, 3]
        generated = Mock()
        model.model.generate.return_value = generated
        for tokens, complete in (([10, 2], True), ([10], False)):
            generated.__getitem__ = Mock(return_value=tokens)
            processor.tokenizer.decode.return_value = "EMPTY"
            result = model.classify(Mock())
            processor.tokenizer.decode.assert_called_with(tokens, skip_special_tokens=True)
            self.assertEqual(result["label"], "EMPTY" if complete else "UNKNOWN")
            self.assertEqual(result["format_valid"], complete)
        self.assertNotIn("prompt", result)
        self.assertNotIn("image", result)

    def test_choices_accept_one_letter_with_optional_period_but_no_prose(self):
        for text, label in (("A", Status.OCCUPIED), ("B.", Status.AWAY), (" C. ", Status.EMPTY), ("D", Status.UNKNOWN)):
            self.assertEqual(parse_choice(text)[0], label)
        for text in ("C or A", "C: empty", "Not C", "c", "C..", None):
            self.assertEqual(parse_choice(text), (Status.UNKNOWN, "INVALID_OUTPUT"))
        self.assertEqual(parse_choice("C.", complete=False), (Status.UNKNOWN, "TRUNCATED_OUTPUT"))

    def test_resolution_change_updates_visual_token_count_together(self):
        model = object.__new__(LocalSeatVLM)
        model.model = SimpleNamespace(config=SimpleNamespace(vision_config=SimpleNamespace(patch_size=16), scale_factor=4))
        model.processor = SimpleNamespace(image_processor=SimpleNamespace())
        model.language = "en"
        for edge, expected_tokens in ((256, 16), (512, 64)):
            model.configure("choices", edge)
            self.assertEqual(model.processor.image_seq_len, expected_tokens)
            self.assertEqual(model.processor.image_processor.max_image_size, {"longest_edge": edge})
        with self.assertRaises(ValueError):
            model.configure("choices", 128)

    def test_visibility_abstention_never_calls_model_or_claims_format_success(self):
        model = object.__new__(LocalSeatVLM)
        model.visibility_guard = True
        model.model = Mock()
        with patch("zeroframe.vlm.image_visibility", return_value={"usable": False, "reason": "TOO_DARK"}):
            result = model.classify(Mock())
        self.assertEqual(result["label"], "UNKNOWN")
        self.assertFalse(result["model_ran"])
        self.assertIsNone(result["format_valid"])
        model.model.generate.assert_not_called()


class ImageTests(unittest.TestCase):
    def test_crop_masks_neighbor_content_and_does_not_share_frame_storage(self):
        try:
            import numpy as np
            import cv2
            from PIL import Image
        except ImportError:
            self.skipTest("Optional OpenCV/NumPy/Pillow unavailable")
        config = ROIConfig("room", 5, 5, (SeatROI("seat", ((0, 0), (4, 0), (0, 4))),))
        frame = np.zeros((5, 5, 3), dtype=np.uint8)
        frame[:] = (10, 20, 30)
        image = seat_image(frame, config, "seat")
        try:
            frame[:] = 255
            self.assertEqual(image.getpixel((0, 0)), (30, 20, 10))
            self.assertEqual(image.getpixel((4, 4)), (127, 127, 127))
        finally:
            image.close()

    def test_resolution_and_seat_errors_fail_before_cropping(self):
        config = ROIConfig("room", 5, 5, (SeatROI("seat", ((0, 0), (4, 0), (0, 4))),))
        with self.assertRaisesRegex(ValueError, "resolution"):
            seat_image(SimpleNamespace(shape=(6, 5, 3)), config, "seat")
        with self.assertRaisesRegex(ValueError, "missing"):
            seat_image(SimpleNamespace(shape=(5, 5, 3)), config, "wrong")

    def test_black_white_and_flat_gray_abstain_without_seat_evidence(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Optional Pillow unavailable")
        for color, reason in (("black", "TOO_DARK"), ("white", "OVEREXPOSED"), ("gray", "LOW_CONTRAST")):
            with Image.new("RGB", (128, 128), color) as image:
                self.assertEqual(image_visibility(image), {"usable": False, "reason": reason})

    def test_gray_mask_border_cannot_make_dark_seat_appear_usable(self):
        try:
            import numpy as np
            import cv2
        except ImportError:
            self.skipTest("Optional OpenCV/NumPy unavailable")
        config = ROIConfig("room", 128, 128, (SeatROI("seat", ((0, 0), (127, 0), (0, 127))),))
        image = seat_image(np.zeros((128, 128, 3), dtype=np.uint8), config, "seat")
        try:
            self.assertEqual(image_visibility(image)["reason"], "TOO_DARK")
        finally:
            image.close()


@unittest.skipUnless(probe_vlm.sys.platform.startswith("linux"), "Linux privacy checks")
class PrivacyTests(unittest.TestCase):
    def privacy_patches(self):
        libc = SimpleNamespace(prctl=Mock(return_value=0))
        return patch("ctypes.CDLL", return_value=libc), patch("resource.setrlimit")

    def test_active_swap_refuses_real_images_but_allows_synthetic(self):
        library, limits = self.privacy_patches()
        with library, limits, patch.object(Path, "read_text", return_value="Filename Type\n/dev/zram0 partition\n"):
            with self.assertRaisesRegex(RuntimeError, "swap disabled"):
                protect_camera_process(live=True)
            protect_camera_process(live=False)

    def test_no_swap_disables_dump_before_camera_use(self):
        library, limits = self.privacy_patches()
        with library as cdll, limits as set_limit, patch.object(Path, "read_text", return_value="Filename Type\n"):
            protect_camera_process(live=True)
            set_limit.assert_called_once()
            cdll.return_value.prctl.assert_any_call(4, 0, 0, 0, 0)
            cdll.return_value.prctl.assert_any_call(1, signal.SIGTERM, 0, 0, 0)


class WorkerTests(unittest.TestCase):
    def test_deadline_does_not_wait_for_late_result(self):
        connection = Mock()
        connection.poll.return_value = False
        with self.assertRaises(TimeoutError):
            probe_vlm.receive_message(connection, 15)
        connection.recv.assert_not_called()

    def test_timeout_stops_worker_before_reporting_unknown(self):
        context = Mock()
        connection, child_connection, process = Mock(), Mock(), Mock()
        context.Pipe.return_value = (connection, child_connection)
        context.Process.return_value = process
        events = []
        with patch.object(probe_vlm.multiprocessing, "get_context", return_value=context), \
             patch.object(probe_vlm, "receive_message", side_effect=[{"type": "ready"}, TimeoutError()]), \
             patch.object(probe_vlm, "stop_worker", side_effect=lambda *a: events.append("stopped")), \
             patch("builtins.print", side_effect=lambda *a, **kw: events.append(a[0])):
            result = probe_vlm.run_probe({"synthetic": True, "seat": None, "threads": 2, "language": "en"})
        self.assertEqual(result, 2)
        self.assertEqual(events[1], "stopped")
        self.assertIn('"VLM_TIMEOUT"', events[2])
        connection.send.assert_called_once_with({"type": "probe", "frame_id": 1})

    def test_forced_stop_joins_then_kills_before_reuse(self):
        process, connection = Mock(), Mock()
        process.is_alive.side_effect = [True, True, False]
        probe_vlm.stop_worker(process, connection)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.join.call_count, 2)
        connection.close.assert_called_once()

    def test_failed_worker_is_not_treated_as_a_verification(self):
        connection = Mock()
        connection.recv.return_value = {"type": "error", "error_type": "RuntimeError", "message": "offline file missing"}
        with self.assertRaisesRegex(RuntimeError, "offline file missing"):
            probe_vlm.receive_message(connection, 15)

    def test_compare_uses_one_ram_image_and_releases_it_before_last_result(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Optional Pillow unavailable")
        connection, image, model = Mock(), Mock(), Mock()
        events = []
        image.close.side_effect = lambda: events.append("closed")
        connection.send.side_effect = lambda payload: events.append(payload)
        connection.recv.side_effect = [{"type": "probe", "frame_id": 7}, "stop"]
        model.classify.side_effect = lambda img: {"label": "UNKNOWN", "model_ran": True, "format_valid": True}
        settings = {"synthetic": True, "model": "unused", "threads": 2, "language": "en", "compare": True}
        with patch.object(probe_vlm, "protect_camera_process"), \
             patch.object(probe_vlm, "LocalSeatVLM", return_value=model), \
             patch.object(probe_vlm, "memory_metrics", return_value={}), \
             patch.object(Image, "new", return_value=image):
            probe_vlm.probe_worker(connection, settings)
        self.assertEqual(model.classify.call_count, 4)
        self.assertTrue(all(call.args[0] is image for call in model.classify.call_args_list))
        self.assertEqual(events[-2], "closed")
        self.assertEqual(events[-1]["frame_id"], 7)
        self.assertTrue(all(item["frame_id"] == 7 for item in events if isinstance(item, dict) and item["type"] == "result"))
        image.close.assert_called_once()

    def test_operator_skip_and_cancel_preserve_partial_metadata_and_hide_answer_from_worker(self):
        context = Mock()
        connection, child_connection = Mock(), Mock()
        context.Pipe.return_value = (connection, child_connection)
        result = {"type": "result", "profile": "choices", "image_edge": 256, "label": "OCCUPIED",
                  "model_ran": True, "format_valid": True, "inference_seconds": .5}
        settings = {"synthetic": False, "seat": "A-01", "threads": 2, "language": "en"}
        with TemporaryDirectory() as directory, \
             patch.object(probe_vlm.multiprocessing, "get_context", return_value=context), \
             patch.object(probe_vlm, "receive_message", side_effect=[{"type": "ready"}, result]), \
             patch.object(probe_vlm, "stop_worker"), patch("builtins.input", side_effect=["", "s", "q"]), \
             patch("builtins.print"):
            report_path = Path(directory) / "report.json"
            self.assertEqual(probe_vlm.run_probe(settings, samples=1, interactive=True,
                             cases=["OCCUPIED", "AWAY", "EMPTY"], report_path=report_path), 0)
            import json
            report = json.loads(report_path.read_text())
        self.assertEqual(report["run_status"], "cancelled")
        self.assertEqual(report["records"][0]["expected_source"], "operator")
        self.assertFalse(report["summary"]["by_variant"]["choices_256"]["all_states_sampled"])
        self.assertEqual(connection.send.call_args_list[0].args, ({"type": "probe", "frame_id": 1},))

    def test_inference_error_releases_image_without_logging_exception_content(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Optional Pillow unavailable")
        connection, image, model = Mock(), Mock(), Mock()
        connection.recv.return_value = {"type": "probe", "frame_id": 1}
        model.classify.side_effect = RuntimeError("PRIVATE_FRAME_DATA")
        settings = {"synthetic": True, "model": "unused", "threads": 2, "language": "en"}
        with patch.object(probe_vlm, "protect_camera_process"), \
             patch.object(probe_vlm, "LocalSeatVLM", return_value=model), \
             patch.object(probe_vlm, "memory_metrics", return_value={}), \
             patch.object(Image, "new", return_value=image):
            probe_vlm.probe_worker(connection, settings)
        error = connection.send.call_args_list[-1].args[0]
        self.assertEqual(error["type"], "error")
        self.assertNotIn("PRIVATE_FRAME_DATA", str(connection.send.call_args_list))
        image.close.assert_called_once()

    def test_existing_report_is_rejected_before_model_or_camera_start(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text("preserve this file")
            with patch.object(probe_vlm, "run_probe") as run, patch("sys.stderr"), self.assertRaises(SystemExit):
                probe_vlm.main(["--synthetic", "--report", str(report)])
            self.assertEqual(report.read_text(), "preserve this file")
            run.assert_not_called()


class EvaluationTests(unittest.TestCase):
    def row(self, expected, label, **extra):
        return {"profile": "choices", "image_edge": 256, "expected": expected, "label": label,
                "model_ran": True, "format_valid": True, **extra}

    def test_invalid_unknown_and_guard_unknown_are_not_model_correct(self):
        summary = summarize_results([
            self.row("UNKNOWN", "UNKNOWN", reason="INVALID_OUTPUT", format_valid=False),
            self.row("UNKNOWN", "UNKNOWN", reason="LOW_VISIBILITY", model_ran=False, format_valid=None),
            self.row("UNKNOWN", "EMPTY"),
        ])["by_variant"]["choices_256"]
        self.assertEqual(summary["model_runs"], 2)
        self.assertEqual(summary["model_correct"], 0)
        self.assertEqual(summary["system_correct"], 2)
        self.assertEqual(summary["guard_abstentions"], 1)
        self.assertEqual(summary["format_compliance"], .5)
        self.assertEqual(summary["unknown_as_empty_count"], 1)

    def test_false_empty_uses_known_occupied_ground_truth_only(self):
        summary = summarize_results([
            self.row("OCCUPIED", "EMPTY"), self.row("AWAY", "UNKNOWN"),
            self.row("EMPTY", "EMPTY"), self.row(None, "EMPTY"),
        ])["by_variant"]["choices_256"]
        self.assertEqual(summary["occupied_samples"], 2)
        self.assertEqual(summary["false_empty_count"], 1)
        self.assertEqual(summary["false_empty_rate"], .5)
        self.assertEqual(summary["labeled_samples"], 3)
        self.assertFalse(summary["all_states_sampled"])

    def test_all_states_and_confusion_matrix_remain_diagnostic(self):
        summary = summarize_results([self.row(label, label) for label in CASE_LABELS])
        self.assertFalse(summary["runtime_enabled"])
        group = summary["by_variant"]["choices_256"]
        self.assertTrue(group["all_states_sampled"])
        self.assertEqual(group["model_accuracy"], 1)
        self.assertEqual(group["confusion_matrix"]["AWAY"]["AWAY"], 1)

    def test_timeout_and_missing_denominators_do_not_become_success(self):
        summary = summarize_results([self.row(None, "UNKNOWN", reason="VLM_TIMEOUT", model_ran=None,
                                             format_valid=None)])["by_variant"]["choices_256"]
        self.assertEqual(summary["timeouts"], 1)
        self.assertIsNone(summary["model_accuracy"])
        self.assertIsNone(summary["format_compliance"])
        self.assertIsNone(summary["false_empty_rate"])


CASE_LABELS = tuple(status.value for status in Status)


if __name__ == "__main__":
    unittest.main()
