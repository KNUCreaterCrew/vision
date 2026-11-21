"""One-seat VLM measurements in an isolated worker. No backend/state updates."""

import argparse
import json
import multiprocessing
from pathlib import Path
import sys
from time import monotonic

from prepare_vlm import DEFAULT_MODEL, MODEL_ID, MODEL_REVISION
from zeroframe.roi import load_config
from zeroframe.state import Status
from zeroframe.vlm import LocalSeatVLM, protect_camera_process, seat_image, stop_worker
from zeroframe.vlm_eval import summarize_results


CASE_INSTRUCTIONS = {
    "OCCUPIED": "사람이 이 좌석에 앉은 상태",
    "AWAY": "사람 없이 개인 가방·책·노트북 등 짐만 남은 상태",
    "EMPTY": "책상·의자가 잘 보이고 사람·개인 소지품은 모두 없는 상태",
    "UNKNOWN": "좌석이 가려지거나 구도를 확인할 수 없는 상태",
}


def variants_for(settings):
    if settings.get("compare"):
        return [{"profile": profile, "image_edge": edge}
                for edge in (512, 256) for profile in ("labels", "choices")]
    return [{"profile": settings.get("profile", "choices"), "image_edge": settings.get("image_edge", 256)}]


def memory_metrics():
    import psutil
    current = psutil.Process().memory_info()
    if sys.platform.startswith("linux"):
        import resource
        peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    else:
        peak_mib = getattr(current, "peak_wset", current.rss) / (1024 * 1024)
    peak_mib = max(peak_mib, current.rss / (1024 * 1024))
    return {"rss_mib": round(current.rss / (1024 * 1024), 1), "peak_rss_mib": round(peak_mib, 1)}


def probe_worker(connection, settings):
    image = frame = model = None
    stage = "privacy"
    try:
        protect_camera_process(live=not settings["synthetic"])
        stage = "model_load"
        started_at = monotonic()
        variants = variants_for(settings)
        model = LocalSeatVLM(settings["model"], threads=settings["threads"], language=settings["language"],
                             visibility_guard=not settings.get("raw_model", False), **variants[0])
        connection.send({"type": "ready", "load_seconds": round(monotonic() - started_at, 3), **memory_metrics()})
        config = None if settings["synthetic"] else load_config(settings["config"])
        while True:
            request = connection.recv()
            if request == "stop":
                break
            try:
                stage = "capture"
                if settings["synthetic"]:
                    from PIL import Image
                    image = Image.new("RGB", (512, 512), settings.get("control", "black"))
                    observed_at = monotonic()
                else:
                    from zeroframe.camera import camera_session, read_frame
                    stage = "privacy"
                    protect_camera_process(live=True)
                    stage = "capture"
                    with camera_session(settings["camera"]) as capture:
                        for _ in range(5):
                            frame = read_frame(capture)
                        observed_at = monotonic()
                        image = seat_image(frame, config, settings["seat"])
                    frame = None
                for index, variant in enumerate(variants):
                    stage = "inference"
                    model.configure(**variant)
                    result = model.classify(image)
                    result.update(variant)
                    result.update(type="result", frame_id=request["frame_id"],
                                  captured_at=observed_at,
                                  observation_age_seconds=round(monotonic() - observed_at, 3))
                    # Only metadata leaves the worker; the final send happens after image release.
                    if index == len(variants) - 1:
                        image.close()
                        image = None
                    connection.send({**result, **memory_metrics()})
            finally:
                frame = None
                if image is not None:
                    image.close()
                image = None
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        # No traceback, model-generated prose, frame repr, or image buffers in logs/IPC.
        message = str(error) if stage in ("privacy", "model_load") else f"VLM worker failed during {stage}; image/error contents omitted."
        connection.send({"type": "error", "message": message, "error_type": type(error).__name__})
    finally:
        frame = image = model = None
        connection.close()


def receive_message(connection, timeout):
    if not connection.poll(timeout):
        raise TimeoutError("VLM worker deadline exceeded.")
    try:
        result = connection.recv()
    except EOFError as error:
        raise RuntimeError("VLM worker exited without a result.") from error
    if result["type"] == "error":
        raise RuntimeError(f"{result['error_type']}: {result['message']}")
    return result


