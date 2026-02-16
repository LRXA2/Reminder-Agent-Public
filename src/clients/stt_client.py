from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from src.core.config import Settings


class SttClient:
    def __init__(self, settings: Settings):
        self.provider = settings.stt_provider
        self.model_name = settings.stt_model
        self.device = settings.stt_device
        self.compute_type = settings.stt_compute_type
        self.use_highest_vram_gpu = settings.stt_use_highest_vram_gpu
        self._model = None
        self._disabled_reason = ""

    def is_enabled(self) -> bool:
        return self.provider == "faster_whisper"

    def disabled_reason(self) -> str:
        return self._disabled_reason

    def transcribe_bytes(self, audio_bytes: bytes, file_name: str = "") -> str:
        if not self.is_enabled():
            self._disabled_reason = "STT provider disabled"
            return ""

        model = self._ensure_model()
        if model is None:
            return ""

        suffix = self._infer_suffix(file_name)
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(audio_bytes)
                temp_path = temp_file.name

            segments, _info = model.transcribe(
                temp_path,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=True,
            )
            lines = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
            return " ".join(lines).strip()
        except Exception as exc:
            self._disabled_reason = f"Transcription failed: {exc}"
            return ""
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        self._configure_windows_cuda_dll_dirs()

        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            self._disabled_reason = f"faster-whisper import failed: {exc}"
            return None

        device = "cuda" if self.device in {"auto", "cuda"} else "cpu"
        compute_type = self._resolve_compute_type(device)
        try:
            if device == "cuda":
                device_index = self._pick_cuda_device_index() if self.use_highest_vram_gpu else 0
                self._model = WhisperModel(
                    self.model_name,
                    device=device,
                    device_index=device_index,
                    compute_type=compute_type,
                )
            else:
                self._model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
            return self._model
        except Exception:
            if device == "cuda":
                try:
                    self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                    return self._model
                except Exception as exc:
                    self._disabled_reason = f"Model load failed: {exc}"
                    return None
            raise

    def _resolve_compute_type(self, device: str) -> str:
        if self.compute_type != "auto":
            return self.compute_type
        return "float16" if device == "cuda" else "int8"

    def _infer_suffix(self, file_name: str) -> str:
        name = (file_name or "").strip()
        if not name:
            return ".ogg"
        suffix = Path(name).suffix.lower()
        if suffix:
            return suffix
        return ".ogg"

    def _pick_cuda_device_index(self) -> int:
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

    def _configure_windows_cuda_dll_dirs(self) -> None:
        if os.name != "nt":
            return
        add_dir = getattr(os, "add_dll_directory", None)
        if not callable(add_dir):
            return

        cuda_candidates = []
        cuda_path = os.getenv("CUDA_PATH", "").strip()
        if cuda_path:
            cuda_candidates.append(Path(cuda_path) / "bin")

        default_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
        if default_root.exists():
            for version_dir in sorted(default_root.glob("v12*"), reverse=True):
                cuda_candidates.append(version_dir / "bin")

        seen: set[str] = set()
        for candidate in cuda_candidates:
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
