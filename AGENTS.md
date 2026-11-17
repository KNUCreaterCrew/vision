# Repository Guidelines

## Project Structure & Module Organization

Specification: `zeroframe_v1.1_local_multimodal.md`. State: `zeroframe/state.py`; camera/coordinates: `zeroframe/camera.py`, `zeroframe/roi.py`; YOLO/mapping: `zeroframe/detection.py`; transport: `zeroframe/transport.py`; editor: `calibrate.py`; runtime: `run_vision.py`; desktop launcher: `main.py`; tests: `tests/test_*.py`. See `README.md` and `docs/implementation-plan.md`. Pi live inference and field-quality verification remain pending.

- `zeroframe-vision`: Python, YOLO11n, ROI, state transitions, optional local SmolVLM2-256M.
- `zeroframe-backend`: authenticated ingestion, freshness, WebSocket; framework undecided.
- `zeroframe-frontend`: React/Next.js maps, four-state UI, `/admin` metrics.
- `zeroframe-docs`: architecture, contracts, layouts, evaluations, demonstrations.

Update contracts in `zeroframe-docs` before changing APIs, events, or fields across implementations.

## Current Implementation Status

Stages 1–2 are implemented; stage 3 includes YOLO, the desktop launcher, and upper-body ROI mapping, with field validation pending. Windows YOLO success is user-reported; the latest 136 automated tests pass on Pi ARM64 / Python 3.13.5 with simulated model/camera inputs and some real loopback HTTP checks. Pi 5 C270 V4L2 capture of 60 frames, the real Tk launcher, and local model inference on a synthetic black frame are verified separately; live seat inference remains pending. Development desktop HTTP/WebSocket/RFB connection and remote mouse/keyboard delivery to a test Tk window are verified locally. Stage 4 transport is implemented and locally verified, including a four-report synthetic HTTP demo; an actual backend is not available yet. Stage 5 VLM/integration remains unimplemented. Keep README, this guide, the specification, and `docs/implementation-plan.md` consistent; distinguish working code, user reports, simulations, and hardware evidence.

## Architecture & State Rules

Support Windows development and prioritize Raspberry Pi OS Linux deployment (Pi 5, 8GB). USB cameras use DirectShow/V4L2 respectively; CSI requires separate Picamera2 work. Calibration needs a local desktop. Validate Pi hardware and models separately; keep YOLO independent and VLM concurrency at one. Reject stale results.

Person mapping uses polygon intersection covering at least 50% of the visible bbox in exactly one seat; positive-area overlap with multiple seats is ambiguous. Calibrate the seated head/shoulders/back region as well as the desk. Belongings retain bottom-center mapping. Never require visible feet or infer model recall from mapping tests.

Use time-based debouncing. Start `UNKNOWN`; missing detections never establish `EMPTY`. Require sustained absence and current verification, including initial empty seats. Never generate `empty_verified` from missing detections. Expire unverified states after 30 seconds. Internal timestamps use monotonic seconds, not API timestamps. Transport uses 10-second heartbeats; backend offline handling at 30 seconds remains a backend requirement. FPS/latency remain targets.

## Build, Test, and Development Commands

Python 3.10+. Run from the repository root:

- `python -m zeroframe`: synthetic demo; no camera/model/network.
- `python main.py`: Tkinter launcher for calibration and local preview; Pi needs apt `python3-tk` and a desktop. Reuses existing functions on the main thread, hides while OpenCV runs, then returns. Keep optional headless CLI available.
- VS Code Ctrl+F5: `.vscode/launch.json` runs `main.py` with the project `.venv` Python for Linux/Windows. Linux SSH launches also auto-enable the authorized development desktop, including the plain Run Python File button without launch configuration. Forward loopback port 6080 through SSH and use the laptop browser. Use `main.py --local` to disable sharing explicitly; non-SSH CLI launch stays local unless development mode is requested.
- `.venv/bin/python main.py --dev-remote`: explicit development desktop; needs an active Pi Wayland session and apt `wayvnc novnc websockify util-linux`. Servers stop with the app; no boot service is enabled.
- `python -m unittest discover -s tests -v`: unit tests.
- Windows: `python -m pip install -r requirements.txt` in a standard CPython venv.
- Pi OS: install `python3-opencv` via apt, create `.venv` with `--system-site-packages`, and run `.venv/bin/python`. Avoid mixing pip/apt OpenCV.
- `python calibrate.py --room IT4-101 --seats A-01 A-02`: ROI editor; JSON only. Existing files require `--overwrite`.
- Windows YOLO: install `requirements-yolo.txt`; Pi: use README's apt/pip split to preserve apt OpenCV. Pin Ultralytics 8.3.253 until predictor buffer cleanup is rechecked.
- `python run_vision.py --config roi_config.json --model local/yolo11n.pt --preview`: local inference; omit preview for headless Linux. Prepare trusted model files before camera startup; runtime downloads/auto-installs are disabled. Stage 3 never verifies `EMPTY`.
- `python mock_backend.py`, then `python send_status_demo.py` in another terminal: camera/model-free synthetic HTTP reports to loopback port 8765. This receiver is not the actual backend.
- Copy `examples/transport_config.example.json` to ignored `transport_config.json` to enable reports from existing GUI/CLI inference. Environment overrides: `ZEROFRAME_BACKEND_URL`, `EDGE_NODE_SECRET_KEY`. Missing settings disable transport; never commit real keys.
- Transport keeps one in-flight report and one latest metadata snapshot, retries with bounded backoff, converts monotonic to UTC only at serialization, expires stale snapshots independently of inference, and reports nullable confidence. HTTPS is required except loopback development. External API fields/routes are unchanged.

Update progress after each stage.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions/modules, PascalCase classes, UTF-8, Korean terminology, labeled fences, and two-space JSON indentation. No formatter/linter exists.

Use snake_case fields and uppercase states/events. Accept nullable `confidence` and optional `decision_source`, `last_observed_at`, and `reason`. Keep observation time distinct from `status_since`.

## Testing Guidelines

Use `unittest`; files `tests/test_*.py`, methods `test_*`. No coverage threshold. Use explicit timestamps and fake camera events; never sleep in tests. Cover transitions, gaps, stale inputs, invalid ROIs, mapping ambiguity, and save/cancel failures. Separate simulations from hardware validation.

## Commit & Pull Request Guidelines

Use type-prefixed subjects, matching `feat: initialize vision`.

Merge `feat/<feature>` into `dev` with one reviewer; `main` is the release/demo baseline. Describe changes and validation; link specifications/issues.

## Security & Configuration

Keep images in device RAM only; release buffers on completion, cancellation, or timeout. Prevent image writes to disk, swap, logs, dumps, or external APIs. Transmit metadata JSON only. Keep keys private. Verify storage/network behavior; counters alone cannot establish compliance.

User-authorized exception (2026-11-17): during development and calibration, the trusted developer laptop may receive the Pi desktop/camera preview through an encrypted SSH tunnel for interactive control. The development server binds only to `127.0.0.1:6080`, with WayVNC on a private Unix socket. Image saving/recording and external image APIs remain prohibited. Production/demo deployment retains metadata-only transmission; do not enable the development mode there. Backend/frontend API contracts are unchanged.
