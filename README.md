# ZeroFrame Vision

프라이버시 보호형 좌석 탐지 시스템의 엣지 판정 모듈입니다.
개발은 **Windows 노트북과 내장 카메라**로 진행하며, 우선 운영 대상은 **Raspberry Pi 5(8GB, AI 가속기 없음) + 64비트 Raspberry Pi OS + USB 웹캠**입니다.
Windows와 Linux의 공통 코드를 유지하고, 플랫폼별 카메라 입력과 설치 방법을 구분합니다. 충돌하는 구현 선택은 Linux 운영 환경을 우선합니다.
전체 요구사항은 [v1.1 명세서](zeroframe_v1.1_local_multimodal.md), 세부 검증 기록은 [구현 진행 기록](docs/implementation-plan.md)을 참고하세요.

## 전체 5단계와 현재 진행 상황

| 단계 | 목표와 구현 범위 | 현재 상태 | 완료 확인 기준 |
| :--- | :--- | :--- | :--- |
| **1. 상태 판정 핵심** | 4상태 모델, 설정 검증, 시간 기반 전이, 관측 만료, 가짜 관측 데모 | **완료** | 경계 시각·잘못된 입력·관측 중단 테스트 통과 |
| **2. 카메라·좌석 ROI** | 내장 카메라 미리보기, RAM 화면 고정, 좌석 영역 지정, JSON 저장·검증, bbox 매핑 | **구현·자동 테스트 완료 / 사용자 실행 확인** | 실제 카메라에서 좌석을 지정하고 유효한 좌표 JSON 저장 |
| **3. YOLO 연결** | YOLO11n 탐지 → ROI 매핑 → 좌석별 상태 머신 연결, `main.py` 시작 창, 상반신 매핑 | **기능 구현·자동 테스트 완료 / 현장 검증 중** | GUI 연속 조작, 실제 상반신 탐지·경계·가림 사례, 노트북/Pi 성능 측정 |
| **4. 백엔드 전송** | 상태 JSON, 인증 헤더, 변경 전송, 10초 하트비트, 통신 실패 처리 | **완료** | 인증 JSON·하트비트·실패/복구 검증, 실서버 수신·장치 연결 정상 확인 |
| **5. 선택적 VLM·최종 통합 검증** | 로컬 SmolVLM2 검증, 단일 워커·제한 큐·타임아웃·오래된 결과 폐기, 실제 백엔드 연동·현장 보정 | **진행 중 — 단독 진단·네 상태 비교 도구 구현** | 사람의 현장 정답 검증·상태 연결·최종 통합 대기 |

**4단계 백엔드 전송까지 완료했습니다.** 3단계 현장 정확도·성능 계측은 별도 확인 대상이며, 다음 구현은 5단계 선택적 VLM·최종 통합입니다. 최근 Pi ARM64 / Python 3.13.5에서 전체 자동 테스트 162개 통과. 사용자가 좌석 설정 후 Windows 노트북에서 YOLO 실행 성공을 알려주었습니다.
Pi 5에서는 C270 USB 웹캠의 실제 프레임 60개 수신, 로컬 Tk 시작 창 생성, 검은 합성 프레임의 실제 모델 추론을 확인했습니다. 실제 좌석 추론·인식 정확도·FPS 검증은 남아 있습니다.
**`main.py` 시작 창, 가짜 관측 데모, 좌석 영역 지정 도구, 로컬 YOLO 실행기**가 있습니다.
시작 창은 실제 Tk 생성·위젯 배치와 실행 흐름의 모의 테스트를 확인했으며, 새 GUI와 실제 카메라의 전체 연속 조작은 확인 대기입니다.
2026-11-19 Render 배포 서버에서 실제 인증 POST·10초 하트비트·방 조회 반영·30초 오프라인 전환을 확인했습니다. 이 에이전트 검사는 카메라 없이 `A-01=UNKNOWN`으로 수행했습니다. 이후 사용자가 백엔드에서 JSON 수신과 장치 연결 상태가 정상임을 확인했습니다. [연결 근거](docs/backend-integration.md).
5단계의 **로컬 VLM 단독 진단·현장 비교 도구**를 구현했습니다. 초기 긴 질문은 라벨 형식을 지키지 못했지만, 후속 선택형·256 입력은 실제 C270 A-01 비교에서 형식을 지키며 2.915초를 기록했습니다. 정답 없는 실제 영상이므로 정확도는 미검증입니다. 기본 가시성 보류는 어둡거나 단색인 입력을 UNKNOWN으로만 처리하며, 모델 원본의 검은 화면 오답도 별도로 기록했습니다. `--interactive --compare`로 SSH 터미널에서 네 상태를 직접 준비하고, 같은 프레임의 질문·해상도를 비교해 메타데이터 보고서를 남깁니다. [측정 결과·실행법](docs/vlm-probe.md)

