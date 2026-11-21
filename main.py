"""Single desktop entry point for calibration and local seat detection."""

import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import socket
import subprocess
import sys

from calibrate import calibrate
from run_vision import run
from zeroframe.dev_remote import DevelopmentDesktop
from zeroframe.roi import load_config


ROOT = Path(__file__).resolve().parent


def run_vlm_preview(config_path, model, camera):
    """Keep Pi swap off until the process owning the preview and model has exited."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("실시간 VLM 재판정는 Linux에서 실행하세요.")
    command = [sys.executable, str(ROOT / "run_vision.py"), "--config", str(config_path),
               "--model", str(model), "--camera", str(camera), "--preview", "--vlm-empty"]
    # Serialize GUI sessions even when the first launcher has already stopped swap.
    with socket.socket(socket.AF_UNIX) as lock:
        try:
            lock.bind("\0zeroframe-vlm-preview")
        except OSError as error:
            raise RuntimeError("VLM 재판정을 시작할 수 없습니다. 다른 비교 창이 열려 있는지 확인하세요.") from error
        active = [line.split()[0] for line in Path("/proc/swaps").read_text().splitlines()[1:]]
        unit = "dev-zram0.swap"
        if active:
            if active != ["/dev/zram0"]:
                raise RuntimeError("다른 스왑이 켜져 있습니다. docs/vlm-probe.md의 수동 실행 방법을 확인하세요.")
            if subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode:
                raise RuntimeError("systemd에서 활성 zram을 확인할 수 없습니다. 스왑 설정을 확인하세요.")
            if subprocess.run(["sudo", "-n", "systemctl", "stop", unit]).returncode:
                raise RuntimeError("스왑을 중지하지 못했습니다. 터미널에서 sudo systemctl stop dev-zram0.swap 실행 후 다시 시도하고, 비교 종료 후 sudo systemctl start dev-zram0.swap 으로 복구하세요.")
        try:
            # The child rechecks /proc/swaps before opening the camera.
            if subprocess.run(command).returncode:
                raise RuntimeError("VLM 재판정이 종료되었습니다. 실행 터미널의 오류를 확인하세요.")
        finally:
            if active and subprocess.run(["sudo", "-n", "systemctl", "start", unit]).returncode:
                raise RuntimeError("스왑 복구에 실패했습니다. sudo systemctl start dev-zram0.swap 을 실행하세요.")


def prepare_local_display():
    """Use the user's single local X display when launched through Remote-SSH."""
    if not sys.platform.startswith("linux") or os.environ.get("DISPLAY"):
        return
    try:
        displays = [path for path in Path("/tmp/.X11-unix").glob("X[0-9]*")
                    if path.name[1:].isdigit() and path.is_socket()
                    and path.stat().st_uid == os.getuid()]
    except OSError:
        return
    if len(displays) != 1:
        return
    os.environ["DISPLAY"] = f":{displays[0].name[1:]}"
    authority = Path.home() / ".Xauthority"
    if authority.is_file():
        os.environ.setdefault("XAUTHORITY", str(authority))


def read_settings(room, count, camera, model):
    room = room.strip()
    if not room or not room.isprintable():
        raise ValueError("방 ID를 입력하세요.")
    if not count.strip().isdecimal() or int(count) < 1:
        raise ValueError("최대 좌석 수는 1 이상의 정수로 입력하세요.")
    try:
        camera_index = int(camera)
    except (TypeError, ValueError) as error:
        raise ValueError("카메라 번호는 0 이상의 정수로 입력하세요.") from error
    if camera_index < 0:
        raise ValueError("카메라 번호는 0 이상의 정수로 입력하세요.")
    model_path = Path(model.strip()).expanduser()
    if not model.strip():
        raise ValueError("모델 파일 경로를 입력하세요.")
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    return room, int(count), camera_index, model_path


