# ZeroFrame Vision

프라이버시 보호형 좌석 탐지 시스템의 엣지 판정 모듈입니다.
개발은 **Windows 노트북과 내장 카메라**로 진행하며, 우선 운영 대상은 **Raspberry Pi 5(8GB, AI 가속기 없음) + 64비트 Raspberry Pi OS + USB 웹캠**입니다.
Windows와 Linux의 공통 코드를 유지하고, 플랫폼별 카메라 입력과 설치 방법을 구분합니다. 충돌하는 구현 선택은 Linux 운영 환경을 우선합니다.
전체 요구사항은 [v1.1 명세서](zeroframe_v1.1_local_multimodal.md), 세부 검증 기록은 [구현 진행 기록](docs/implementation-plan.md)을 참고하세요.

## 전체 5단계와 현재 진행 상황

| 단계 | 목표와 구현 범위 | 현재 상태 | 완료 확인 기준 |
| :--- | :--- | :--- | :--- |
| **1. 상태 판정 핵심** | 4상태 모델, 설정 검증, 시간 기반 전이, 관측 만료, 가짜 관측 데모 | **완료** | 경계 시각·잘못된 입력·관측 중단 테스트 통과 |
| **2. 카메라·좌석 ROI** | 내장 카메라 미리보기, RAM 화면 고정, 좌석 영역 지정, JSON 저장·검증, bbox 매핑 | **구현·자동 테스트 완료 / 사용자 저장 확인 대기** | 실제 카메라에서 좌석을 지정하고 유효한 좌표 JSON 저장 |
| **3. YOLO 연결** | YOLO11n 탐지 → ROI 매핑 → 좌석별 상태 머신 연결 | **구현·자동 테스트 완료 / 실모델·카메라 확인 대기** | 실제 사람·짐 탐지, 경계·가림 사례 확인, 노트북 성능 측정 |
| **4. 백엔드 전송** | 상태 JSON, 인증 헤더, 변경 전송, 10초 하트비트, 통신 실패 처리 | **미구현** | 테스트 수신 서버에서 계약 검증, 통신 지연·단절에도 분석 지속 |
| **5. 선택적 VLM·최종 통합 검증** | 로컬 SmolVLM2 검증, 단일 워커·제한 큐·타임아웃·오래된 결과 폐기, 실제 백엔드 연동·현장 보정 | **미구현** | VLM 실패 시 안전한 대체 동작, 정확도·지연·RAM·프라이버시 점검 |

**현재 확인된 내용:** 자동 테스트 77개가 통과했고, 사용자의 실행 로그에서 Windows 카메라 캘리브레이션 화면 진입을 확인했습니다.
좌표 JSON 저장 성공과 수정한 화면 안내의 실제 조작 결과는 아직 확인하지 않았습니다.
**가짜 관측 데모, 좌석 영역 지정 도구, 로컬 YOLO 실행기**가 있습니다.
YOLO 실행 코드는 모의 테스트를 통과했으며, 실제 모델과 카메라를 함께 실행하는 확인은 남아 있습니다.
백엔드 전송과 VLM은 아직 연결되지 않았습니다.

3단계는 먼저 노트북에서 동작을 확인하고, Pi 확보 후 NCNN/ONNX 경로를 같은 조건에서 비교합니다.
5단계의 **VLM은 선택 기능**입니다. 모델·런타임 호환성, 정확도, 자원 사용량을 실측한 뒤 활성화합니다.
VLM을 제외하더라도 최종 통합·장애·품질 검증은 수행해야 합니다.
검증 없는 빈자리를 임의로 확정하지 않으므로 VLM 미사용 시 일부 좌석이 `UNKNOWN`으로 남을 수 있습니다.

이 저장소는 Vision 영역만 담당합니다. 백엔드의 상태 저장·30초 오프라인 판정·WebSocket,
프론트엔드의 좌석 지도·관리자 화면은 별도 저장소의 책임입니다.

## 설치 및 빠른 시작

명령은 **저장소 루트**에서 OS에 맞는 터미널로 실행합니다.
코드는 Python 3.10 이상을 대상으로 하며, 상태 데모와 자동 테스트는 표준 라이브러리만 사용합니다.
카메라 도구의 OpenCV는 Windows에서는 pip, Raspberry Pi OS에서는 운영체제 패키지로 설치합니다.

### Windows 노트북: PowerShell