최근 반영한 기능은 다음과 같습니다.

- **단일 실행 창:** `main.py`에서 좌석 설정·재설정과 탐지 시작, 취소·종료 후 시작 창 복귀.
- **상반신 좌석 연결:** 하반신이 잘린 person 상자도 한 좌석과 50% 이상 겹치면 연결. 물품은 하단 중심점 기준 유지.
- **보수적 판정:** 약한·다중 좌석 겹침과 지연된 결과는 확정을 보류. 실제 실행에서는 빈자리 검증기가 없어 `EMPTY`를 생성하지 않음.

상반신 대응은 **탐지된 사람을 좌석에 연결하는 규칙의 개선**입니다. 실제 뒷모습에서 `person` 상자를 생성하는 인식률은 확인해야 합니다.
좌석 일괄 생성·자동 캘리브레이션과 운영용 VLM 검증기는 현재 제공하지 않습니다. `probe_vlm.py`는 상태를 변경하지 않는 단독 진단 도구입니다.

3단계의 다음 검증은 현재 연결된 Pi에서 좌석을 다시 지정하고 NCNN/ONNX 경로를 같은 조건에서 비교하는 것입니다.
5단계의 **VLM은 선택 기능**입니다. 모델·런타임 호환성, 정확도, 자원 사용량을 실측한 뒤 활성화합니다.
VLM을 제외하더라도 최종 통합·장애·품질 검증은 수행해야 합니다.
검증 없는 빈자리를 임의로 확정하지 않으므로 VLM 미사용 시 일부 좌석이 `UNKNOWN`으로 남을 수 있습니다.

이 저장소는 Vision 영역만 담당합니다. 백엔드의 상태 저장·30초 오프라인 판정·WebSocket,
프론트엔드의 좌석 지도·관리자 화면은 별도 저장소의 책임입니다.

## 평소에는 main.py 하나로 실행