def run_probe(settings, *, samples=3, timeout=15, expected=None, interactive=False, cases=None, report_path=None):
    context = multiprocessing.get_context("spawn")
    connection, child_connection = context.Pipe()
    process = context.Process(target=probe_worker, args=(child_connection, settings), daemon=True)
    process.start()
    child_connection.close()
    records = []
    variants = variants_for(settings)
    header = {
        "probe_only": True, "source": f"synthetic_{settings.get('control', 'black')}" if settings["synthetic"] else "camera",
        "seat_id": settings["seat"], "model": MODEL_ID, "revision": MODEL_REVISION,
        "threads": settings["threads"], "language": settings["language"],
        "visibility_guard": not settings.get("raw_model", False), "variants": variants,
        "timeout_seconds_per_variant": timeout,
    }
    run_status = "error"
    try:
        ready = receive_message(connection, 60)
        print(json.dumps({**header, **ready}), flush=True)
        targets = list(cases or CASE_INSTRUCTIONS) if interactive else [expected]
        frame_id = 0
        for target in targets:
            if interactive:
                try:
                    answer = input(f"\n{settings['seat']}: {CASE_INSTRUCTIONS[target]}를 준비하세요. "
                                   f"{samples}회 촬영이 끝날 때까지 유지합니다. Enter=촬영, s=건너뛰기, q=종료: ").strip().lower()
                except EOFError:
                    answer = "q"
                if answer == "q":
                    run_status = "cancelled"
                    break
                if answer == "s":
                    continue
                if answer:
                    raise ValueError("촬영은 Enter로 확인하세요. 입력 오류로 촬영하지 않았습니다.")
            for index in range(samples):
                frame_id += 1
                # Ground truth stays in the parent and is never supplied to the model.
                connection.send({"type": "probe", "frame_id": frame_id})
                for variant in variants:
                    started_at = monotonic()
                    try:
                        result = receive_message(connection, timeout)
                    except TimeoutError:
                        stop_worker(process, connection)
                        result = {"type": "result", **variant, "frame_id": frame_id,
                                  "label": "UNKNOWN", "reason": "VLM_TIMEOUT", "model_ran": None,
                                  "format_valid": None}
                        run_status = "timeout"
                    result.update(sample=index + 1, request_seconds=round(monotonic() - started_at, 3),
                                  visibility_guard=not settings.get("raw_model", False))
                    if target is not None:
                        result.update(expected=target,
                                      expected_source="operator" if interactive else "synthetic" if settings["synthetic"] else "cli",
                                      matches_expected=(result.get("format_valid") is True and result["label"] == target)
                                      if result.get("model_ran") is True else None,
                                      system_matches_expected=result["label"] == target)
                    records.append(result)
                    print(json.dumps(result), flush=True)
                    if run_status == "timeout":
                        return 2
        connection.send("stop")
        process.join(timeout=2)
        if run_status != "cancelled":
            run_status = "completed"
        return 0
    except KeyboardInterrupt:
        run_status = "interrupted"
        raise
    finally:
        stop_worker(process, connection)
        summary = summarize_results(records)
        print(json.dumps({"type": "summary", "run_status": run_status, **summary}), flush=True)
        if report_path is not None:
            report_path = Path(report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as output:
                json.dump({"schema_version": 1, **header, "run_status": run_status,
                           "records": records, "summary": summary}, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline one-seat VLM probe; prints metadata only, never updates seat states.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--synthetic", action="store_true", help="Uniform RAM control image; expected UNKNOWN; never opens camera.")
    parser.add_argument("--config", type=Path, default=Path("roi_config.json"))
    parser.add_argument("--seat", help="One seat ID from the existing calibration.")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--language", choices=("en", "ko"), default="en")
    parser.add_argument("--profile", choices=("labels", "choices"), default="choices")
    parser.add_argument("--image-edge", type=int, choices=(256, 512), default=256)
    parser.add_argument("--compare", action="store_true", help="Compare both prompts at 512 and 256 using the same RAM capture.")
    parser.add_argument("--raw-model", action="store_true", help="Diagnostic only: bypass visibility abstention to measure model failures.")
    parser.add_argument("--control", choices=("black", "white", "gray"), default="black", help="Color for --synthetic.")
    parser.add_argument("--interactive", action="store_true", help="SSH terminal prompts for four human-prepared seat states.")
    parser.add_argument("--cases", nargs="+", choices=tuple(CASE_INSTRUCTIONS), help="Subset of states for --interactive.")
    parser.add_argument("--report", type=Path, help="Create a new metadata-only JSON report; never overwrite an existing file.")
    parser.add_argument("--timeout", type=float, default=15, help="Hard per-request deadline, including capture; 1..60 seconds.")
    parser.add_argument("--expected", choices=tuple(status.value for status in Status), help="Live human label; metadata only.")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 20 or not 1 <= args.threads <= 4 or not 1 <= args.timeout <= 60 or args.camera < 0:
        parser.error("samples=1..20, threads=1..4, timeout=1..60, camera>=0 required.")
    if (args.interactive and (args.synthetic or args.expected)) or (args.cases and not args.interactive):
        parser.error("--interactive needs real camera, without --expected; --cases needs --interactive.")
    if args.report is not None and (args.report.suffix.lower() != ".json" or args.report.exists()):
        parser.error("--report must name a new .json file; existing files are protected.")
    if not args.synthetic:
        if not args.seat:
            parser.error("Specify --seat, or use --synthetic for the camera-free compatibility test.")
        try:
            config = load_config(args.config)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        if args.seat not in {seat.seat_id for seat in config.seats}:
            parser.error("Selected seat is missing from ROI configuration.")
    settings = {
        "model": str(args.model.resolve()), "config": str(args.config.resolve()),
        "seat": args.seat, "camera": args.camera, "synthetic": args.synthetic,
        "threads": args.threads, "language": args.language,
        "profile": args.profile, "image_edge": args.image_edge, "compare": args.compare,
        "raw_model": args.raw_model, "control": args.control,
    }
    try:
        return run_probe(settings, samples=args.samples, timeout=args.timeout,
                         expected="UNKNOWN" if args.synthetic else args.expected,
                         interactive=args.interactive, cases=args.cases, report_path=args.report)
    except (RuntimeError, TimeoutError, OSError, ValueError) as error:
        print(f"VLM probe failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