일반 Windows용 CPython 3.13(x64) 기준입니다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -c "import cv2; print(cv2.__version__)"
```

이미 가상환경과 OpenCV를 설치했다면 아래 실행 명령부터 사용하면 됩니다.
가상환경 활성화 없이 실행하므로 PowerShell 실행 정책을 변경할 필요가 없습니다.
설치 방식은 [Python Windows 공식 안내](https://docs.python.org/3.13/using/windows.html)를 참고하세요.

Windows에서는 화면 창을 지원하는 `opencv-python==4.13.0.92`를 사용합니다.
`headless` 또는 다른 OpenCV 배포판을 같은 환경에 함께 설치하지 마세요.
[OpenCV 패키지 공식 안내](https://pypi.org/project/opencv-python/4.13.0.92/)

### Raspberry Pi OS 64비트 Linux: Bash

현재 카메라 경로는 **USB 웹캠의 V4L2 입력**을 대상으로 합니다. Python 3.10 이상인 Raspberry Pi OS 환경에서 사용합니다.
Pi의 로컬 데스크톱에서 모니터·마우스로 캘리브레이션합니다.
Raspberry Pi OS Lite나 화면 없는 SSH 세션에서는 클릭 방식의 도구를 실행할 수 없습니다.

```bash
sudo apt update
sudo apt install -y python3-opencv python3-venv v4l-utils
python3 -m venv --system-site-packages .venv
.venv/bin/python -c "import cv2; print(cv2.__version__)"
v4l2-ctl --list-devices
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python calibrate.py --room IT4-101 --seats A-01 --camera 0
```

`--system-site-packages`로 apt에서 설치한 OpenCV를 가상환경에서도 읽습니다.
이 경로에서는 pip OpenCV를 추가로 설치하지 않습니다. `requirements.txt`의 OpenCV 항목은 Windows에서만 적용됩니다.
Linux의 OpenCV 버전은 OS 패키지를 따르며, 실제 장비에서 GUI·V4L2 기능을 확인해야 합니다.
[Debian OpenCV 패키지](https://packages.debian.org/trixie/python3-opencv), [Python venv 안내](https://docs.python.org/3/library/venv.html)

장치 목록에서 USB 웹캠에 해당하는 번호를 선택하세요. 예를 들어 `/dev/video2`라면 `--camera 2`를 사용합니다.
`v4l2-ctl --list-devices`는 장치 목록을 확인하는 명령이며 이미지를 저장하지 않습니다.
[v4l2-ctl 공식 매뉴얼](https://manpages.debian.org/trixie/v4l-utils/v4l2-ctl.1.en.html)

권한 오류가 나면 `ls -l /dev/video*`와 `id -nG`로 장치 권한과 사용자 그룹을 확인합니다.
장치가 `video` 그룹 권한을 요구하고 사용자가 그 그룹에 없다면 `sudo usermod -aG video "$USER"` 후 다시 로그인합니다.
[Raspberry Pi USB 웹캠 안내](https://www.raspberrypi.com/documentation/accessories/camera-software.html#use-a-usb-webcam)

Windows에서 만든 `.venv`는 Pi로 복사하지 않고 Pi에서 새로 만듭니다.
노트북의 좌표 파일도 재사용하지 말고 **Pi에 연결한 카메라의 실제 설치 각도·해상도로 다시 지정**합니다.
이미지는 장치 내부에만 두므로 캘리브레이션 화면을 VNC나 SSH X11 포워딩으로 외부 전송하지 않습니다.

### 지원 범위와 검증 상태

| 항목 | Windows 노트북 | Raspberry Pi OS Linux |
| :--- | :--- | :--- |
| 상태 머신·ROI·JSON | 공통 Python 코드 | 동일 코드 |
| 내장/USB 웹캠 | DirectShow 입력 | V4L2 USB 웹캠 입력 |
| 클릭 캘리브레이션 | 로컬 OpenCV 창 | 로컬 데스크톱 OpenCV 창 필요 |
| CSI 리본 케이블 카메라 | 대상 아님 | 현재 미지원; Picamera2 캡처 경로 별도 구현 필요 |
| 실물 확인 | 사용자 카메라 화면 진입 확인 | Pi·웹캠 실물 미검증 |

USB 웹캠과 CSI 카메라의 소프트웨어 경로를 혼동하지 마세요.
CSI 모듈은 libcamera 기반 Picamera2 경로를 확인해야 하며 현재 `--camera` 번호만 바꿔서 지원된다고 보장하지 않습니다.
[Raspberry Pi Picamera2 안내](https://www.raspberrypi.com/documentation/accessories/camera-software.html#use-libcamera-from-python-with-picamera2)

Windows/Linux 백엔드 선택과 화면 없는 Linux 오류 처리는 모의 테스트를 통과했습니다.
Linux 분기를 시뮬레이션한 테스트이며 실제 Linux OS·ARM64·카메라 드라이버를 실행한 결과는 아닙니다.
YOLO는 실행 코드를 추가했지만 모델 호환성·Pi 성능은 아직 확인하지 않았습니다. VLM은 미구현입니다.

### 목적별 실행 명령 (Windows)

| 목적 | 명령 |
| :--- | :--- |
| 상태 판정 데모 | `.\.venv\Scripts\python.exe -m zeroframe` |
| 전체 자동 테스트 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| 좌석 하나 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01` |
| 좌석 두 개 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 A-02` |
| 기존 좌표 다시 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 --overwrite` |
| 도구 도움말 | `.\.venv\Scripts\python.exe calibrate.py --help` |
| YOLO 실시간 확인 | `.\.venv\Scripts\python.exe run_vision.py --config roi_config.json --model local/yolo11n.pt --preview` |