최초 설치가 끝났다면 VS Code에서 **이 저장소 폴더를 열고 `main.py` → Ctrl+F5**로 시작합니다.
`.vscode/launch.json`이 Pi/Linux에서는 `.venv/bin/python`, Windows에서는 `.venv/Scripts/python.exe`를 직접 선택합니다.
상단 Python 실행 버튼도 프로젝트 가상환경으로 열린 파일을 실행합니다. [VS Code 실행 설정 안내](https://code.visualstudio.com/docs/python/debugging)
현재 Pi에는 가상환경·apt OpenCV·CPU용 추론 패키지와 `local/yolo11n.pt`를 준비했습니다.
Windows에서 사용하던 `Scripts/python.exe` 경로는 Pi에서 실행할 수 없습니다.

터미널에서 직접 실행할 때는 OS에 맞는 명령을 사용하세요.

```bash
# Raspberry Pi / Linux
.venv/bin/python main.py
```

```powershell
# Windows
.\.venv\Scripts\python.exe main.py
```

파일 더블클릭은 Windows의 Python 연결 설정에 따라 다른 인터프리터를 사용할 수 있습니다.

1. 시작 창에서 **방 ID, 좌석 ID, 카메라 번호, 모델 경로**를 확인합니다. 저장된 `roi_config.json`이 있으면 방·좌석을 자동으로 읽습니다.
2. **탐지 시작**: 좌표가 없거나 방·좌석 목록이 바뀌었으면 캘리브레이션부터 진행합니다.
   `Space → 꼭짓점 클릭 → N → 모든 좌석 확정 후 S`로 저장하면 이어서 탐지 화면을 엽니다.
3. 좌표가 이미 맞으면 바로 탐지합니다. 화면에 좌석별 상태·탐지 상자·FPS·추론 시간을 표시합니다.
4. **좌석 설정 / 다시 설정**: 캘리브레이션만 수행합니다. 기존 파일 교체는 확인을 받고, 실제 `S` 저장 시 반영합니다.
5. 카메라 창에서 **Q / Esc / 창 닫기**로 시작 창에 돌아옵니다. 설정 중 취소하면 탐지를 시작하지 않습니다.
   오류는 시작 창의 대화상자로 안내합니다.

카메라 작업 중에는 시작 창을 숨기고 기존 OpenCV 화면을 사용합니다. 모델 로딩 중에는 화면이 열리기까지 잠시 기다려 주세요.
기본 실행은 로컬 데스크톱용입니다. 개발용 SSH 화면 공유는 아래의 명시적 예외를 따르며 이미지 저장·녹화는 허용하지 않습니다. 모델 자동 다운로드·자동 설치 기능은 없습니다.
최초 설치는 아래 절차가 필요하고, 기본 파일은 저장소의 `roi_config.json`, `local/yolo11n.pt`입니다.
NCNN 모델은 폴더 경로를 입력할 수 있습니다. 다른 이름의 좌표를 사용하는 고급 실행은 기존 CLI를 사용하세요.

Linux 시작 창에는 로컬 데스크톱과 Tkinter가 필요합니다.

```bash
sudo apt install -y python3-tk
.venv/bin/python main.py
```

Tkinter는 [Python 기본 GUI 인터페이스](https://docs.python.org/3/library/tkinter.html)이며 Windows Python에서는 Tcl/Tk 구성 요소가 설치되어 있어야 합니다.
Remote-SSH에서 `DISPLAY`가 없고 같은 사용자의 로컬 X 화면이 하나 있으면 `main.py`가 자동 선택합니다.
창은 Pi의 데스크톱에 열립니다. Pi 데스크톱에 먼저 로그인하세요. 기존 `DISPLAY` 설정은 유지합니다.
로컬 화면이 없거나 후보가 여러 개라면 자동 선택하지 않습니다. 화면 없는 Pi/SSH 운영은 기존 `run_vision.py`를 `--preview` 없이 실행합니다.

### 개발용: 노트북 SSH 접속에서 화면 조작

**2026-11-17 사용자 승인:** 개발·캘리브레이션 중에는 신뢰하는 노트북으로 SSH 암호화 터널을 통해 화면을 전송할 수 있습니다.
운영·시연 배포의 비영상 메타데이터 전송 원칙과 이미지 저장·녹화·외부 이미지 API 금지는 유지합니다.

1. 노트북 VS Code에서 Remote-SSH로 Pi에 연결하고 이 저장소를 엽니다.
2. `main.py`에서 **Ctrl+F5** 또는 일반 **Python 파일 실행** 버튼을 누릅니다. Linux에서는 SSH 접속을 직접 감지해 화면 공유도 함께 시작하므로 실행 설정의 환경 변수가 없어도 동작합니다.
3. **포트(Ports)** 탭에 `6080 / ZeroFrame 개발 화면`이 표시됩니다. 자동으로 열린 노트북 브라우저에서 시작 창과 카메라 창을 조작하세요.
4. 자동 연결이 안 되면 포트 탭에서 `6080`을 전달하고 **브라우저에서 열기**를 누릅니다. 노트북 쪽 포트가 바뀌었다면 포트 탭의 전달 주소를 사용합니다.
5. 시작 창의 **종료** 또는 Ctrl+C로 끝내면 화면 공유 서버도 종료됩니다. 브라우저 탭만 닫으면 앱은 계속 실행됩니다.

최초 패키지 준비와 VS Code 밖에서의 실행은 다음과 같습니다. 현재 Pi에는 패키지를 설치했습니다.

```bash
sudo apt install -y wayvnc novnc websockify util-linux
.venv/bin/python main.py --dev-remote
```

SSH에서도 로컬 창만 사용하려면 `.venv/bin/python main.py --local`로 실행합니다.
페이지가 계속 로딩되면 Pi 터미널에서 `curl -fsS http://127.0.0.1:6080/`을 확인하세요. 연결 거부는 앱 서버가 켜지지 않은 상태입니다.
앱을 다시 실행하고 포트 탭의 전달 주소를 새로 열어야 합니다. `main.py`를 닫은 동안에는 해당 주소도 응답하지 않습니다.

VS Code 없이 접속한다면 **노트북 터미널**에서 터널을 열고 노트북 브라우저로 `http://127.0.0.1:6080/`에 접속합니다.

```bash
ssh -N -L 127.0.0.1:6080:127.0.0.1:6080 akgusgh1234@192.168.0.14
```

`zeroframe/dev_remote.py`가 기존 Wayland 데스크톱과 마우스·키보드를 공유합니다. Pi가 부팅된 뒤 데스크톱에 로그인돼 있어야 하며 Lite OS의 가상 화면 생성은 제공하지 않습니다.
WayVNC는 사용자 전용 임시 디렉터리의 Unix 소켓, 브라우저 서버는 `127.0.0.1:6080`에서만 대기합니다. 영구 서비스나 공개 포트를 만들지 않습니다.
임시 파일은 웹 클라이언트 경로·HTML뿐이며 카메라 이미지를 저장하지 않습니다. 노트북에서의 실제 브라우저 조작과 장비별 스왑·캐시 동작은 별도로 확인해야 합니다.

## 최초 설치 및 CLI 실행

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
Pi의 데스크톱 세션에서 로컬 마우스 또는 위의 승인된 개발용 원격 화면으로 캘리브레이션합니다.
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
운영에서는 캘리브레이션 화면을 외부 전송하지 않습니다. 개발 중에는 위의 승인된 SSH 터널 화면 공유를 사용할 수 있습니다.

### 지원 범위와 검증 상태

| 항목 | Windows 노트북 | Raspberry Pi OS Linux |
| :--- | :--- | :--- |
| 상태 머신·ROI·JSON | 공통 Python 코드 | 동일 코드 |
| 내장/USB 웹캠 | DirectShow 입력 | V4L2 USB 웹캠 입력 |
| 클릭 캘리브레이션 | 로컬 OpenCV 창 | 로컬 데스크톱 OpenCV 창 필요 |
| CSI 리본 케이블 카메라 | 대상 아님 | 현재 미지원; Picamera2 캡처 경로 별도 구현 필요 |
| 실물 확인 | 사용자 카메라·YOLO 실행 성공 보고 | Pi 5 C270 프레임 수신·Tk 시작 창 확인; 실제 좌석 추론 미검증 |

USB 웹캠과 CSI 카메라의 소프트웨어 경로를 혼동하지 마세요.
CSI 모듈은 libcamera 기반 Picamera2 경로를 확인해야 하며 현재 `--camera` 번호만 바꿔서 지원된다고 보장하지 않습니다.
[Raspberry Pi Picamera2 안내](https://www.raspberrypi.com/documentation/accessories/camera-software.html#use-libcamera-from-python-with-picamera2)

Windows/Linux 백엔드 선택과 화면 없는 Linux 오류 처리는 모의 테스트를 통과했습니다.
162개 테스트는 Pi ARM64에서도 실행했지만 카메라·탐지 입력은 모의 객체입니다. 전송 테스트 일부는 실제 loopback HTTP를 사용합니다. C270의 V4L2 프레임 수신과 Tk 시작 창은 별도 실제 장비 검사로 확인했습니다.
Windows 노트북 YOLO는 사용자 실행 성공 보고가 있습니다. Pi의 YOLO 현장 성능과 실제 강의실 정확도는 미검증입니다. VLM은 단독 진단만 구현했고 운영 상태 판정에는 연결하지 않았습니다.

### 목적별 실행 명령 (Windows)

| 목적 | 명령 |
| :--- | :--- |
| GUI 시작 창 | `.\.venv\Scripts\python.exe main.py` |
| 상태 판정 데모 | `.\.venv\Scripts\python.exe -m zeroframe` |
| 전체 자동 테스트 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| 좌석 하나 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01` |
| 좌석 두 개 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 A-02` |
| 기존 좌표 다시 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 --overwrite` |
| 도구 도움말 | `.\.venv\Scripts\python.exe calibrate.py --help` |
| YOLO 실시간 확인 | `.\.venv\Scripts\python.exe run_vision.py --config roi_config.json --model local/yolo11n.pt --preview` |

`--overwrite`는 새로 지정한 좌석 목록으로 **파일 전체를 교체**합니다. 위 예시는 좌석 하나만 저장하므로,
두 좌석을 유지하려면 `--seats A-01 A-02`를 지정하세요.
YOLO는 아래 3단계의 추가 설치·모델 준비가 필요합니다. 서버 전송은 아래 4단계, VLM 단독 진단 명령은 [5단계 진단 안내](docs/vlm-probe.md)를 참고하세요.
Linux에서는 위 표의 `.\.venv\Scripts\python.exe`를 `.venv/bin/python`으로 바꾸고 Bash에서 실행합니다.

## 1단계: 상태 판정 데모와 테스트

`python -m zeroframe`는 실제 시간이 흐르기를 기다리지 않고 가짜 관측으로
`UNKNOWN → OCCUPIED → AWAY → EMPTY → UNKNOWN`을 출력합니다.
**카메라·YOLO·VLM·네트워크는 사용하지 않습니다.**

데모의 `3s`는 사람 탐지가 2초간 지속된 시점, `8s`는 짐만 보이는 상태가 4초간 지속된 시점입니다.
`14s`에는 테스트용 빈자리 검증 신호를 넣고, `44s`에는 마지막 유효 판정 후 30초 만료를 확인합니다.

현재 자동 테스트는 기존 상태·ROI·캘리브레이션 55개, 최신 프레임·YOLO·매핑·실행기 22개,
GUI 실행 흐름·로컬 화면 선택 19개, 상반신 ROI 겹침 매핑 9개, 개발용 원격 화면 7개,
전송 설정·보고·재시도·실제 HTTP 21개, 실행기 전송 연결 3개, VLM 진단·평가 26개로 총 162개입니다.
카메라와 화면 이벤트는 가짜 입력으로 검증하므로 테스트 통과가 실제 영상의 인식 정확도를 뜻하지 않습니다.
테스트 출력 마지막의 `Ran 162 tests`와 `OK`를 확인하세요.
가상환경이 없어도 `python -m zeroframe`, `python -m unittest discover -s tests -v`는 실행할 수 있습니다.

## 2단계: 노트북 카메라로 좌석 지정

처음에는 좌석 하나로 시험하는 편이 간단합니다.

```powershell
.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01
```

1. 카메라 미리보기에서 촬영 각도를 맞추고 **Space**를 눌러 화면을 고정합니다.
2. `A-01` 좌석의 **책상·의자와 앉은 사람의 머리·어깨·등이 화면에 보일 공간**을 포함하는 영역의 꼭짓점을 순서대로 클릭합니다.
   사각형이라면 **좌상 → 우상 → 우하 → 좌하** 순서입니다. 최소 3개가 필요하며 첫 꼭짓점을 다시 찍지 않습니다.
   책상 면이나 벽면만 지정하지 마세요. 뒷모습·상반신만 보이는 구도도 사용할 수 있지만, 보이는 사람 상자의 절반 이상이 좌석 안에 들어와야 합니다.
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
.venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
.venv/bin/python -m pip install polars ultralytics-thop
.venv/bin/python -m pip install --no-deps ultralytics==8.3.253
mkdir -p local
curl --fail --location https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt --output local/yolo11n.pt
.venv/bin/python -c "import cv2, torch, torchvision, ultralytics; print(cv2.__version__, torch.__version__, ultralytics.__version__)"
.venv/bin/python run_vision.py --config roi_config.json --model local/yolo11n.pt
```

위 의존성은 고정 버전의 [Ultralytics 패키지 정의](https://github.com/ultralytics/ultralytics/blob/v8.3.253/pyproject.toml)에 맞춥니다.
현재 Pi의 apt OpenCV는 4.10.0이며 PyTorch는 CPU 전용 배포처에서 준비합니다. import가 실패하면 실행 전에 의존성을 확인하세요.
`pip check`는 apt의 `python3-opencv`를 `opencv-python`으로 인식하지 못할 수 있습니다.

실행기 기본값은 화면 없이 상태만 출력하므로 SSH에서도 사용할 수 있습니다.
Pi의 로컬 데스크톱에서 확인할 때 `--preview`를 추가하세요. 개발 중 노트북 조작은 위의 SSH 터널 예외를 따릅니다.
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
- 사람은 `0.50`, backpack/handbag/laptop/book은 `0.45` 이상을 사용합니다. 사람은 **보이는 탐지 상자 면적의 50% 이상이
  한 좌석 ROI에 들어오고 다른 좌석과 면적 겹침이 없을 때** 연결합니다. 상반신만 보이거나 화면 끝에서 잘려도 하단 중심점을 요구하지 않습니다.
  물품은 기존 하단 중심점 방식입니다. 낮은 신뢰도(검출 하한 `0.25`), 사람의 다중 좌석 겹침·부족한 겹침,
  물품의 다중 후보는 관련 좌석의 확정을 보류합니다. 범위 밖 탐지는 좌석에 할당하지 않습니다.
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

## 4단계: 백엔드 상태 전송

현재 장비는 `https://zeroframe-backend-p1wa.onrender.com`에 연결하도록 설정했습니다.
현재 연동 계약은 [docs/INTERFACES.md](https://github.com/KNUCreaterCrew/docs/blob/main/INTERFACES.md)를 따릅니다. 요청 경로·인증 헤더·필드는 기존 전송부와 호환됩니다.
인증키는 Git 제외된 `transport_config.json`(권한 0600)에 저장되어 있으며, 기존 `main.py`에서 **탐지 시작**을 누르면 자동 전송합니다. 실제 키를 문서·이슈·커밋에 넣지 마세요.
배포 방 `IT4-101`에는 `A-01`, `A-02`, `B-01`, `B-02`가 있지만 현재 ROI는 `A-01`만 포함하므로 그 좌석만 보고합니다.

기존 계약인 `POST /api/v1/rooms/{room_id}/seats/status`와 `X-Edge-Api-Key` 헤더를 사용합니다.
시작 시 전체 좌석을 보고하고, 상태·UNKNOWN 사유가 바뀌면 전송하며, 변화가 없어도 10초마다 전체 스냅샷을 보냅니다.
카메라·YOLO·좌표 처리는 분석 스레드에, HTTP는 별도 단일 작업자에 둡니다. 실패한 보고를 쌓지 않고 최신 메타데이터 하나로 교체하며 1→2→4→8→10초 간격으로 재시도합니다.

### 서버 없이 JSON 전송 확인

Pi의 VS Code 터미널 두 개에서 각각 실행하세요. 추가 pip 패키지는 필요하지 않습니다.

```bash
# 터미널 1: loopback 개발 수신기
.venv/bin/python mock_backend.py
```

```bash
# 터미널 2: 카메라·모델을 사용하지 않는 합성 메타데이터 전송
.venv/bin/python send_status_demo.py
```

수신기 터미널에 `UNKNOWN → OCCUPIED → AWAY → UNKNOWN` JSON 네 건이 표시됩니다.
이는 실제 탐지·시간 기반 전이 시험이 아닌 전송 예시이며 `inference_fps`는 0입니다. 데모는 loopback으로만 보냅니다.
수신기는 `127.0.0.1:8765`에만 열리고 메타데이터를 터미널에 출력합니다. 상태 저장·30초 오프라인 감시·WebSocket을 제공하는 실제 백엔드는 아닙니다.

### main.py에서도 전송 활성화

아직 전송 설정이 없는 새 장비에서 로컬 수신기를 연결할 때만 아래 예제를 복사합니다. 현재 Pi의 실서버 설정은 유지하세요.
개발 수신기를 실행한 상태에서 설정 예제를 복사한 뒤 기존처럼 `main.py`를 실행하고 **탐지 시작**을 누릅니다.

```bash
cp -n examples/transport_config.example.json transport_config.json
.venv/bin/python main.py
```

Windows는 `Copy-Item examples/transport_config.example.json transport_config.json`으로 복사하고 Windows `.venv` Python을 사용합니다.
이미 설정 파일이 있다면 덮어쓰지 말고 내용을 확인하세요. `transport_config.json`은 Git에서 제외합니다.

```json
{
  "backend_url": "http://127.0.0.1:8765",
  "api_key": "local-development-only",
  "timeout_seconds": 2
}
```

위 키는 로컬 테스트용 공개 문자열입니다. 실서버 설정에는 HTTPS 기본 주소와 발급받은 비밀키를 사용합니다. 현재 Pi에는 Render 주소·실제 키·5초 타임아웃을 저장했습니다.
`ZEROFRAME_BACKEND_URL`과 `EDGE_NODE_SECRET_KEY` 환경 변수는 파일 값보다 우선합니다. 파일 자동 로드는 일반 Python 실행 버튼과 Ctrl+F5에서 모두 동작합니다.
설정 파일·환경 변수가 없으면 전송은 꺼지며 기존 로컬 분석을 사용합니다. 설정을 바꾸면 탐지를 종료한 뒤 다시 시작하세요.
CLI는 `run_vision.py --transport-config local/backend.json`으로 별도 파일을 지정할 수 있습니다.
HTTP는 loopback 개발 수신기에만 허용하며, HTTPS 인증서 검증을 사용하고 리다이렉트나 프록시 환경 변수로 인증 헤더를 전달하지 않습니다.

JSON의 시각은 내부 monotonic 시각을 시작 시 UTC 기준점으로 변환합니다. `timestamp`는 보고 생성 시각, `last_observed_at`은 실제 마지막 유효 관측 시각입니다.
하트비트만으로 관측 시각을 갱신하지 않으며 분석 업데이트가 멈춰도 30초가 지난 점유 상태는 전송 시 `UNKNOWN`으로 만료합니다.
`inference_fps`는 실행 시작 후 누적 분석 횟수/경과 시간, `confidence`는 검증된 좌석 확률이 없어 항상 `null`입니다.
현재 확정 점유·짐은 `YOLO`, UNKNOWN은 `FALLBACK`으로 보고합니다. 실제 실행기의 `EMPTY` 검증은 여전히 없습니다.
`saved_frames_count: 0`은 이미지 저장이 없다는 구현 지표이며 스왑·드라이버·네트워크 점검을 대신하지 않습니다.
키·응답 본문은 오류 로그에 출력하지 않습니다. 앱 종료 시 작업자를 중지하고 전송 버퍼를 버리며, 종료 후의 엣지 오프라인 판정은 백엔드의 책임입니다.

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
| `person` 상자는 있는데 좌석이 `UNKNOWN` | 사람 confidence 0.50 이상인지, 보이는 상자 면적의 50% 이상이 한 ROI에 들어오는지 확인합니다. 다른 좌석과도 면적이 겹치면 확정을 보류하므로 좌석 영역을 조정하세요. |
| 뒷모습·상반신에 `person` 상자 자체가 없음 | ROI 연결 이전의 YOLO 탐지 문제입니다. 이번 겹침 수정은 모델 재학습이나 미탐지 해결을 보장하지 않습니다. 실제 구도에서 크기·조명·가림과 탐지 confidence를 확인해야 합니다. |
| `Camera driver did not stop` | 카메라 드라이버 읽기가 종료되지 않았습니다. 프로세스를 완전히 종료한 뒤 장치를 다시 연결합니다. |
| `main.py`에서 `Tkinter가 필요합니다` | Windows는 Python의 Tcl/Tk 구성 요소, Pi는 apt `python3-tk`를 설치합니다. 가상환경에 Tkinter를 pip로 설치하지 않습니다. |
| 시작 창에서 모델이 없다고 나옴 | 이미 받은 `local/yolo11n.pt` 경로를 확인하거나 찾아보기로 선택합니다. 모델 없이도 '좌석 설정 / 다시 설정'은 사용할 수 있습니다. |

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

위 `matching_seat_ids()` 예제는 **물품**의 하단 중심점 `((x1 + x2) / 2, y2)`을 사용하고 경계도 포함합니다.
후보가 여러 개면 불확실한 매핑이며 임의로 하나를 고르면 안 됩니다.
후보가 없다는 결과도 빈자리의 증거가 아닙니다.
물품 상자가 이미지 최하단에 닿는 `y2=frame_height`는 마지막 픽셀보다 아래이므로 미매핑일 수 있습니다.
사람은 `overlapping_seat_ratios()`로 **실제 ROI 다각형과 보이는 상자 교차 면적 / 상자 면적**을 계산합니다.
오목한 다각형도 지원하며 ROI의 외접 사각형으로 대신 계산하지 않습니다. 경계에 닿기만 한 경우는 면적 겹침이 아닙니다.
한 좌석에만 면적이 겹치고 비율이 0.50 이상이면 사람 신호를 만들며, 그 외 관련 좌석은 보류합니다.
따라서 발이나 몸통 아래쪽이 안 보여도 매핑할 수 있습니다. 반면 인접 좌석과 조금이라도 면적이 겹치면 보류하는
보수적인 초기 정책이므로, 실제 강의실의 사람 크기·좌석 간 가림·ROI 배치를 보며 보정해야 합니다.
실제 프레임 해상도가 저장된 해상도와 다르면 매핑을 거부하며 자동 확대·축소하지 않습니다.

## 구조

- `main.py`: 방·좌석·카메라 입력, 캘리브레이션·탐지 실행과 복귀를 묶은 로컬 시작 창.
- `zeroframe/dev_remote.py`: 개발 전용 SSH 화면 공유 서버의 시작·종료와 loopback 제한.
- `zeroframe/transport.py`: 메타데이터 JSON·UTC 시각 변환·인증 POST·최신 스냅샷 작업자와 재시도.
- `mock_backend.py`, `send_status_demo.py`: loopback 테스트 수신기와 카메라 없는 합성 전송 예시.
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
4단계 전송 경계에서 UTC ISO 8601 시각으로 변환하며 내부 상태의 monotonic 시각은 유지합니다.

## 이미지 처리와 현재 검증 한계

캘리브레이션과 YOLO 실행기는 카메라 이미지를 RAM에서만 읽고 선택적으로 로컬 화면에 표시하며,
이미지·영상 파일을 저장하지 않습니다. 승인된 개발 모드에서는 별도 원격 데스크톱이 이 화면을 SSH 터널로 공유합니다.
운영·시연 배포에서는 개발 모드를 끄고 비영상 메타데이터만 전송합니다.
OS의 스왑·페이지 파일·크래시 덤프 등은 코드 테스트로 검증하지 않았습니다.
운영 전 장비 환경에서 별도로 점검해야 합니다.
.gitignore는 가상환경·캐시, 로컬 인증 정보, 현장 좌표, 이미지·영상·배열 덤프, 모델 가중치·로그를 제외합니다.
제외 규칙은 파일 저장 자체를 방지하지 않으며, 이미 추적 중인 파일에도 소급 적용되지 않습니다.
커밋 전 `git status --short`로 대상을 확인하고, `.env.example`에도 실제 키를 넣지 마세요.
실영상의 false-empty 비율·`UNKNOWN` 비율·FPS·P95 지연·최대 RAM 사용량은 아직 측정하지 않았습니다.
명세의 YOLO 1~3 FPS, 상태 반영 5~15초는 목표이며, 노트북 또는 Pi에서 달성했다는 의미가 아닙니다.

## 다음 작업과 확인 기준

| 우선 작업 | 확인할 결과 |
| :--- | :--- |
| GUI 실제 조작 | 기존 좌표로 시작, 좌석 재설정·취소, 결과 창 종료 후 시작 창 복귀 |
| 상반신·짐 현장 보정 | person 상자 생성·confidence, 한 좌석과의 겹침, 인접 좌석·통로 보행자·가림·짐만 남은 사례 |
| 성능·Linux 검증 | 노트북 실제 FPS·반영 지연·UNKNOWN 비율, Pi 실영상 추론과 NCNN/ONNX·CPU/RAM 비교 |
| 5단계 선택적 VLM·통합 | EMPTY 검증 경로와 로컬 VLM 도입 여부를 실측하고 실제 판정·웹 표시·장애·성능·프라이버시를 최종 검증 |

각 검증의 근거와 테스트 파일별 162개 내역은 [구현 진행 기록](docs/implementation-plan.md)에 정리했습니다.
