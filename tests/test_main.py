from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main
from zeroframe.roi import load_config, save_config


class LauncherTests(unittest.TestCase):
    def launcher(self, directory):
        app = object.__new__(main.Launcher)
        app.root = Mock()
        app.dialogs = Mock()
        app.dialogs.askyesno.return_value = True
        app.config_path = Path(directory) / "roi_config.json"
        model = Path(directory) / "yolo11n.pt"
        model.touch()
        app.room = SimpleNamespace(get=lambda: "IT4-101", set=Mock())
        app.seats = SimpleNamespace(get=lambda: "A-01 A-02", set=Mock())
        app.camera = SimpleNamespace(get=lambda: "0")
        app.model = SimpleNamespace(get=lambda: str(model))
        app.status = Mock()
        app.saved = Mock()
        return app

    def test_settings_accept_spaces_commas_and_resolve_model_relative_to_repository(self):
        room, seats, camera, model = main.read_settings(" R ", "A-01, A-02\nA-03", "1", "local/model.pt")
        self.assertEqual((room, seats, camera), ("R", ["A-01", "A-02", "A-03"], 1))
        self.assertEqual(model, main.ROOT / "local/model.pt")

    def test_invalid_fields_are_rejected(self):
        for room, seats, camera, model in (("", "A", "0", "x.pt"), ("R", "", "0", "x.pt"),
                                           ("R", "A A", "0", "x.pt"), ("R", "A", "-1", "x.pt"),
                                           ("R", "A", "1.5", "x.pt"), ("R", "A", "0", "")):
            with self.subTest(room=room, seats=seats, camera=camera), self.assertRaises(ValueError):
                main.read_settings(room, seats, camera, model)

    def test_first_start_calibrates_saves_then_runs_preview(self):
        config = load_config("examples/roi_config.example.json")
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)

            def calibrate(room, seats, *, camera, output, overwrite):
                self.assertEqual((room, seats, camera, overwrite), ("IT4-101", ["A-01", "A-02"], 0, False))
                save_config(config, output)
                return config

            with patch.object(main, "calibrate", side_effect=calibrate), patch.object(main, "run") as run:
                app.launch("detect")
            run.assert_called_once_with(app.config_path, Path(directory) / "yolo11n.pt", camera=0, preview=True)
            app.dialogs.askyesno.assert_not_called()
            app.root.withdraw.assert_called_once()
            app.root.deiconify.assert_called_once()
            self.assertEqual(load_config(app.config_path), config)
            app.dialogs.showerror.assert_not_called()

    def test_existing_matching_config_skips_calibration(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            save_config(load_config("examples/roi_config.example.json"), app.config_path)
            with patch.object(main, "calibrate") as calibrate, patch.object(main, "run") as run:
                app.launch("detect")
            calibrate.assert_not_called()
            run.assert_called_once()
            app.dialogs.askyesno.assert_not_called()

    def test_declining_replacement_does_not_hide_window_or_touch_file(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            app.config_path.write_text("existing data", encoding="utf-8")
            app.dialogs.askyesno.return_value = False
            with patch.object(main, "calibrate") as calibrate, patch.object(main, "run") as run:
                app.launch("calibrate")
            self.assertEqual(app.config_path.read_text(encoding="utf-8"), "existing data")
            calibrate.assert_not_called()
            run.assert_not_called()
            app.root.withdraw.assert_not_called()

    def test_cancelled_calibration_restores_launcher_and_does_not_start_detection(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            with patch.object(main, "calibrate", return_value=None), patch.object(main, "run") as run:
                app.launch("detect")
            run.assert_not_called()
            self.assertFalse(app.config_path.exists())
            app.root.deiconify.assert_called_once()
            app.dialogs.showerror.assert_not_called()

    def test_saved_config_can_be_replaced_without_loading_corrupt_old_json(self):
        config = load_config("examples/roi_config.example.json")
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            app.config_path.write_text("not JSON", encoding="utf-8")
            with patch.object(main, "calibrate", return_value=config) as calibrate, \
                 patch.object(main, "run") as run:
                app.launch("calibrate")
            self.assertTrue(calibrate.call_args.kwargs["overwrite"])
            run.assert_not_called()
            app.dialogs.showerror.assert_not_called()

    def test_modified_seat_list_requires_recalibration_and_confirmation(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            save_config(load_config("examples/roi_config.example.json"), app.config_path)
            app.seats.get = lambda: "B-01"
            with patch.object(main, "calibrate", return_value=None) as calibrate, patch.object(main, "run") as run:
                app.launch("detect")
            app.dialogs.askyesno.assert_called_once()
            calibrate.assert_called_once()
            self.assertEqual(calibrate.call_args.args, ("IT4-101", ["B-01"]))
            run.assert_not_called()

    def test_camera_or_inference_error_restores_window_and_shows_error(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            save_config(load_config("examples/roi_config.example.json"), app.config_path)
            with patch.object(main, "run", side_effect=RuntimeError("camera unavailable")):
                app.launch("detect")
            app.root.deiconify.assert_called_once()
            app.dialogs.showerror.assert_called_once_with("ZeroFrame 실행 오류", "camera unavailable", parent=app.root)

    def test_missing_model_is_reported_before_opening_camera(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            app.model.get = lambda: str(Path(directory) / "missing.pt")
            with patch.object(main, "calibrate") as calibrate, patch.object(main, "run") as run:
                app.launch("detect")
            calibrate.assert_not_called()
            run.assert_not_called()
            app.root.withdraw.assert_not_called()
            app.dialogs.showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()