`--overwrite`는 새로 지정한 좌석 목록으로 **파일 전체를 교체**합니다. 위 예시는 좌석 하나만 저장하므로,
두 좌석을 유지하려면 `--seats A-01 A-02`를 지정하세요.
YOLO는 아래 3단계의 추가 설치·모델 준비가 필요합니다. 서버 전송·VLM 명령은 아직 없습니다.
Linux에서는 위 표의 `.\.venv\Scripts\python.exe`를 `.venv/bin/python`으로 바꾸고 Bash에서 실행합니다.

## 1단계: 상태 판정 데모와 테스트

`python -m zeroframe`는 실제 시간이 흐르기를 기다리지 않고 가짜 관측으로
`UNKNOWN → OCCUPIED → AWAY → EMPTY → UNKNOWN`을 출력합니다.
**카메라·YOLO·VLM·네트워크는 사용하지 않습니다.**

데모의 `3s`는 사람 탐지가 2초간 지속된 시점, `8s`는 짐만 보이는 상태가 4초간 지속된 시점입니다.
`14s`에는 테스트용 빈자리 검증 신호를 넣고, `44s`에는 마지막 유효 판정 후 30초 만료를 확인합니다.

현재 자동 테스트는 기존 상태·ROI·캘리브레이션 55개에 최신 프레임 캡처·YOLO 어댑터·매핑·실행기 22개를 더해 총 77개입니다.
카메라와 화면 이벤트는 가짜 입력으로 검증하므로 테스트 통과가 실제 영상의 인식 정확도를 뜻하지 않습니다.
테스트 출력 마지막의 `Ran 77 tests`와 `OK`를 확인하세요.
가상환경이 없어도 `python -m zeroframe`, `python -m unittest discover -s tests -v`는 실행할 수 있습니다.

## 2단계: 노트북 카메라로 좌석 지정

처음에는 좌석 하나로 시험하는 편이 간단합니다.

```powershell
.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01
```

1. 카메라 미리보기에서 촬영 각도를 맞추고 **Space**를 눌러 화면을 고정합니다.
2. `A-01` 좌석의 책상·의자를 포함하는 영역의 꼭짓점을 순서대로 클릭합니다.
   사각형이라면 **좌상 → 우상 → 우하 → 좌하** 순서입니다. 최소 3개가 필요하며 첫 꼭짓점을 다시 찍지 않습니다.
3. **N** 또는 **Enter**로 좌석을 확정하고 다음 좌석도 지정합니다.
4. 모든 좌석을 확정한 뒤 **S**로 `roi_config.json`을 저장합니다.
5. **U**는 마지막 꼭짓점 취소입니다. 작성 중인 점이 없으면 직전 확정 좌석을 다시 편집합니다.
   **Q / Esc / 창 닫기**는 저장 없이 취소합니다.

