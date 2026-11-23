from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main
from zeroframe.roi import load_config, save_config


class LocalDisplayTests(unittest.TestCase):
    def display(self, name, uid=42):
        display = Mock()
        display.name = name
        display.is_socket.return_value = True
        display.stat.return_value = SimpleNamespace(st_uid=uid)
        return display

    def test_existing_display_and_authority_are_preserved(self):
        environment = {"DISPLAY": ":3", "XAUTHORITY": "/existing/authority"}
        with patch.dict(main.os.environ, environment, clear=True), \
             patch.object(main.sys, "platform", "linux"), patch.object(main, "Path") as paths:
            main.prepare_local_display()
            self.assertEqual(dict(main.os.environ), environment)
            paths.assert_not_called()

    def test_remote_launch_uses_own_single_local_display(self):
        with TemporaryDirectory() as directory:
            authority = Path(directory) / ".Xauthority"
            authority.touch()
            with patch.dict(main.os.environ, {}, clear=True), \
                 patch.object(main.sys, "platform", "linux"), \
                 patch.object(main.os, "getuid", return_value=42, create=True), \
                 patch.object(main, "Path") as paths:
                paths.return_value.glob.return_value = [self.display("X2")]
                paths.home.return_value = Path(directory)
                main.prepare_local_display()
                self.assertEqual(main.os.environ["DISPLAY"], ":2")
                self.assertEqual(main.os.environ["XAUTHORITY"], str(authority))

    def test_foreign_or_multiple_displays_are_not_selected(self):
        for displays in ([], [self.display("X0", uid=99)],
                         [self.display("X0"), self.display("X1")]):
            with self.subTest(displays=len(displays)), \
                 patch.dict(main.os.environ, {}, clear=True), \
                 patch.object(main.sys, "platform", "linux"), \
                 patch.object(main.os, "getuid", return_value=42, create=True), \
                 patch.object(main, "Path") as paths:
                paths.return_value.glob.return_value = displays
                main.prepare_local_display()
                self.assertNotIn("DISPLAY", main.os.environ)

    def test_unavailable_display_directory_is_tolerated(self):
        with patch.dict(main.os.environ, {}, clear=True), \
             patch.object(main.sys, "platform", "linux"), patch.object(main, "Path") as paths:
            paths.return_value.glob.side_effect = PermissionError("unavailable")
            main.prepare_local_display()
            self.assertNotIn("DISPLAY", main.os.environ)

    def test_windows_does_not_probe_linux_displays(self):
        with patch.dict(main.os.environ, {}, clear=True), \
             patch.object(main.sys, "platform", "win32"), patch.object(main, "Path") as paths:
            main.prepare_local_display()
            paths.assert_not_called()
            self.assertNotIn("DISPLAY", main.os.environ)


