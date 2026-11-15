# ZeroFrame Vision

프라이버시 보호형 좌석 탐지 시스템의 엣지 판정 모듈입니다.
개발은 **Windows 노트북과 내장 카메라**로 진행하며, 최종 운영 대상은 Raspberry Pi 5(8GB, AI 가속기 없음)입니다.
전체 요구사항은 [v1.1 명세서](zeroframe_v1.1_local_multimodal.md), 세부 검증 기록은 [구현 진행 기록](docs/implementation-plan.md)을 참고하세요.

## 전체 5단계와 현재 진행 상황

| 단계 | 목표와 구현 범위 | 현재 상태 | 완료 확인 기준 |
| :--- | :--- | :--- | :--- |
| **1. 상태 판정 핵심** | 4상태 모델, 설정 검증, 시간 기반 전이, 관측 만료, 가짜 관측 데모 | **완료** | 경계 시각·잘못된 입력·관측 중단 테스트 통과 |
| **2. 카메라·좌석 ROI** | 내장 카메라 미리보기, RAM 화면 고정, 좌석 영역 지정, JSON 저장·검증, bbox 매핑 | **구현·자동 테스트 완료 / 사용자 저장 확인 대기** | 실제 카메라에서 좌석을 지정하고 유효한 좌표 JSON 저장 |
| **3. YOLO 연결** | YOLO11n 탐지 → ROI 매핑 → 좌석별 상태 머신 연결 | **미구현** | 실제 사람·짐 탐지, 경계·가림 사례 확인, 노트북 성능 측정 |
| **4. 백엔드 전송** | 상태 JSON, 인증 헤더, 변경 전송, 10초 하트비트, 통신 실패 처리 | **미구현** | 테스트 수신 서버에서 계약 검증, 통신 지연·단절에도 분석 지속 |
| **5. 선택적 VLM·최종 통합 검증** | 로컬 SmolVLM2 검증, 단일 워커·제한 큐·타임아웃·오래된 결과 폐기, 실제 백엔드 연동·현장 보정 | **미구현** | VLM 실패 시 안전한 대체 동작, 정확도·지연·RAM·프라이버시 점검 |

**현재 확인된 내용:** 자동 테스트 52개가 통과했고, 사용자의 실행 로그에서 카메라 캘리브레이션 화면 진입을 확인했습니다.
좌표 JSON 저장 성공과 수정한 화면 안내의 실제 조작 결과는 아직 확인하지 않았습니다.
지금 실행할 수 있는 것은 **가짜 관측 데모와 좌석 영역 지정 도구**입니다.
실시간 사람·짐 자동 인식, 백엔드 전송, VLM은 아직 연결되지 않았습니다.

3단계는 먼저 노트북에서 동작을 확인하고, Pi 확보 후 NCNN/ONNX 경로를 같은 조건에서 비교합니다.
5단계의 **VLM은 선택 기능**입니다. 모델·런타임 호환성, 정확도, 자원 사용량을 실측한 뒤 활성화합니다.
VLM을 제외하더라도 최종 통합·장애·품질 검증은 수행해야 합니다.
검증 없는 빈자리를 임의로 확정하지 않으므로 VLM 미사용 시 일부 좌석이 `UNKNOWN`으로 남을 수 있습니다.

이 저장소는 Vision 영역만 담당합니다. 백엔드의 상태 저장·30초 오프라인 판정·WebSocket,
프론트엔드의 좌석 지도·관리자 화면은 별도 저장소의 책임입니다.

## 설치 및 빠른 시작

모든 명령은 **저장소 루트의 Windows PowerShell**에서 실행합니다.
코드는 Python 3.10 이상을 대상으로 하며, 카메라 실행 안내는 Windows용 CPython 3.13(x64) 기준입니다.
상태 데모와 자동 테스트는 표준 라이브러리만 사용하고, 카메라 도구에는 `requirements.txt`의 OpenCV가 필요합니다.