`--seats A-01 A-02`는 사각형 하나의 이름이 아니라 **별도 좌석 두 개**를 의미합니다.
카메라 창에 포커스를 두고 각 좌석을 `클릭 → N`으로 확정한 뒤 마지막에 `S`를 누르세요.
화면의 `Seat`, `Points`, `Done`에서 현재 좌석·등록된 점 수·확정 좌석 수를 확인합니다.
예를 들어 `Done: 1/2`, `Seat: A-02`, `Points: 0`이면 첫 좌석은 확정했고 두 번째 좌석을 새로 찍어야 합니다.
`N`을 누른 뒤 점 개수가 0이 되는 것은 다음 좌석으로 넘어간 동작입니다.

이미지는 RAM에만 두고 카메라는 화면 고정 시 해제합니다. 저장하는 것은 좌표·좌석 ID·방 ID·실제 해상도뿐입니다.
현재 단계는 라이브 영상을 보고 좌석 영역을 지정하는 도구이며, 사람·짐 인식 결과를 표시하지 않습니다.

기존 파일은 자동으로 덮어쓰지 않습니다. 다시 지정하려면 다음처럼 명시합니다.

```powershell
.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 A-02 --overwrite
```

카메라가 열리지 않으면 Windows 카메라 권한과 다른 앱의 사용 여부를 확인하세요.
다른 장치를 선택하려면 `--camera 1`, 다른 방의 좌표 파일은 `--output roi_config_IT4-102.json`을 사용합니다.
`roi_config*.json`은 Git에서 제외하며, 검토한 가짜 좌표 예제 `examples/roi_config.example.json`만 포함합니다.
다른 이름의 현장 좌표는 Git에서 제외되는 `local/` 아래에 저장하세요. 폴더를 먼저 만들어야 합니다.

```powershell
New-Item -ItemType Directory -Force .\local
.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 --output local/other_room.json
```

카메라 위치·해상도가 바뀌면 다시 캘리브레이션해야 합니다.
Linux에서 별도 폴더를 만들 때는 `New-Item` 대신 `mkdir -p local`을 사용합니다.

### 옵션

| 옵션 | 의미 | 기본값 |
| :--- | :--- | :--- |
| `--room` | 방 ID | 필수 |
| `--seats` | 지정할 좌석 ID 목록과 순서 | 필수 |
| `--camera` | 로컬 카메라 번호 | `0` |
| `--output` | 저장할 좌표 JSON 경로 | `roi_config.json` |
| `--overwrite` | 기존 파일 전체를 명시적으로 교체 | 사용하지 않음 |

### 저장 성공 확인

터미널에 `Saved ... seat polygons ... no images saved.`가 출력되면 저장한 파일을 확인하세요.

```powershell
Get-Content -Encoding UTF8 .\roi_config.json
.\.venv\Scripts\python.exe -c "from zeroframe.roi import load_config; c = load_config('roi_config.json'); print(c.room_id, c.frame_width, c.frame_height, [s.seat_id for s in c.seats])"
```

파일에는 `room_id`, `frame_width`, `frame_height`, `seats`만 들어갑니다.
각 좌석은 `seat_id`와 `polygon` 좌표를 가집니다. 해상도는 요청한 640×480이 아니라 **실제 캡처된 크기**를 저장합니다.
다른 `--output`을 사용했다면 위 명령의 파일명도 바꾸세요.
Linux에서는 `Get-Content` 대신 `cat roi_config.json`, Python 실행 경로는 `.venv/bin/python`을 사용합니다.

## 3단계: YOLO 사람·소지품 탐지

저장한 **실제 카메라의** `roi_config.json`이 필요합니다. 예제 좌표를 현장 좌표로 사용하지 마세요.
좌석 일괄 생성 기능은 아직 없으며, 현재 캘리브레이션 방식은 그대로 사용합니다.

### Windows 추가 설치·모델 준비

