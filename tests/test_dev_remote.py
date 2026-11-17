import os
from pathlib import Path
import signal
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zeroframe import dev_remote


class DevelopmentDesktopTests(unittest.TestCase):
    def test_wayland_selection_rejects_foreign_and_ambiguous_sessions(self):
        def display(name, uid):
            path = Mock()
            path.name = name
            path.is_socket.return_value = True
            path.stat.return_value = SimpleNamespace(st_uid=uid)
            return path

        for displays, expected in (([display("wayland-2", 42)], "wayland-2"),
                                   ([display("wayland-0", 99)], None),
                                   ([display("wayland-0", 42), display("wayland-1", 42)], None)):
            with self.subTest(expected=expected), patch.dict(os.environ, {}, clear=True), \
                 patch.object(os, "getuid", return_value=42, create=True), \
                 patch.object(dev_remote, "Path") as paths:
                paths.return_value.glob.return_value = displays
                if expected:
                    self.assertEqual(dev_remote.wayland_environment()["WAYLAND_DISPLAY"], expected)
                else:
                    with self.assertRaises(RuntimeError):
                        dev_remote.wayland_environment()

    def test_missing_packages_fail_before_starting_servers(self):
        with patch.object(dev_remote.sys, "platform", "linux"), \
             patch.object(dev_remote.shutil, "which", return_value=None), \
             patch.object(dev_remote.subprocess, "Popen") as start:
            with self.assertRaisesRegex(RuntimeError, "apt install"):
                with dev_remote.DevelopmentDesktop():
                    pass
            start.assert_not_called()

    def session(self, directory):
        assets = Path(directory) / "novnc"
        assets.mkdir()
        (assets / "vnc.html").write_text("synthetic client", encoding="utf-8")
        return assets

    def test_servers_bind_loopback_and_stop_after_launcher_error(self):
        with TemporaryDirectory() as directory:
            assets = self.session(directory)
            processes = [Mock(pid=111), Mock(pid=222)]
            desktop = dev_remote.DevelopmentDesktop()
            with patch.object(dev_remote.sys, "platform", "linux"), \
                 patch.object(dev_remote, "NOVNC_ROOT", assets), \
                 patch.object(dev_remote, "wayland_environment", return_value={"XDG_RUNTIME_DIR": directory}), \
                 patch.object(dev_remote.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
                 patch.object(dev_remote.subprocess, "Popen", side_effect=processes) as start, \
                 patch.object(dev_remote.socket, "socket"), \
                 patch.object(dev_remote, "wait_for_server"), \
                 patch.object(Path, "symlink_to"), \
                 patch.object(signal, "signal"), patch.object(os, "killpg", create=True) as kill:
                with self.assertRaisesRegex(ValueError, "launcher failed"):
                    with desktop:
                        runtime = Path(desktop.directory.name)
                        proxy = start.call_args_list[1].args[0]
                        self.assertEqual(proxy[:3], ["/usr/bin/setpriv", "--pdeathsig", "TERM"])
                        self.assertEqual(proxy[-1], "127.0.0.1:6080")
                        self.assertIn(f"--unix-target={runtime / 'vnc.sock'}", proxy)
                        self.assertIn("--file-only", proxy)
                        self.assertIn("autoconnect=1", (runtime / "web/index.html").read_text())
                        raise ValueError("launcher failed")
                self.assertEqual([call.args[0] for call in kill.call_args_list], [222, 111])
                self.assertFalse(runtime.exists())
                self.assertEqual(desktop.processes, [])

    def test_partial_startup_failure_stops_already_started_server(self):
        with TemporaryDirectory() as directory:
            assets = self.session(directory)
            server = Mock(pid=111)
            with patch.object(dev_remote.sys, "platform", "linux"), \
                 patch.object(dev_remote, "NOVNC_ROOT", assets), \
                 patch.object(dev_remote, "wayland_environment", return_value={"XDG_RUNTIME_DIR": directory}), \
                 patch.object(dev_remote.shutil, "which", return_value="/synthetic/server"), \
                 patch.object(dev_remote.subprocess, "Popen", side_effect=[server, OSError("proxy failed")]), \
                 patch.object(dev_remote.socket, "socket"), \
                 patch.object(dev_remote, "wait_for_server"), \
                 patch.object(Path, "symlink_to"), patch.object(os, "killpg", create=True) as kill:
                with self.assertRaisesRegex(OSError, "proxy failed"):
                    with dev_remote.DevelopmentDesktop():
                        pass
                kill.assert_called_once_with(111, signal.SIGTERM)
                self.assertEqual(list(Path(directory).glob("zeroframe-dev-*")), [])

    def test_hung_server_is_killed_on_shutdown(self):
        desktop = dev_remote.DevelopmentDesktop()
        process = Mock(pid=111)
        process.wait.side_effect = [subprocess.TimeoutExpired("server", 2), 0]
        desktop.processes = [process]
        with patch.object(os, "killpg", create=True) as kill:
            desktop.close()
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    def test_readiness_rejects_failed_process_without_waiting(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(dev_remote, "sleep") as sleep:
            with self.assertRaises(RuntimeError):
                dev_remote.wait_for_server(process, ("127.0.0.1", 6080))
            sleep.assert_not_called()

    def test_readiness_timeout_uses_explicit_clock(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(dev_remote, "monotonic", side_effect=[0, 6]), \
             patch.object(dev_remote.socket, "socket") as socket, patch.object(dev_remote, "sleep") as sleep:
            socket.return_value.__enter__.return_value.connect.side_effect = OSError("not ready")
            with self.assertRaisesRegex(RuntimeError, "초과"):
                dev_remote.wait_for_server(process, ("127.0.0.1", 6080))
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