### 처음 설치할 때

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -c "import cv2; print(cv2.__version__)"
```

이미 가상환경과 OpenCV를 설치했다면 아래 실행 명령부터 사용하면 됩니다.
가상환경 활성화 없이 실행하므로 PowerShell 실행 정책을 변경할 필요가 없습니다.
설치 방식은 [Python Windows 공식 안내](https://docs.python.org/3.13/using/windows.html)를 참고하세요.

화면 창을 사용하는 도구이므로 `opencv-python==4.13.0.92`를 사용합니다.
`headless` 또는 다른 OpenCV 배포판을 같은 환경에 함께 설치하지 마세요.
[OpenCV 패키지 공식 안내](https://pypi.org/project/opencv-python/4.13.0.92/)

### 목적별 실행 명령

| 목적 | 명령 |
| :--- | :--- |
| 상태 판정 데모 | `.\.venv\Scripts\python.exe -m zeroframe` |
| 전체 자동 테스트 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| 좌석 하나 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01` |
| 좌석 두 개 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 A-02` |
| 기존 좌표 다시 지정 | `.\.venv\Scripts\python.exe calibrate.py --room IT4-101 --seats A-01 --overwrite` |
| 도구 도움말 | `.\.venv\Scripts\python.exe calibrate.py --help` |

`--overwrite`는 새로 지정한 좌석 목록으로 **파일 전체를 교체**합니다. 위 예시는 좌석 하나만 저장하므로,
두 좌석을 유지하려면 `--seats A-01 A-02`를 지정하세요.
YOLO·서버 전송·VLM 실행 명령은 구현 후 추가합니다.

## 1단계: 상태 판정 데모와 테스트

`python -m zeroframe`는 실제 시간이 흐르기를 기다리지 않고 가짜 관측으로
`UNKNOWN → OCCUPIED → AWAY → EMPTY → UNKNOWN`을 출력합니다.
**카메라·YOLO·VLM·네트워크는 사용하지 않습니다.**

데모의 `3s`는 사람 탐지가 2초간 지속된 시점, `8s`는 짐만 보이는 상태가 4초간 지속된 시점입니다.
`14s`에는 테스트용 빈자리 검증 신호를 넣고, `44s`에는 마지막 유효 판정 후 30초 만료를 확인합니다.

현재 자동 테스트는 상태 머신 25개, ROI·카메라·도구 제어 27개로 총 52개입니다.
카메라와 화면 이벤트는 가짜 입력으로 검증하므로 테스트 통과가 실제 영상의 인식 정확도를 뜻하지 않습니다.
테스트 출력 마지막의 `Ran 52 tests`와 `OK`를 확인하세요.
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

## 자주 발생하는 문제

| 증상 | 확인할 내용과 해결 방법 |
| :--- | :--- |
| `polygon needs at least three vertices` | 현재 좌석에 점이 3개 미만입니다. `Points`를 확인하고 꼭짓점을 클릭한 뒤 `N`을 누르세요. |
| 저장 시 `seats confirmed` 또는 `Confirm every requested seat` | 모든 좌석을 확정하지 않았습니다. 좌석별로 `클릭 → N`을 반복하고 마지막에 `S`를 누르세요. |
| 점이 연결되지만 확정되지 않음 | `N`을 누르세요. 교차하거나 첫 점을 중복 클릭했다면 `U`로 수정하세요. |
| 키가 먹히지 않음 | 터미널 대신 카메라 창을 클릭해 포커스를 둡니다. 수정한 코드는 프로그램을 종료하고 다시 실행해야 적용됩니다. |
| `already exists` | 기존 파일이 있습니다. 다른 `--output`을 쓰거나 전체 교체 의도로 `--overwrite`를 지정하세요. |
| `Cannot open camera` / `Camera frame unavailable` | Windows 카메라 권한, 다른 앱의 점유, 카메라 번호를 확인합니다. 다른 장치는 `--camera 1`로 선택할 수 있습니다. |
| `OpenCV is unavailable` / `No module named cv2` | 패키지를 설치한 가상환경의 Python으로 실행하는지 확인합니다. 위 설치 명령을 사용하세요. |
| `externally-managed-environment` | MSYS2 Python의 시스템 환경에 설치하려고 한 경우입니다. Windows CPython으로 만든 `.venv`를 사용하세요. |
| `py -3.13` 실행 실패 / `did not find executable` | Windows Python 설치·등록 또는 가상환경의 원본 Python 경로를 확인합니다. 원본 Python을 옮기거나 제거했다면 가상환경도 다시 준비해야 합니다. |
| `frame resolution changed` | 저장한 좌표와 프레임 크기가 다릅니다. 같은 해상도를 사용하거나 다시 캘리브레이션하세요. |

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
- `calibrate.py`: 카메라 미리보기와 좌석 영역 지정 도구.
- `requirements.txt`: 카메라 도구의 OpenCV 의존성.
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

캘리브레이션 코드는 카메라 이미지를 RAM에서만 읽고 화면에 표시하며,
이미지 파일·영상 파일·네트워크 전송 기능은 사용하지 않습니다.
OS의 스왑·페이지 파일·크래시 덤프 등은 코드 테스트로 검증하지 않았습니다.
운영 전 장비 환경에서 별도로 점검해야 합니다.
.gitignore는 가상환경·캐시, 로컬 인증 정보, 현장 좌표, 이미지·영상·배열 덤프, 모델 가중치·로그를 제외합니다.
제외 규칙은 파일 저장 자체를 방지하지 않으며, 이미 추적 중인 파일에도 소급 적용되지 않습니다.
커밋 전 `git status --short`로 대상을 확인하고, `.env.example`에도 실제 키를 넣지 마세요.
실영상의 false-empty 비율·`UNKNOWN` 비율·FPS·P95 지연·최대 RAM 사용량은 아직 측정하지 않았습니다.
명세의 YOLO 1~3 FPS, 상태 반영 5~15초는 목표이며, 노트북 또는 Pi에서 달성했다는 의미가 아닙니다.

다음 작업은 **실제 좌표 저장 확인 → 3단계 YOLO 연결**입니다.