캘리브레이션 프로그램을 종료한 뒤 설치합니다. PyTorch 등으로 다운로드 용량이 커질 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-yolo.txt
New-Item -ItemType Directory -Force .\local
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt" -OutFile .\local\yolo11n.pt
.\.venv\Scripts\python.exe run_vision.py --config roi_config.json --model local/yolo11n.pt --preview
```

모델 다운로드는 최초 준비 때만 합니다. 같은 파일을 다시 다운로드하면 덮어쓰므로 이미 있다면 생략하세요.
공식 [YOLO11 모델 배포처](https://github.com/ultralytics/assets/releases/tag/v8.3.0)의 가중치만 사용하세요.
`.pt`는 신뢰하는 파일만 로드합니다. 모델과 현장 좌표는 Git에서 제외됩니다.
어댑터는 내부 프레임 참조 해제 대상이 달라지는 것을 방지하기 위해 **`ultralytics==8.3.253`**을 요구합니다.

### Raspberry Pi OS 추가 설치·실행

앞의 `--system-site-packages` 가상환경을 사용합니다. apt OpenCV를 유지하기 위해
**Pi에서는 `requirements-yolo.txt`로 설치하지 않고** 나머지 의존성과 Ultralytics를 분리합니다.

```bash
sudo apt install -y python3-numpy python3-matplotlib python3-pil python3-yaml python3-requests python3-scipy python3-psutil
.venv/bin/python -m pip install torch torchvision polars ultralytics-thop
.venv/bin/python -m pip install --no-deps ultralytics==8.3.253
mkdir -p local
curl --fail --location https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt --output local/yolo11n.pt
.venv/bin/python -c "import cv2, torch, torchvision, ultralytics; print(cv2.__version__, torch.__version__, ultralytics.__version__)"
.venv/bin/python run_vision.py --config roi_config.json --model local/yolo11n.pt
```

위 의존성은 고정 버전의 [Ultralytics 패키지 정의](https://github.com/ultralytics/ultralytics/blob/v8.3.253/pyproject.toml)에 맞춥니다.
Pi의 ARM64 wheel·apt/pip 조합은 실물 미검증입니다. import가 실패하면 실행 전에 의존성을 확인하세요.
`pip check`는 apt의 `python3-opencv`를 `opencv-python`으로 인식하지 못할 수 있습니다.

실행기 기본값은 화면 없이 상태만 출력하므로 SSH에서도 사용할 수 있습니다.
Pi에 연결한 **로컬 모니터**에서 확인할 때만 `--preview`를 추가하세요. VNC 등으로 영상을 외부 전송하지 않습니다.
USB 웹캠은 `--camera` 번호로 선택하며, CSI 카메라는 계속 미지원입니다.

Pi CPU에서는 `.pt`가 느릴 수 있습니다. 준비 단계에서 공식 [NCNN 변환](https://docs.ultralytics.com/integrations/ncnn/),
[ONNX 변환](https://docs.ultralytics.com/integrations/onnx/)에 따라 YOLO11n을 `imgsz=640`으로 변환하고,
해당 런타임(`ncnn` 또는 `onnxruntime`)을 설치한 뒤 `--model local/yolo11n_ncnn_model` 또는
`--model local/yolo11n.onnx`로 지정할 수 있습니다. NCNN 폴더의 `model.ncnn.param`, `model.ncnn.bin`,
`metadata.yaml`을 함께 유지하세요. 변환·추가 패키지 설치는 카메라 실행 전에 끝냅니다.
현재 로더는 이 형식을 받지만 실제 변환·추론·성능 비교는 확인 대기입니다.

### 동작과 화면 확인

- 기본 `--fps 2`는 분석 목표이며 `1`~`3`으로 지정합니다. 실제 처리량을 보장하지 않습니다.
- `--preview`는 ROI, 좌석 상태, 탐지 상자·객체 confidence, 평균 분석 FPS, `predict` 시간을 표시합니다.
  객체 confidence는 좌석 상태의 정확도 확률이 아닙니다. 화면 없이 실행해도 상태·속도는 터미널에 출력됩니다.
- 사람은 `0.50`, backpack/handbag/laptop/book은 `0.45` 이상을 사용합니다. 낮은 신뢰도(검출 하한 `0.25`)나
  여러 좌석에 걸친 하단 중심점은 관련 좌석의 확정을 보류합니다. 범위 밖 탐지는 좌석에 할당하지 않습니다.
- 사람 2초 → `OCCUPIED`, 짐만 3초 → `AWAY`, 기존 점유에서 짐만 남으면 4초 → `AWAY`입니다.
  사람이 없어진 뒤에도 짐이 보이지 않으면 기존 상태를 잠시 유지하고 마지막 유효 판정 30초 후 `UNKNOWN`입니다.
- **이 단계에서는 `EMPTY`가 나오지 않는 것이 정상입니다.** 빈자리 검증기는 아직 연결하지 않았습니다.
- 캡처는 최신 프레임 하나만 보관합니다. 소프트웨어 읽기 시각부터 결과 완료까지 2초를 초과한 추론은
  상태 갱신에 사용하지 않습니다. 관측 간격도 2초를 넘으면 연속 확인을 다시 시작하므로 느린 장비에서는 `UNKNOWN`이 많을 수 있습니다.
- 미리보기 창에서 **Q / Esc / 창 닫기**, 화면 없이 실행할 때는 **Ctrl+C**로 종료합니다.
  추론 중에는 입력 처리가 지연될 수 있습니다. 카메라 읽기 오류는 자원을 정리하고 종료하며 자동 재연결은 없습니다.

먼저 좌석 하나에서 사람 착석, 사람 퇴장 후 가방만 남기기, 모두 치우기를 시험하세요.
책상 위 노트북·공용 책도 소지품으로 인식될 수 있습니다. 통로 보행자, 가림, 인접 좌석은 별도 확인이 필요하며
현재 코드에 일반적인 가림 감지기가 구현된 것은 아닙니다.

실행기는 로컬 모델만 허용하고 자동 설치·온라인 확인·사용 통계 동기화를 끕니다.
이미지·crop·라벨 저장 및 라이브러리 화면 출력을 명시적으로 끄고, 직접 만든 로컬 미리보기만 사용합니다.
추론 후 라이브러리가 보관하던 원본 프레임 참조를 해제합니다. `local/ultralytics/`에는 비영상 설정이 생길 수 있습니다.
OS 스왑·드라이버 내부 버퍼·실제 네트워크 무전송 검증은 별도입니다.
[Ultralytics 추론 인터페이스](https://docs.ultralytics.com/modes/predict/),
[고정 버전 예측기 소스](https://github.com/ultralytics/ultralytics/blob/v8.3.253/ultralytics/engine/predictor.py)

## 자주 발생하는 문제

| 증상 | 확인할 내용과 해결 방법 |
| :--- | :--- |
| `polygon needs at least three vertices` | 현재 좌석에 점이 3개 미만입니다. `Points`를 확인하고 꼭짓점을 클릭한 뒤 `N`을 누르세요. |
| 저장 시 `seats confirmed` 또는 `Confirm every requested seat` | 모든 좌석을 확정하지 않았습니다. 좌석별로 `클릭 → N`을 반복하고 마지막에 `S`를 누르세요. |
| 점이 연결되지만 확정되지 않음 | `N`을 누르세요. 교차하거나 첫 점을 중복 클릭했다면 `U`로 수정하세요. |
| 키가 먹히지 않음 | 터미널 대신 카메라 창을 클릭해 포커스를 둡니다. 수정한 코드는 프로그램을 종료하고 다시 실행해야 적용됩니다. |
| `already exists` | 기존 파일이 있습니다. 다른 `--output`을 쓰거나 전체 교체 의도로 `--overwrite`를 지정하세요. |
| `Cannot open camera` / `Camera frame unavailable` | Windows 카메라 권한, 다른 앱의 점유, 카메라 번호를 확인합니다. 다른 장치는 `--camera 1`로 선택할 수 있습니다. |
| Linux에서 카메라를 열지 못함 | `v4l2-ctl --list-devices`로 USB 웹캠 번호를 확인하고 장치 권한·`video` 그룹을 점검합니다. CSI 모듈은 현재 캡처 경로의 대상이 아닙니다. |
| `Calibration needs a local Linux desktop` | 화면 없는 SSH/Lite 환경입니다. 모니터·마우스가 연결된 Pi의 로컬 데스크톱에서 실행합니다. 상태 데모·자동 테스트는 화면 없이 실행할 수 있습니다. |
| Linux 가상환경에서 `OpenCV is unavailable` | apt의 `python3-opencv` 설치 여부와 `.venv` 생성 시 `--system-site-packages` 사용 여부를 확인합니다. |
| `OpenCV is unavailable` / `No module named cv2` | 패키지를 설치한 가상환경의 Python으로 실행하는지 확인합니다. 위 설치 명령을 사용하세요. |
| `externally-managed-environment` | MSYS2 Python의 시스템 환경에 설치하려고 한 경우입니다. Windows CPython으로 만든 `.venv`를 사용하세요. |
| `py -3.13` 실행 실패 / `did not find executable` | Windows Python 설치·등록 또는 가상환경의 원본 Python 경로를 확인합니다. 원본 Python을 옮기거나 제거했다면 가상환경도 다시 준비해야 합니다. |
| `frame resolution changed` | 저장한 좌표와 프레임 크기가 다릅니다. 같은 해상도를 사용하거나 다시 캘리브레이션하세요. |
| `existing local .pt` | 모델 파일이 없거나 형식이 잘못되었습니다. 준비 단계에서 다운로드하고 `--model` 경로를 확인하세요. 실행 중 자동 다운로드하지 않습니다. |
| `YOLO dependencies unavailable` | Windows는 `requirements-yolo.txt`, Pi는 위의 분리 설치 방법을 사용합니다. 패키지를 설치한 Python으로 실행하세요. |
| `stale=True` / `STALE: discarded` | 캡처부터 추론 완료까지 2초를 초과했습니다. Pi에서는 NCNN/ONNX 비교가 필요합니다. 임의로 오래된 결과를 최신 판정으로 사용하지 않습니다. |
| 빈 좌석이 `UNKNOWN` | 3단계는 빈자리 검증기가 없어 `EMPTY`를 생성하지 않습니다. 정상적인 보수적 동작입니다. |
| `Camera driver did not stop` | 카메라 드라이버 읽기가 종료되지 않았습니다. 프로세스를 완전히 종료한 뒤 장치를 다시 연결합니다. |

좌표 폴리곤이 실제 좌석에 맞는지는 사용자가 직접 확인해야 합니다. JSON 형식 검증만으로 공간 정합성이 확인되지는 않습니다.

## 좌표 검증과 매핑

[좌표 예제](examples/roi_config.example.json)는 가짜 좌표이며 실제 설치 환경에서 다시 지정해야 합니다.
중복 좌석 ID, 화면 밖 좌표, 면적 없는 다각형, 자기 교차, 알 수 없는 JSON 필드는 거부합니다.
오목한 다각형도 지원합니다. 좌석 간 겹침은 허용하지만 매핑 결과의 모든 후보를 반환합니다.

```python
from zeroframe.roi import load_config

