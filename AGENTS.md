# Repository Guidelines

## Project Structure & Module Organization

Specification: `zeroframe_v1.1_local_multimodal.md`. State: `zeroframe/state.py`; camera/coordinates: `zeroframe/camera.py`, `zeroframe/roi.py`; editor: `calibrate.py`; tests: `tests/test_*.py`. See `README.md` and `docs/implementation-plan.md`. Hardware/model verification remains pending.

- `zeroframe-vision`: Python, YOLO11n, ROI, state transitions, optional local SmolVLM2-256M.
- `zeroframe-backend`: authenticated ingestion, freshness, WebSocket; framework undecided.
- `zeroframe-frontend`: React/Next.js maps, four-state UI, `/admin` metrics.
- `zeroframe-docs`: architecture, contracts, layouts, evaluations, demonstrations.

Update contracts in `zeroframe-docs` before changing APIs, events, or fields across implementations.

## Architecture & State Rules

Develop on a laptop with its built-in camera; validate Raspberry Pi 5 (8GB, no accelerator) separately. Compare NCNN/ONNX for YOLO11n. Validate VLM compatibility, accuracy, and performance before enabling it; keep YOLO independent and VLM concurrency at one. Reject stale results.

Use time-based debouncing. Start `UNKNOWN`; missing detections never establish `EMPTY`. Require sustained absence and current verification, including initial empty seats. Never generate `empty_verified` from missing detections. Expire unverified states after 30 seconds. Internal timestamps use monotonic seconds, not API timestamps. Future transport uses 10-second heartbeats and backend offline handling at 30 seconds. FPS/latency remain targets.

## Build, Test, and Development Commands

Python 3.10+. Run from the repository root:

- `python -m zeroframe`: synthetic demo; no camera/model/network.
- `python -m unittest discover -s tests -v`: unit tests.
- `python -m pip install -r requirements.txt`: GUI OpenCV; use a standard CPython venv (Windows/MSYS2 instructions in README).
- `python calibrate.py --room IT4-101 --seats A-01 A-02`: ROI editor; JSON only. Existing files require `--overwrite`.

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