class Launcher:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.root = root
        self.dialogs = messagebox
        self.files = filedialog
        self.config_path = ROOT / "roi_config.json"
        root.title("ZeroFrame — 좌석 탐지")
        root.minsize(660, 460)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        body = ttk.Frame(root, padding=24)
        body.grid(sticky="nsew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="ZeroFrame", font=("", 22, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="좌석을 설정하고, 사람과 소지품의 탐지 결과를 확인하세요.").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 20))

        self.room = tk.StringVar(value="IT4-101")
        self.max_seats = tk.StringVar(value="4")
        self.camera = tk.StringVar(value="0")
        self.model = tk.StringVar(value="local/yolo11n.pt")
        for row, (label, variable) in enumerate((
            ("방 ID", self.room), ("최대 좌석 수", self.max_seats),
            ("카메라 번호", self.camera), ("YOLO 모델", self.model),
        ), start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            ttk.Entry(body, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(body, text="찾아보기", command=self.choose_model).grid(row=5, column=2, padx=(8, 0))
        ttk.Label(body, text="좌석 ID는 지정 순서대로 붙습니다. 최대 개수에 도달하기 전에도 저장할 수 있습니다.").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 12))

        self.saved = tk.StringVar()
        ttk.Label(body, textvariable=self.saved, wraplength=600).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(0, 16))
        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=3, sticky="w")
        ttk.Button(buttons, text="탐지 시작", command=lambda: self.launch("detect")).pack(side="left")
        ttk.Button(buttons, text="탐지 + VLM 빈자리 재판정", command=lambda: self.launch("compare")).pack(side="left", padx=4)
        ttk.Button(buttons, text="좌석 설정 / 다시 설정", command=lambda: self.launch("calibrate")).pack(
            side="left", padx=10)
        ttk.Button(buttons, text="종료", command=root.destroy).pack(side="left")

        ttk.Separator(body).grid(row=9, column=0, columnspan=3, sticky="ew", pady=18)
        ttk.Label(body, text="Space로 화면 고정 → 좌석마다 꼭짓점 클릭 후 N → 마지막에 S로 저장\n"
                  "탐지 화면에서 모든 좌석이 비고 잘 보일 때 E → 빈자리 기준 등록 (이번 실행만)\n"
                  "빨강 OCCUPIED · 주황 AWAY · 초록 EMPTY · 회색 UNKNOWN\n"
                  "Q / Esc / 카메라 창 닫기: 시작 화면으로 돌아오기\n"
                  "좌석 영역에는 앉은 사람의 머리·어깨·등이 보일 공간도 포함하세요.\n"
                  "카메라 장치·위치가 바뀌면 좌석을 다시 설정하세요.", justify="left").grid(
            row=10, column=0, columnspan=3, sticky="w")
        self.status = tk.StringVar(value="준비됨. 처음이라면 탐지 시작 후 좌석을 지정하세요.")
        ttk.Label(body, textvariable=self.status, wraplength=600).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(16, 0))
        self.refresh_config()

    def choose_model(self):
        path = self.files.askopenfilename(
            parent=self.root, title="로컬 YOLO 모델 선택", initialdir=ROOT / "local",
            filetypes=[("YOLO 모델", "*.pt *.onnx"), ("모든 파일", "*")],
        )
        if path:
            self.model.set(path)

    def refresh_config(self):
        try:
            config = load_config(self.config_path)
        except FileNotFoundError:
            self.saved.set("저장된 좌석 없음 · 탐지 시작 시 캘리브레이션을 먼저 진행합니다.")
        except (OSError, ValueError) as error:
            self.saved.set(f"좌석 파일을 읽을 수 없습니다. '좌석 설정 / 다시 설정'을 사용하세요.\n{error}")
        else:
            self.room.set(config.room_id)
            if int(self.max_seats.get()) < len(config.seats):
                self.max_seats.set(str(len(config.seats)))
            self.saved.set(f"저장됨: {config.room_id} · {len(config.seats)}석 · "
                           f"{config.frame_width}×{config.frame_height} · roi_config.json")

    def launch(self, action):
        hidden = False
        error_message = None
        try:
            room, max_seats, camera, model = read_settings(
                self.room.get(), self.max_seats.get(), self.camera.get(), self.model.get())
            if action in ("detect", "compare") and not model.exists():
                raise ValueError("YOLO 모델이 없습니다. local/yolo11n.pt를 준비하거나 모델 경로를 선택하세요.\n"
                                 "최초 설치·다운로드 방법은 README.md를 참고하세요.")
            exists = self.config_path.exists()
            needs_calibration = action == "calibrate" or not exists
            if not needs_calibration:
                config = load_config(self.config_path)
                needs_calibration = room != config.room_id or len(config.seats) > max_seats
            if needs_calibration and exists and not self.dialogs.askyesno(
                "좌석 설정 교체", f"{room}의 좌석을 최대 {max_seats}개까지 다시 지정할까요?\n"
                "카메라 화면에서 S로 저장할 때 교체됩니다. Q/Esc로 취소하면 기존 파일을 유지합니다.",
                parent=self.root,
            ):
                return
            # OpenCV owns the foreground until Q/Esc; keep both GUI event loops on the main thread.
            self.root.withdraw()
            hidden = True
            self.root.update_idletasks()
            if needs_calibration:
                config = calibrate(room, max_seats=max_seats, camera=camera, output=self.config_path, overwrite=exists)
                if config is None:
                    self.status.set("좌석 설정을 취소했습니다. 기존 파일은 유지됩니다.")
                    return
            if action in ("detect", "compare"):
                if action == "compare":
                    run_vlm_preview(self.config_path, model, camera)
                else:
                    run(self.config_path, model, camera=camera, preview=True, vlm_compare=False)
                self.status.set("탐지가 종료되었습니다. 다시 시작하거나 좌석을 다시 설정할 수 있습니다.")
            else:
                self.status.set("좌석을 저장했습니다. '탐지 시작'을 누르세요.")
        except KeyboardInterrupt:
            self.status.set("작업을 중지했습니다.")
        except Exception as error:
            error_message = str(error) or type(error).__name__
            self.status.set("실행하지 못했습니다. 오류 내용을 확인하세요.")
        finally:
            if hidden:
                self.refresh_config()
                self.root.deiconify()
                self.root.lift()
        if error_message is not None:
            self.dialogs.showerror("ZeroFrame 실행 오류", error_message, parent=self.root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dev-remote", action="store_true",
                       help="개발 전용: SSH 터널을 통해 노트북 브라우저로 Pi 화면 조작")
    modes.add_argument("--local", action="store_false", dest="dev_remote",
                       help="SSH 접속에서도 Pi 로컬 창만 사용하고 화면 공유 끄기")
    parser.set_defaults(dev_remote=(sys.platform.startswith("linux") and (
        bool(os.environ.get("SSH_CONNECTION")) or os.environ.get("ZEROFRAME_DEV_REMOTE") == "1"
    )))
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
    except ImportError:
        print("Tkinter가 필요합니다. Windows: Python의 Tcl/Tk 구성 요소 설치, Pi OS: sudo apt install python3-tk", file=sys.stderr)
        return 1
    os.chdir(ROOT)
    prepare_local_display()
    try:
        with DevelopmentDesktop() if args.dev_remote else nullcontext():
            root = tk.Tk()
            Launcher(root)
            root.mainloop()
    except KeyboardInterrupt:
        return 0
    except (tk.TclError, OSError, RuntimeError) as error:
        print(f"시작 창을 열 수 없습니다: {error}\n"
              "Pi 데스크톱에 로그인하세요. 개발용 원격 조작은 main.py --dev-remote로 실행합니다.\n"
              "화면 없는 운영은 run_vision.py를 --preview 없이 실행하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