config = load_config("examples/roi_config.example.json")
candidates = config.matching_seat_ids(
    (100, 120, 200, 300),  # bbox: x1, y1, x2, y2
    frame_width=640,
    frame_height=480,
)
print(candidates)  # ('A-01',)
```

객체 하단 중심점 `((x1 + x2) / 2, y2)`을 사용하고 경계도 포함합니다.
후보가 여러 개면 불확실한 매핑이며 임의로 하나를 고르면 안 됩니다.
후보가 없다는 결과도 빈자리의 증거가 아닙니다.
이미지 최하단에 닿는 bbox의 `y2=frame_height`는 마지막 픽셀보다 아래이므로 미매핑일 수 있습니다.
좌표를 임의 보정하지 않으며 이런 사례는 3단계 현장 검증에 포함합니다.
실제 프레임 해상도가 저장된 해상도와 다르면 매핑을 거부하며 자동 확대·축소하지 않습니다.

## 구조

- `zeroframe/state.py`: 상태, 관측, 시간 설정, 좌석별 상태 머신.
- `zeroframe/__main__.py`: 가짜 관측을 사용하는 실행 예제.
- `zeroframe/camera.py`: 로컬 카메라 열기·읽기·해제.
- `zeroframe/roi.py`: 좌표 검증, JSON 저장·읽기, bbox 매핑.
- `zeroframe/detection.py`: 로컬 YOLO 어댑터, 클래스별 임계값, 보수적 ROI 매핑·좌석 추적.
- `run_vision.py`: 최신 카메라 프레임 분석, 상태·속도 출력, 선택적 로컬 미리보기.
- `calibrate.py`: 카메라 미리보기와 좌석 영역 지정 도구.
- `requirements.txt`: 카메라 도구의 OpenCV 의존성.
- `requirements-yolo.txt`: Windows용 선택적 YOLO 추가 설치; Pi는 본문의 분리 설치 사용.
- `examples/roi_config.example.json`: 이미지 없는 가짜 좌표 예제.
- `tests/test_*.py`: 상태·좌표·저장·카메라 오류·캘리브레이션 제어 테스트.
- `zeroframe_v1.1_local_multimodal.md`: 전체 시스템 명세.
- `docs/implementation-plan.md`: 단계별 진행 상황과 미검증 사항.

## 판정 규칙

| 조건 | 기본 동작 |
| :--- | :--- |
| 시작 | `UNKNOWN`, 마지막 유효 관측 시각 없음 |
| 사람 검출 | 2초간 지속하면 `OCCUPIED`; 사람과 짐이 함께 있으면 사람 우선 |
| 사람 없이 짐 검출 | 3초간 지속하면 `AWAY`; `OCCUPIED`에서 전환할 때는 4초 |
| 빈자리 후보 | 가림 없는 사람·짐 부재가 5초 이상 지속되고, 현재 관측의 명시적인 빈자리 검증까지 있을 때만 `EMPTY` |
| 관측 간격 2초 초과 | 후보의 연속 관측 시간을 초기화 |
| 판정 불가 | 기존 확정 상태를 잠시 유지하되, 마지막 유효 판정 후 30초가 되면 `UNKNOWN` |

`TransitionPolicy`로 시간을 지정할 수 있으며, 모든 설정값은 유한한 양수여야 합니다.
`max_observation_gap`은 `stale_seconds`보다 작아야 합니다.
빈자리 지속 시간과 관측 간격은 현장 실측 전의 보수적인 개발 기본값입니다.

`Observation.empty_verified`는 동일 관측에 대한 신뢰할 수 있는 검증 결과를
전달받기 위한 입력입니다. **미탐지를 이유로 이 값을 `True`로 설정하면 안 됩니다.**
지금은 테스트에서만 이 신호를 만듭니다. 추후 VLM 연동에서 결과 시각·버전·내용을
검증한 뒤 연결합니다. 최초 빈자리도 같은 검증을 요구합니다.

가림 또는 모순된 검증은 후보 시간을 초기화하고 기존 판정의 최신성을 갱신하지 않습니다.
검증 없는 미탐지 역시 최신성을 갱신하지 않습니다. 따라서 확정했던 `EMPTY`도
재검증 없이 무기한 유지하지 않습니다.

## 코드에서 사용

```python
from time import monotonic

