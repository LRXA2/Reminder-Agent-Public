from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test local speech-to-text with faster-whisper")
    parser.add_argument("--file", default="", help="Path to audio file")
    parser.add_argument("--model", default="large-v3", help="Whisper model name")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="Execution device")
    parser.add_argument(
        "--compute-type",
        default="auto",
        help="Compute type for faster-whisper (auto, float16, int8, int8_float16, int8_float32, etc.)",
    )
    parser.add_argument(
        "--highest-vram-gpu",
        action="store_true",
        help="When using CUDA, pick the Nvidia GPU with the largest total VRAM",
    )
    parser.add_argument("--language", default="", help="Optional language code like en")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size")
    parser.add_argument("--no-vad", action="store_true", help="Disable VAD filter")
    parser.add_argument("--max-chars", type=int, default=6000, help="Max chars to print")
    return parser.parse_args()


def pick_cuda_device_index() -> int:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return 0
        best_index = 0
        best_vram = -1
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line or "," not in line:
                continue
            idx_text, vram_text = [part.strip() for part in line.split(",", 1)]
            try:
                idx = int(idx_text)
                vram = int(vram_text)
            except ValueError:
                continue
            if vram > best_vram:
                best_vram = vram
                best_index = idx
        return best_index
    except Exception:
        return 0


def resolve_device_and_compute(args: argparse.Namespace) -> tuple[str, str, int | None]:
    device = "cuda" if args.device == "auto" else args.device
    compute_type = args.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    device_index: int | None = None
    if device == "cuda":
        device_index = pick_cuda_device_index() if args.highest_vram_gpu else 0
    return device, compute_type, device_index


def configure_windows_cuda_dll_dirs() -> None:
    import os

    if os.name != "nt":
        return
    add_dir = getattr(os, "add_dll_directory", None)
    if not callable(add_dir):
        return

    candidates: list[Path] = []
    cuda_path = os.getenv("CUDA_PATH", "").strip()
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin")

    root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if root.exists():
        for version_dir in sorted(root.glob("v12*"), reverse=True):
            candidates.append(version_dir / "bin")

    seen: set[str] = set()
    for candidate in candidates:
        path_str = str(candidate)
        if path_str in seen:
            continue
        seen.add(path_str)
        if not candidate.exists():
            continue
        try:
            add_dir(path_str)
        except Exception:
            continue


def main() -> int:
    args = parse_args()
    file_path = args.file.strip()
    if not file_path:
        file_path = input("Enter audio file path: ").strip()
    file_path = file_path.strip().strip('"').strip("'")
    audio_path = Path(file_path)
    if not audio_path.exists() or not audio_path.is_file():
        print(f"ERROR: File not found: {audio_path}")
        return 1

    try:
        configure_windows_cuda_dll_dirs()
        from faster_whisper import WhisperModel
    except Exception as exc:
        print("ERROR: faster-whisper is not installed or failed to import.")
        print(f"Details: {exc}")
        print("Install with: python -m pip install -r requirements.txt")
        return 2

    device, compute_type, device_index = resolve_device_and_compute(args)
    print("Starting STT test...")
    print(f"- File: {audio_path}")
    print(f"- Model: {args.model}")
    print(f"- Device: {device}")
    print(f"- Compute type: {compute_type}")
    if device_index is not None:
        print(f"- CUDA device index: {device_index}")

    try:
        if device_index is not None:
            model = WhisperModel(args.model, device=device, device_index=device_index, compute_type=compute_type)
        else:
            model = WhisperModel(args.model, device=device, compute_type=compute_type)
    except Exception as exc:
        print("ERROR: Failed to load model.")
        print(f"Details: {exc}")
        return 3

    kwargs: dict[str, object] = {
        "beam_size": args.beam_size,
        "vad_filter": not args.no_vad,
        "condition_on_previous_text": True,
    }
    if args.language.strip():
        kwargs["language"] = args.language.strip()

    try:
        segments, info = model.transcribe(str(audio_path), **kwargs)
        transcript = " ".join(seg.text.strip() for seg in segments if seg.text and seg.text.strip()).strip()
    except Exception as exc:
        print("ERROR: Transcription failed.")
        print(f"Details: {exc}")
        return 4

    print("Done.")
    print(f"- Detected language: {getattr(info, 'language', 'unknown')}")
    print("\nTranscript:\n")
    if transcript:
        print(transcript[: args.max_chars])
        if len(transcript) > args.max_chars:
            print("\n...[truncated]")
    else:
        print("(empty transcript)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
