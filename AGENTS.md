# Repository Guidelines

## Project Structure & Module Organization

The current specification is `zeroframe_v1.1_local_multimodal.md` (Local Multimodal Edge). Documentation only; no application code, tests, or assets exist.

Four separate repositories are specified:

- `zeroframe-vision`: Python, YOLO11n, ROI calibration, time-based state transitions, and optional local SmolVLM2-256M verification.
- `zeroframe-backend`: authenticated ingestion, freshness tracking, in-memory state, and WebSocket updates; framework remains undecided.
- `zeroframe-frontend`: React/Next.js maps, four-state UI, observation timestamps, and `/admin` metrics.
- `zeroframe-docs`: architecture, contracts, layouts, model evaluations, and demonstrations.

Update contracts in `zeroframe-docs` before changing APIs, events, or fields across implementations.

## Architecture & State Rules

Target Raspberry Pi 5 (8GB, no AI accelerator). Compare NCNN/ONNX for YOLO11n. Enable VLM after hardware, accuracy, and performance validation; run YOLO independently, with one concurrent VLM task. Reject stale results using observation time and generation.

Use time-based debouncing. Missing detections alone never establish `EMPTY`; without required verification, fall back to `UNKNOWN`. Use `UNKNOWN` after 30 seconds without valid observation. Count only `EMPTY` as available. Send heartbeats every 10 seconds; mark all room seats `UNKNOWN` after 30 seconds offline. Browser requests must not trigger inference. FPS and latency are unverified targets.

## Build, Test, and Development Commands

No build, server, dependency, or test tooling is configured.

- `Get-Content -Encoding UTF8 .\zeroframe_v1.1_local_multimodal.md`: read the specification in PowerShell.
- `rg --files`: list workspace files.

Preview Markdown; document future commands.

## Coding Style & Naming Conventions

Preserve UTF-8, Korean terminology, labeled code fences, and two-space JSON indentation. No formatter or linter is configured.

Use snake_case fields and uppercase states/events. Accept nullable `confidence` and optional `decision_source`, `last_observed_at`, and `reason`. Keep observation time distinct from `status_since`.

## Testing Guidelines

No framework or coverage threshold exists. Validate JSON examples and contracts. Implementation checks must cover false-empty errors, ROI boundaries, time-based transitions, stale observations/results, VLM failure, offline/reconnection behavior, and REST/WebSocket counts. Measure hardware performance before claiming targets.

## Commit & Pull Request Guidelines

No Git history exists; suggested subject: `docs: clarify VLM fallback`.

Use `feat/<feature>` into `dev` with one reviewer; `main` is the release/demo baseline. Describe changes, link specifications/issues, report validation, and attach UI screenshots without camera imagery when relevant.

## Security & Configuration

Keep raw, ROI, and calibration images in edge RAM only; release buffers on completion, cancellation, or timeout. Prevent image writes to disk, swap, logs, debug dumps, or external APIs. Transmit non-image JSON only. Keep API keys private. Verify actual storage/network behavior; `saved_frames_count: 0` alone proves nothing.
