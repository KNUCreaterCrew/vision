"""Development-only desktop access through an SSH-forwarded loopback port."""

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep


PORT = 6080
NOVNC_ROOT = Path("/usr/share/novnc")
INDEX = ('<!doctype html><meta charset="utf-8">'
         '<meta http-equiv="refresh" content="0; url=vnc.html?'
         'autoconnect=1&amp;resize=scale&amp;path=websockify">')


def wayland_environment():
    environment = os.environ.copy()
    runtime = Path(environment.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    display = environment.get("WAYLAND_DISPLAY")
    candidates = [runtime / display] if display else list(runtime.glob("wayland-*"))
    displays = [path for path in candidates if path.is_socket() and path.stat().st_uid == os.getuid()]
    if len(displays) != 1:
        raise RuntimeError("개발용 원격 화면에는 Pi의 로그인된 Wayland 데스크톱이 하나 필요합니다.")
    environment.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY=displays[0].name)
    return environment


def wait_for_server(process, address, *, timeout=5):
    deadline = monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError("개발용 원격 화면 서버가 종료됐습니다. 터미널 오류와 6080 포트 사용 여부를 확인하세요.")
        try:
            family = socket.AF_UNIX if isinstance(address, str) else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.1)
                connection.connect(address)
            return
        except OSError:
            if monotonic() >= deadline:
                raise RuntimeError("개발용 원격 화면 서버의 시작 시간이 초과됐습니다.") from None
            sleep(0.05)


class DevelopmentDesktop:
    """Own both servers for the lifetime of the launcher; never enable a boot service."""

    def __init__(self):
        self.processes = []
        self.directory = None
        self.previous_sigterm = None
        self.supervisor = None

    def start_process(self, command, environment):
        command = [self.supervisor, "--pdeathsig", "TERM", *command]
        process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self.processes.append(process)
        return process

    def __enter__(self):
        try:
            if not sys.platform.startswith("linux"):
                raise RuntimeError("개발용 원격 화면은 Raspberry Pi OS Linux에서 사용하세요.")
            wayvnc = shutil.which("wayvnc")
            websockify = shutil.which("websockify")
            self.supervisor = shutil.which("setpriv")
            if not wayvnc or not websockify or not self.supervisor or not (NOVNC_ROOT / "vnc.html").is_file():
                raise RuntimeError("개발용 화면 패키지가 필요합니다: sudo apt install wayvnc novnc websockify util-linux")
            environment = wayland_environment()
            # Refuse occupied ports instead of connecting the browser to another service.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", PORT))
                probe.listen(1)
            self.directory = TemporaryDirectory(prefix="zeroframe-dev-", dir=environment["XDG_RUNTIME_DIR"])
            directory = Path(self.directory.name)
            web_root = directory / "web"
            web_root.mkdir()
            for source in NOVNC_ROOT.iterdir():
                if source.name != "index.html":
                    (web_root / source.name).symlink_to(source, target_is_directory=source.is_dir())
            (web_root / "index.html").write_text(INDEX, encoding="utf-8")
            vnc_socket = str(directory / "vnc.sock")
            server = self.start_process([
                wayvnc, "--config=/dev/null", "--log-level=error", "--max-fps=10", "--unix-socket",
                f"--socket={directory / 'control.sock'}", vnc_socket,
            ], environment)
            wait_for_server(server, vnc_socket)
            proxy = self.start_process([
                websockify, "--file-only", f"--web={web_root}", f"--unix-target={vnc_socket}",
                f"127.0.0.1:{PORT}",
            ], environment)
            wait_for_server(proxy, ("127.0.0.1", PORT))
            self.previous_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self.on_sigterm)
            print(f"개발용 원격 화면: http://127.0.0.1:{PORT}/", flush=True)
            print("VS Code의 6080 포트를 SSH로 전달해 노트북 브라우저에서 조작하세요. 앱 종료 시 화면 공유도 종료됩니다.", flush=True)
            return self
        except BaseException:
            self.close()
            raise

    @staticmethod
    def on_sigterm(signum, frame):
        raise SystemExit(128 + signum)

    def close(self):
        for process in reversed(self.processes):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except ProcessLookupError:
                process.wait(timeout=2)
        self.processes.clear()
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
        if self.previous_sigterm is not None:
            signal.signal(signal.SIGTERM, self.previous_sigterm)
            self.previous_sigterm = None

    def __exit__(self, *exc):
        self.close()