from zeroframe.state import Observation, SeatStateMachine

machine = SeatStateMachine(now=monotonic())
# 실제 새 관측이 도착했을 때만 호출합니다.
state = machine.observe(Observation(observed_at=monotonic(), person=True))
# 카메라 입력이 없어도 주기적으로 호출해야 만료를 반영합니다.
state = machine.tick(monotonic())
```

좌석마다 상태 머신을 하나씩 사용합니다. 모든 시각은 같은 `time.monotonic()` 기준의
초 단위 값입니다. 중복·뒤늦은 관측은 무시하고, `tick()`의 시계 역행은 거부합니다.
한 좌석의 `observe()`와 `tick()`은 같은 실행 흐름에서 순서대로 호출합니다.
내부 `status_since`와 `last_observed_at`은 API의 ISO 8601 문자열이 아닙니다.
네트워크 전송용 시각 처리는 4단계에서 연결합니다.

## 이미지 처리와 현재 검증 한계

캘리브레이션과 YOLO 실행기는 카메라 이미지를 RAM에서만 읽고 선택적으로 로컬 화면에 표시하며,
이미지 파일·영상 파일·네트워크 전송 기능은 사용하지 않습니다.
OS의 스왑·페이지 파일·크래시 덤프 등은 코드 테스트로 검증하지 않았습니다.
운영 전 장비 환경에서 별도로 점검해야 합니다.
.gitignore는 가상환경·캐시, 로컬 인증 정보, 현장 좌표, 이미지·영상·배열 덤프, 모델 가중치·로그를 제외합니다.
제외 규칙은 파일 저장 자체를 방지하지 않으며, 이미 추적 중인 파일에도 소급 적용되지 않습니다.
커밋 전 `git status --short`로 대상을 확인하고, `.env.example`에도 실제 키를 넣지 마세요.
실영상의 false-empty 비율·`UNKNOWN` 비율·FPS·P95 지연·최대 RAM 사용량은 아직 측정하지 않았습니다.
명세의 YOLO 1~3 FPS, 상태 반영 5~15초는 목표이며, 노트북 또는 Pi에서 달성했다는 의미가 아닙니다.

다음 작업은 **실제 좌표 저장 확인 → 노트북 YOLO 실행·현장 보정 → Pi 추론 형식 비교**, 이후 4단계 전송입니다.
