# Repository Guidelines

## Project Structure & Module Organization

Specification: `zeroframe_v1.1_local_multimodal.md`. State: `zeroframe/state.py`; camera/coordinates: `zeroframe/camera.py`, `zeroframe/roi.py`; YOLO/mapping: `zeroframe/detection.py`; transport: `zeroframe/transport.py`; editor: `calibrate.py`; runtime: `run_vision.py`; desktop launcher: `main.py`; VLM diagnostic/evaluation: `prepare_vlm.py`, `probe_vlm.py`, `zeroframe/vlm.py`, `zeroframe/vlm_eval.py`; tests: `tests/test_*.py`. See `README.md` and `docs/implementation-plan.md`. Pi live YOLO/VLM comparison was exercised without ground truth; field quality remains unmeasured.

- `zeroframe-vision`: Python, YOLO11n, ROI, state transitions, optional local SmolVLM2-256M.
- `zeroframe-backend`: authenticated ingestion, freshness, WebSocket; framework undecided.
- `zeroframe-frontend`: React/Next.js maps, four-state UI, `/admin` metrics.
- `zeroframe-docs`: architecture, contracts, layouts, evaluations, demonstrations.

Update contracts in `zeroframe-docs` before changing APIs, events, or fields across implementations.

## Current Implementation Status

Stages 1–2 are implemented; stage 3 includes YOLO, the desktop launcher, and upper-body ROI mapping, with field validation pending. Windows YOLO success is user-reported; the latest 188 automated tests pass on Pi ARM64 / Python 3.13.5 with simulated model/camera inputs and some real loopback HTTP checks. Pi 5 C270 V4L2 capture of 60 frames, the real Tk launcher, and local model inference on a synthetic black frame are verified separately; live YOLO/VLM comparison ran on two seats; accuracy remains unmeasured. Development desktop HTTP/WebSocket/RFB connection and remote mouse/keyboard delivery to a test Tk window are verified locally. Stage 4 transport is complete. On 2026-11-19, the deployed Render backend accepted two authenticated UNKNOWN reports about 10 seconds apart, REST readback matched, and stopping reports produced the documented 30-second offline transition. This verification used no camera. The user subsequently confirmed normal backend JSON receipt and device connectivity; this is user-reported evidence, separate from automated tests and measured detection quality. See `docs/backend-integration.md`; field accuracy/performance measurement and stage 5 field validation of reference-based EMPTY, optional VLM, and final system evaluation remain pending. Keep README, this guide, the specification, and `docs/implementation-plan.md` consistent; distinguish working code, user reports, simulations, and hardware evidence.

## Architecture & State Rules

Stage 5 standalone VLM diagnostics are implemented; see `docs/vlm-probe.md`. On Pi 5, pinned SmolVLM2-256M with CPU float32/SDPA ran on synthetic black inputs and C270 A-01 crops. Initial long-prompt synthetic inference took about 6.8–7.1 seconds with peak worker RSS 1467.9 MiB; traced live inference took about 8.4 seconds with peak 1566 MiB. Initial long-prompt outputs failed the strict label format, and synthetic prompt comparison exposed an incorrect occupied answer. This is model execution evidence, not a quality pass. The diagnostic has one isolated worker with hard deadlines and no state/backend integration. Opt-in runtime --vlm-compare samples one masked seat from the YOLO frame and tags historical comparison labels by frame/seat. The GUI VLM button and CLI --vlm-empty enable seat-local review of marginal enrolled references. A darker view with preserved spatial luminance structure may qualify after tone correction and a local object-change check. Apply the per-frame curve to the current crop only. In --vlm-empty mode, consistent mild dimming measured outside seats may yield a reference match only after a close pixel comparison and a connected-change veto on the seat. Require two consecutive visible, detection-free VLM EMPTY observations for promotion, prioritize the second check, and allow one fresh review to renew confirmed EMPTY. Discard superseded/expired replies. Report fused EMPTY with the existing YOLO_VLM source. Field accuracy is unmeasured. Live mode requires Linux with swap off. The GUI VLM review button temporarily stops active systemd-managed Pi zram and restores it after the comparison subprocess exits. The latest suite contains the 156 other tests plus 32 VLM tests, totaling 188.

Support Windows development and prioritize Raspberry Pi OS Linux deployment (Pi 5, 8GB). USB cameras use DirectShow/V4L2 respectively; CSI requires separate Picamera2 work. Calibration needs a local desktop. Validate Pi hardware and models separately; keep YOLO independent and VLM concurrency at one. Reject stale results.

Follow-up VLM diagnostics default to English choices, 256-pixel input, and visibility abstention. `--compare` tests long/choice prompts at 512/256 on one RAM capture; 256 input also requires image_seq_len=16, versus 64 at 512. Actual traced C270 comparison measured 2.915 seconds for choices/256 versus 7.625 for labels/512, one sample per variant; live ground truth was not supplied, so accuracy is unknown. Raw black controls still elicited incorrect AWAY/EMPTY; darkness/flatness checks may only abstain, never verify EMPTY. `--interactive` prompts the SSH operator to prepare four scenes, keeps expected labels out of worker/model input, and optionally creates a new metadata-only JSON report. Distinguish model accuracy/format from guard abstention/system correctness; missing denominators are null, missing classes are incomplete, and no report activates runtime inference. The latest suite has 32 VLM tests, 188 overall.

