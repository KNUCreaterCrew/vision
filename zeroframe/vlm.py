"""Optional offline seat VLM; diagnostic labels are not state verification."""

import os
from pathlib import Path
import re
import signal
import sys
from time import monotonic

from .state import Status


PROMPTS = {
    "en": (
        "Look only at this seat (desk and chair). Ignore the gray masked border. "
        "Return exactly one label and no explanation: "
        "OCCUPIED if a person is using the seat; AWAY if there is no person but "
        "personal belongings reserve it; EMPTY only if the desk and chair are "
        "clearly visible and there is no person or personal belonging; "
        "UNKNOWN if the seat is obscured, dark, unclear, or not visible."
    ),
    "ko": (
        "이 좌석의 책상과 의자만 판단하세요. 회색으로 가린 테두리는 무시하세요. "
        "설명 없이 다음 라벨 하나만 답하세요: 사람이 사용 중이면 OCCUPIED, "
        "사람 없이 개인 소지품만 남아 있으면 AWAY, 책상과 의자가 잘 보이고 "
        "사람과 소지품이 모두 없을 때만 EMPTY, 가려지거나 어둡거나 "
        "좌석이 보이지 않아 판단할 수 없으면 UNKNOWN."
    ),
}

CHOICE_PROMPTS = {
    "en": (
        "What is visible in this seat? A: a person. B: personal belongings, no person. "
        "C: clearly empty desk and chair. D: cannot see the desk and chair clearly. "
        "Answer with only A, B, C, or D."
    ),
    "ko": (
        "이 좌석에 무엇이 보이나요? A: 사람. B: 사람 없이 개인 소지품만 있음. "
        "C: 책상과 의자가 잘 보이며 비어 있음. D: 책상과 의자를 명확히 볼 수 없음. "
        "A, B, C, D 중 하나만 답하세요."
    ),
}
CHOICE_LABELS = {"A": Status.OCCUPIED, "B": Status.AWAY, "C": Status.EMPTY, "D": Status.UNKNOWN}


def parse_choice(text, *, complete=True):
    if not complete:
        return Status.UNKNOWN, "TRUNCATED_OUTPUT"
    match = re.fullmatch(r"([ABCD])\.?", text.strip()) if isinstance(text, str) else None
    if match is None:
        return Status.UNKNOWN, "INVALID_OUTPUT"
    label = CHOICE_LABELS[match[1]]
    return label, "MODEL_UNKNOWN" if label == Status.UNKNOWN else None


def visibility_stats(luma, *, width, height):
    """Conservative abstention only; these thresholds never verify an empty seat."""
    import numpy as np
    if min(width, height) < 32 or luma.size < 1024:
        return {"usable": False, "reason": "TOO_SMALL"}
    mean = float(np.mean(luma))
    low, high = np.percentile(luma, (5, 95))
    reason = ("TOO_DARK" if mean < 12 else "OVEREXPOSED" if mean > 243 else
              "LOW_CONTRAST" if high - low < 16 else None)
    return {"usable": reason is None, "reason": reason}


def image_visibility(image):
    if "seat_visibility" in image.info:
        return image.info["seat_visibility"]
    import numpy as np
    with image.convert("L") as gray:
        return visibility_stats(np.asarray(gray), width=image.width, height=image.height)


def parse_label(text, *, complete=True):
    """Reject prose, conflicts and token-limit truncation, including apparent EMPTY."""
    if not complete or not isinstance(text, str):
        return Status.UNKNOWN, "TRUNCATED_OUTPUT" if not complete else "INVALID_OUTPUT"
    try:
        label = Status(text.strip())
    except ValueError:
        return Status.UNKNOWN, "INVALID_OUTPUT"
    return label, "MODEL_UNKNOWN" if label == Status.UNKNOWN else None


def protect_camera_process(*, live, worker=True):
    """Fail before camera startup unless Linux swap/dumps are disabled."""
    if not sys.platform.startswith("linux"):
        if live:
            raise RuntimeError("Live VLM privacy checks currently require Linux; use --synthetic here.")
        return
    import ctypes
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE = 4
        raise RuntimeError("Cannot disable process dumps before VLM inference.")
    if worker and libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG = 1
        raise RuntimeError("Cannot bind the VLM process lifetime to its parent.")
    if live and len(Path("/proc/swaps").read_text().splitlines()) > 1:
        raise RuntimeError(
            "Live VLM requires swap disabled before camera startup. "
            "Pi: sudo systemctl stop dev-zram0.swap (restore after exit). See docs/vlm-probe.md."
        )


def stop_worker(process, connection):
    connection.close()
    if process.is_alive():
        process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)
    if process.is_alive():
        raise RuntimeError("VLM worker did not stop; close this process before another probe.")


