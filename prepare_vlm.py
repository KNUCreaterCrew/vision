"""Explicit model download, before opening a camera; never used by inference."""

import argparse
from pathlib import Path


MODEL_ID = "HuggingFaceTB/SmolVLM2-256M-Video-Instruct"
MODEL_REVISION = "9cda5c165b28c68a52e9bb542d2b89f0f6963709"
MODEL_FILES = (
    "config.json", "generation_config.json", "preprocessor_config.json",
    "processor_config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "added_tokens.json", "chat_template.json",
    "merges.txt", "vocab.json", "model.safetensors",
)
DEFAULT_MODEL = Path(__file__).resolve().parent / "local" / "SmolVLM2-256M-Video-Instruct"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download the pinned official SmolVLM2 snapshot; no camera.")
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        parser.exit(1, "Install requirements-vlm.txt in the project venv first.\n")
    snapshot_download(
        repo_id=MODEL_ID, revision=MODEL_REVISION,
        allow_patterns=list(MODEL_FILES), local_dir=args.output,
        max_workers=2, token=False,
    )
    missing = [name for name in MODEL_FILES if not (args.output / name).is_file()]
    if missing:
        parser.exit(1, "Incomplete model snapshot. Run prepare_vlm.py again.\n")
    print(f"Local model ready: {args.output}\nRevision: {MODEL_REVISION}")


if __name__ == "__main__":
    main()
