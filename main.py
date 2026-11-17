"""Single desktop entry point for calibration and local seat detection."""

import os
from pathlib import Path
import re
import sys

from calibrate import calibrate
from run_vision import run
from zeroframe.roi import load_config, validate_ids


ROOT = Path(__file__).resolve().parent


def read_settings(room, seats, camera, model):
    room = room.strip()
    seat_ids = re.split(r"[\s,]+", seats.strip()) if seats.strip() else []
    validate_ids(room, seat_ids)
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
    return room, seat_ids, camera_index, model_path


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
        self.seats = tk.StringVar(value="A-01")
        self.camera = tk.StringVar(value="0")
        self.model = tk.StringVar(value="local/yolo11n.pt")
        for row, (label, variable) in enumerate((
            ("방 ID", self.room), ("좌석 ID", self.seats),
            ("카메라 번호", self.camera), ("YOLO 모델", self.model),
        ), start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            ttk.Entry(body, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(body, text="찾아보기", command=self.choose_model).grid(row=5, column=2, padx=(8, 0))
        ttk.Label(body, text="좌석 예: A-01 A-02 A-03  ·  카메라 기본값: 0").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 12))

        self.saved = tk.StringVar()
        ttk.Label(body, textvariable=self.saved, wraplength=600).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(0, 16))
        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=3, sticky="w")
        ttk.Button(buttons, text="탐지 시작", command=lambda: self.launch("detect")).pack(side="left")
        ttk.Button(buttons, text="좌석 설정 / 다시 설정", command=lambda: self.launch("calibrate")).pack(
            side="left", padx=10)
        ttk.Button(buttons, text="종료", command=root.destroy).pack(side="left")

        ttk.Separator(body).grid(row=9, column=0, columnspan=3, sticky="ew", pady=18)
        ttk.Label(body, text="좌석 설정: Space → 꼭짓점 클릭 → N(좌석 확정) → S(저장)\n"
                  "결과 화면: 빨강 OCCUPIED · 주황 AWAY · 회색 UNKNOWN\n"
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
            self.seats.set(" ".join(seat.seat_id for seat in config.seats))
            self.saved.set(f"저장됨: {config.room_id} · {len(config.seats)}석 · "
                           f"{config.frame_width}×{config.frame_height} · roi_config.json")

    def launch(self, action):
        hidden = False
        error_message = None
        try:
            room, seats, camera, model = read_settings(
                self.room.get(), self.seats.get(), self.camera.get(), self.model.get())
            if action == "detect" and not model.exists():
                raise ValueError("YOLO 모델이 없습니다. local/yolo11n.pt를 준비하거나 모델 경로를 선택하세요.\n"
                                 "최초 설치·다운로드 방법은 README.md를 참고하세요.")
            exists = self.config_path.exists()
            needs_calibration = action == "calibrate" or not exists
            if not needs_calibration:
                config = load_config(self.config_path)
                needs_calibration = (room != config.room_id
                                     or seats != [seat.seat_id for seat in config.seats])
            if needs_calibration and exists and not self.dialogs.askyesno(
                "좌석 설정 교체", f"기존 좌석 설정 전체를 {room} / {', '.join(seats)}로 다시 지정할까요?\n"
                "카메라 화면에서 S로 저장할 때 교체됩니다. Q/Esc로 취소하면 기존 파일을 유지합니다.",
                parent=self.root,
            ):
                return
            # OpenCV owns the foreground until Q/Esc; keep both GUI event loops on the main thread.
            self.root.withdraw()
            hidden = True
            self.root.update_idletasks()
            if needs_calibration:
                config = calibrate(room, seats, camera=camera, output=self.config_path, overwrite=exists)
                if config is None:
                    self.status.set("좌석 설정을 취소했습니다. 기존 파일은 유지됩니다.")
                    return
            if action == "detect":
                run(self.config_path, model, camera=camera, preview=True)
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


def main():
    try:
        import tkinter as tk
    except ImportError:
        print("Tkinter가 필요합니다. Windows: Python의 Tcl/Tk 구성 요소 설치, Pi OS: sudo apt install python3-tk", file=sys.stderr)
        return 1
    os.chdir(ROOT)
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"로컬 데스크톱 화면을 열 수 없습니다: {error}", file=sys.stderr)
        return 1
    Launcher(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