Person confidence must be at least 0.30 and belongings confidence at least 0.20. Assign either class to the unique seat containing at least 50% of the visible detection box area. Partial overlap with another seat below 50% permits assignment. If no seat qualifies, multiple seats qualify, or confidence is insufficient, mark intersected seats unclear. Calibrate the seated head/shoulders/back region as well as the desk. Never require visible feet or infer model recall from mapping tests.

Sample YOLO immediately and then every 10 seconds by default. Allow up to 15 seconds between consecutive observations at this cadence. Discard results older than 2 seconds from capture regardless of cadence. Use time-based debouncing. Start `UNKNOWN`; missing detections never establish `EMPTY`. Require sustained absence and current verification, including initial empty seats. Never generate `empty_verified` from missing detections. Expire unverified states after 30 seconds. Internal timestamps use monotonic seconds, not API timestamps. Transport uses 10-second heartbeats; backend offline handling at 30 seconds remains a backend requirement. FPS/latency remain targets.

## Build, Test, and Development Commands

Python 3.10+. Run from the repository root:

- `python -m zeroframe`: synthetic demo; no camera/model/network.
- `python main.py`: Tkinter launcher for calibration and local preview; Pi needs apt `python3-tk` and a desktop. Reuses existing functions on the main thread, hides while OpenCV runs, then returns. Keep optional headless CLI available.
- VS Code Ctrl+F5: `.vscode/launch.json` runs `main.py` with the project `.venv` Python for Linux/Windows. Linux SSH launches also auto-enable the authorized development desktop, including the plain Run Python File button without launch configuration. Forward loopback port 6080 through SSH and use the laptop browser. Use `main.py --local` to disable sharing explicitly; non-SSH CLI launch stays local unless development mode is requested.
- `.venv/bin/python main.py --dev-remote`: explicit development desktop; needs an active Pi Wayland session and apt `wayvnc novnc websockify util-linux`. Servers stop with the app; no boot service is enabled.
- `python -m unittest discover -s tests -v`: unit tests.
- Windows: `python -m pip install -r requirements.txt` in a standard CPython venv.
- Pi OS: install `python3-opencv` via apt, create `.venv` with `--system-site-packages`, and run `.venv/bin/python`. Avoid mixing pip/apt OpenCV.
- The launcher and CLI --max-seats mode accept a positive upper bound. Confirm 1 to that many polygons and save with S. Count mode reuses existing same-room IDs in order. Legacy CLI --seats supports explicit IDs. S saves when all explicitly requested IDs are confirmed and no polygon is unfinished. IT4-101 uses the four seeded IDs first when starting from A-01.
- `python calibrate.py --room IT4-101 --seats A-01 A-02`: ROI editor; JSON only. Existing files require `--overwrite`.
- Windows YOLO: install `requirements-yolo.txt`; Pi: use README's apt/pip split to preserve apt OpenCV. Pin Ultralytics 8.3.253 until predictor buffer cleanup is rechecked.
- `python run_vision.py --config roi_config.json --model local/yolo11n.pt --preview`: local inference; omit preview for headless Linux. Prepare trusted model files before camera startup; runtime downloads/auto-installs are disabled. Only operator-confirmed empty references (preview E or headless --confirm-empty-at-start) enable runtime EMPTY. Compare the same fresh polygon to its locked, session-only reference on three consecutive checks. Field quality remains unmeasured.
- Optional VLM: install `requirements-vlm.txt` after CPU torch setup, then explicitly run `python prepare_vlm.py` before inference. `python probe_vlm.py --synthetic` needs no camera; `--seat A-01 --expected EMPTY` runs a human-labeled live diagnostic. Live probing requires Linux with swap disabled; on this Pi stop/start `dev-zram0.swap` via systemd around the probe as documented, since plain swapon after swapoff does not recreate systemd-managed zram. Workers disable dumps, stop on parent death, and are killed/reaped on request timeout. Never use a probe label as `empty_verified` without integration and field validation.
- `python mock_backend.py`, then `python send_status_demo.py` in another terminal: camera/model-free synthetic HTTP reports to loopback port 8765. This receiver is not the actual backend.
- Copy `examples/transport_config.example.json` to ignored `transport_config.json` to enable reports from existing GUI/CLI inference. Environment overrides: `ZEROFRAME_BACKEND_URL`, `EDGE_NODE_SECRET_KEY`. Missing settings disable transport; never commit real keys.
- The current Pi already has a private `0600` transport config for `https://zeroframe-backend-p1wa.onrender.com`, with a 5-second request timeout. Preserve it instead of overwriting with the loopback example. Follow the docs repository's `INTERFACES.md`; currently calibrated `IT4-101/A-01` is registered. Production credentials remain only in the ignored local file.
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