def seat_image(frame, config, seat_id, *, lighting_lut=None):
    """Copy only the selected polygon to a RAM RGB image; mask adjacent content."""
    if frame.shape != (config.frame_height, config.frame_width, 3):
        raise ValueError("Camera resolution differs from calibration; recalibrate first.")
    seat = next((seat for seat in config.seats if seat.seat_id == seat_id), None)
    if seat is None:
        raise ValueError("Selected seat is missing from ROI configuration.")
    from PIL import Image
    import numpy as np
    from .camera import require_opencv

    if frame.dtype != np.uint8:
        raise ValueError("Expected an 8-bit BGR camera frame.")
    cv2 = require_opencv()
    left = min(x for x, _ in seat.polygon)
    top = min(y for _, y in seat.polygon)
    right = max(x for x, _ in seat.polygon) + 1
    bottom = max(y for _, y in seat.polygon) + 1
    crop = mask = rgb = gray = None
    try:
        crop = frame[top:bottom, left:right].copy()
        mask = np.zeros(crop.shape[:2], dtype=np.uint8)
        points = np.array([(x - left, y - top) for x, y in seat.polygon], dtype=np.int32)
        cv2.fillPoly(mask, [points], 255)
        if lighting_lut is not None:
            crop = cv2.LUT(crop, lighting_lut)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        visibility = visibility_stats(gray[mask > 0], width=crop.shape[1], height=crop.shape[0])
        gray = None
        crop[mask == 0] = 127
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.info["seat_visibility"] = visibility
        return image
    finally:
        crop = mask = rgb = gray = None


class LocalSeatVLM:
    """Single caller, CPU float32, safetensors only; no online fallback or image I/O."""

    def __init__(self, model_dir, *, threads=2, language="en", profile="choices", image_edge=256,
                 visibility_guard=True):
        model_dir = Path(model_dir).resolve()
        required = ("config.json", "model.safetensors", "tokenizer.json", "preprocessor_config.json")
        if not model_dir.is_dir() or any(not (model_dir / name).is_file() for name in required):
            raise ValueError("Local VLM files missing. Run prepare_vlm.py before opening a camera.")
        if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 4:
            raise ValueError("VLM CPU threads must be between 1 and 4.")
        if language not in PROMPTS:
            raise ValueError("VLM language must be en or ko.")
        for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
            os.environ[name] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        try:
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as error:
            raise RuntimeError("Install requirements-vlm.txt in the project venv first.") from error
        torch.set_num_threads(threads)
        self.torch = torch
        self.language = language
        self.visibility_guard = visibility_guard
        self.processor = AutoProcessor.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=False,
            size={"longest_edge": 512}, do_image_splitting=False,
        )
        self.model = AutoModelForImageTextToText.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=False,
            use_safetensors=True, dtype=torch.float32, attn_implementation="sdpa",
        ).to("cpu").eval()
        self.configure(profile, image_edge)

    def configure(self, profile, image_edge):
        if profile not in ("labels", "choices") or image_edge not in (256, 512):
            raise ValueError("VLM profile must be labels/choices, image edge 256/512.")
        patch = self.model.config.vision_config.patch_size
        scale = self.model.config.scale_factor
        if image_edge % (patch * scale):
            raise ValueError("Image edge is incompatible with model patch/connector sizes.")
        self.profile = profile
        self.image_edge = image_edge
        self.prompt = (PROMPTS if profile == "labels" else CHOICE_PROMPTS)[self.language]
        self.processor.image_processor.size = {"longest_edge": image_edge}
        self.processor.image_processor.max_image_size = {"longest_edge": image_edge}
        self.processor.image_seq_len = (image_edge // patch // scale) ** 2

    def classify(self, image):
        started_at = monotonic()
        if self.visibility_guard:
            visibility = image_visibility(image)
            if not visibility["usable"]:
                return {
                    "label": "UNKNOWN", "reason": "LOW_VISIBILITY", "guard_reason": visibility["reason"],
                    "model_ran": False, "format_valid": None, "input_tokens": 0, "generated_tokens": 0,
                    "inference_seconds": round(monotonic() - started_at, 3),
                }
        inputs = generated = new_ids = None
        try:
            messages = [{"role": "user", "content": [
                {"type": "image"}, {"type": "text", "text": self.prompt},
            ]}]
            prompt = self.processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
            inputs = self.processor(text=prompt, images=[image], return_tensors="pt")
            input_tokens = inputs["input_ids"].shape[1]
            with self.torch.inference_mode():
                generated = self.model.generate(**inputs, do_sample=False, max_new_tokens=8)
            new_ids = generated[0, input_tokens:]
            eos_ids = self.model.generation_config.eos_token_id
            eos_ids = eos_ids if isinstance(eos_ids, (list, tuple)) else [eos_ids]
            complete = len(new_ids) > 0 and int(new_ids[-1]) in eos_ids
            text = self.processor.tokenizer.decode(new_ids, skip_special_tokens=True)
            parser = parse_label if self.profile == "labels" else parse_choice
            label, reason = parser(text, complete=complete)
            return {
                "label": label.value, "reason": reason,
                "format_valid": reason in (None, "MODEL_UNKNOWN"),
                "model_ran": True,
                "inference_seconds": round(monotonic() - started_at, 3),
                "input_tokens": input_tokens, "generated_tokens": len(new_ids),
            }
        finally:
            inputs = generated = new_ids = None