class LauncherTests(unittest.TestCase):
    def launcher(self, directory):
        app = object.__new__(main.Launcher)
        app.root = Mock()
        app.dialogs = Mock()
        app.dialogs.askyesno.return_value = True
        app.config_path = Path(directory) / "roi_config.json"
        model = Path(directory) / "yolo11n.pt"
        model.touch()
        app.room = SimpleNamespace(get=lambda: "IT4-108", set=Mock())
        app.max_seats = SimpleNamespace(get=lambda: "6", set=Mock())
        app.camera = SimpleNamespace(get=lambda: "0")
        app.model = SimpleNamespace(get=lambda: str(model))
        app.item_toggles = {item: Mock() for item in main.ITEM_LABELS}
        for toggle in app.item_toggles.values():
            toggle.get.return_value = True
        app.status = Mock()
        app.saved = Mock()
        return app

    def test_settings_accept_count_and_resolve_model_relative_to_repository(self):
        room, seats, camera, model = main.read_settings(" R ", " 3 ", "1", "local/model.pt")
        self.assertEqual((room, seats, camera), ("R", 3, 1))
        self.assertEqual(model, main.ROOT / "local/model.pt")

    def test_invalid_fields_are_rejected(self):
        for room, seats, camera, model in (("", "1", "0", "x.pt"), ("R", "", "0", "x.pt"),
                                           ("R", "0", "0", "x.pt"), ("R", "1", "-1", "x.pt"),
                                           ("R", "1", "1.5", "x.pt"), ("R", "1", "0", "")):
            with self.subTest(room=room, seats=seats, camera=camera), self.assertRaises(ValueError):
                main.read_settings(room, seats, camera, model)

    def test_first_start_calibrates_saves_then_runs_preview(self):
        config = load_config("examples/roi_config.example.json")
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)

            def calibrate(room, *, max_seats, camera, output, overwrite):
                self.assertEqual((room, max_seats, camera, overwrite), ("IT4-108", 6, 0, False))
                save_config(config, output)
                return config

            with patch.object(main, "calibrate", side_effect=calibrate), patch.object(main, "run") as run:
                app.launch("detect")
            run.assert_called_once_with(app.config_path, Path(directory) / "yolo11n.pt", camera=0, preview=True,
                                        vlm_compare=False, item_ids=tuple(main.ITEM_LABELS))
            app.dialogs.askyesno.assert_not_called()
            app.root.withdraw.assert_called_once()
            app.root.deiconify.assert_called_once()
            self.assertEqual(load_config(app.config_path), config)
            app.dialogs.showerror.assert_not_called()

    def test_existing_matching_config_skips_calibration(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            save_config(load_config("examples/roi_config.example.json"), app.config_path)
            for item, toggle in app.item_toggles.items():
                toggle.get.return_value = item in (39, 67)
            for action in ("detect", "compare"):
                with self.subTest(action=action), patch.object(main, "calibrate") as calibrate, \
                     patch.object(main, "run") as run, patch.object(main, "run_vlm_preview") as compare:
                    app.launch(action)
                    calibrate.assert_not_called()
                    if action == "compare":
                        compare.assert_called_once_with(app.config_path, Path(directory) / "yolo11n.pt", 0,
                                                        item_ids=(39, 67))
                        run.assert_not_called()
                    else:
                        run.assert_called_once()
                        self.assertEqual(run.call_args.kwargs["item_ids"], (39, 67))
                        compare.assert_not_called()
                    app.dialogs.askyesno.assert_not_called()

    @patch.object(main.sys, "platform", "linux")
    @patch.object(main.socket, "socket")
    @patch.object(main.subprocess, "run")
    def test_compare_restores_zram_after_success_or_failure(self, run_command, _socket):
        for outcome in (0, 1):
            with self.subTest(outcome=outcome), patch.object(Path, "read_text", return_value="Filename\n/dev/zram0 partition"):
                run_command.reset_mock()
                run_command.side_effect = [SimpleNamespace(returncode=n) for n in (0, 0, outcome, 0)]
                if outcome:
                    with self.assertRaises(RuntimeError):
                        main.run_vlm_preview("config", "model", 1)
                else:
                    main.run_vlm_preview("config", "model", 1)
                commands = [call.args[0] for call in run_command.call_args_list]
                self.assertEqual(commands[1], ["sudo", "-n", "systemctl", "stop", "dev-zram0.swap"])
                self.assertIn("--vlm-empty", commands[2])
                self.assertEqual(commands[3], ["sudo", "-n", "systemctl", "start", "dev-zram0.swap"])

    @patch.object(main.sys, "platform", "linux")
    @patch.object(main.socket, "socket")
    @patch.object(main.subprocess, "run")
    def test_compare_respects_disabled_or_unmanaged_swap(self, run_command, _socket):
        with patch.object(Path, "read_text", return_value="Filename\n/swapfile file"):
            with self.assertRaises(RuntimeError):
                main.run_vlm_preview("config", "model", 0)
            run_command.assert_not_called()
        with patch.object(Path, "read_text", return_value="Filename\n"):
            run_command.return_value.returncode = 0
            main.run_vlm_preview("config", "model", 0, item_ids=())
            run_command.assert_called_once()
            self.assertIn("--preview", run_command.call_args.args[0])
            self.assertEqual(run_command.call_args.args[0][-1], "--items")

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

    def test_reducing_limit_below_saved_count_requires_confirmation(self):
        with TemporaryDirectory() as directory:
            app = self.launcher(directory)
            save_config(load_config("examples/roi_config.example.json"), app.config_path)
            app.max_seats.get = lambda: "1"
            with patch.object(main, "calibrate", return_value=None) as calibrate, patch.object(main, "run") as run:
                app.launch("detect")
            app.dialogs.askyesno.assert_called_once()
            calibrate.assert_called_once()
            self.assertEqual(calibrate.call_args.kwargs["max_seats"], 1)
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


class EntryPointTests(unittest.TestCase):
    def test_plain_ssh_run_button_starts_remote_desktop_without_launch_settings(self):
        tkinter = SimpleNamespace(Tk=Mock(), TclError=RuntimeError)
        with patch.dict(main.os.environ, {"SSH_CONNECTION": "synthetic"}, clear=True), \
             patch.dict("sys.modules", {"tkinter": tkinter}), patch.object(main.sys, "platform", "linux"), \
             patch.object(main, "prepare_local_display"), patch.object(main, "Launcher"), \
             patch.object(main, "DevelopmentDesktop") as desktop, patch.object(main.os, "chdir"):
            self.assertEqual(main.main([]), 0)
            desktop.return_value.__enter__.assert_called_once()
            desktop.return_value.__exit__.assert_called_once()

    def test_local_option_disables_screen_sharing_even_in_ssh_and_vscode(self):
        tkinter = SimpleNamespace(Tk=Mock(), TclError=RuntimeError)
        environment = {"SSH_CONNECTION": "synthetic", "ZEROFRAME_DEV_REMOTE": "1"}
        with patch.dict(main.os.environ, environment, clear=True), \
             patch.dict("sys.modules", {"tkinter": tkinter}), patch.object(main.sys, "platform", "linux"), \
             patch.object(main, "prepare_local_display"), patch.object(main, "Launcher"), \
             patch.object(main, "DevelopmentDesktop") as desktop, patch.object(main.os, "chdir"):
            self.assertEqual(main.main(["--local"]), 0)
            desktop.assert_not_called()

    def test_vscode_development_mode_owns_desktop_lifetime_without_ssh_environment(self):
        tkinter = SimpleNamespace(Tk=Mock(), TclError=RuntimeError)
        with patch.dict(main.os.environ, {"ZEROFRAME_DEV_REMOTE": "1"}, clear=True), \
             patch.dict("sys.modules", {"tkinter": tkinter}), patch.object(main.sys, "platform", "linux"), \
             patch.object(main, "prepare_local_display"), patch.object(main, "Launcher"), \
             patch.object(main, "DevelopmentDesktop") as desktop, patch.object(main.os, "chdir"):
            self.assertEqual(main.main([]), 0)
            desktop.return_value.__enter__.assert_called_once()
            desktop.return_value.__exit__.assert_called_once()
            tkinter.Tk.return_value.mainloop.assert_called_once()

    def test_normal_launch_does_not_start_remote_desktop(self):
        tkinter = SimpleNamespace(Tk=Mock(), TclError=RuntimeError)
        with patch.dict(main.os.environ, {}, clear=True), patch.dict("sys.modules", {"tkinter": tkinter}), \
             patch.object(main, "prepare_local_display"), patch.object(main, "Launcher"), \
             patch.object(main, "DevelopmentDesktop") as desktop, patch.object(main.os, "chdir"):
            self.assertEqual(main.main([]), 0)
            desktop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
