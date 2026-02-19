from __future__ import annotations

import subprocess
import time
from typing import Any
import os
import base64
import json
import re

import requests

from src.app.prompts import group_summary_prompt, image_reminder_extract_prompt, image_summary_prompt


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        text_model: str,
        vision_model: str = "",
        request_timeout_seconds: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.text_model = text_model.strip()
        self.vision_model = vision_model.strip()
        self.request_timeout_seconds = max(20, int(request_timeout_seconds))

    def ensure_server(self, autostart: bool, timeout_seconds: int, use_highest_vram_gpu: bool = False) -> bool:
        if self._is_server_ready():
            return True
        if not autostart:
            return False

        launch_env = self._build_ollama_env(use_highest_vram_gpu)
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                env=launch_env,
            )
        except OSError:
            return False

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._is_server_ready():
                return True
            time.sleep(0.5)
        return False

    def set_text_model(self, model: str) -> None:
        self.text_model = model.strip()

    def set_vision_model(self, model: str) -> None:
        self.vision_model = model.strip()

    def get_text_model(self) -> str:
        return self._resolve_text_model()

    def get_vision_model(self) -> str:
        if self.vision_model:
            return self.vision_model
        return self._resolve_text_model()

    def list_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()
            data = response.json()
            models = data.get("models", [])
            return [m.get("name", "").strip() for m in models if (m.get("name") or "").strip()]
        except Exception:
            return []

    def detect_nvidia_gpu(self) -> dict[str, Any]:
        command = [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except OSError as exc:
            return {"has_gpu": False, "error": str(exc)}

        if result.returncode != 0:
            error = (result.stderr or result.stdout or "nvidia-smi failed").strip()
            return {"has_gpu": False, "error": error}

        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return {"has_gpu": False, "error": "No Nvidia GPU detected"}

        gpus = []
        for line in lines:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2:
                gpus.append(f"{parts[0]} ({parts[1]}MB)")
            else:
                gpus.append(line)
        return {"has_gpu": True, "gpus": gpus}

    def ollama_ps(self) -> str:
        try:
            result = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=8, check=False)
        except OSError as exc:
            return f"ollama ps unavailable: {exc}"
        output = (result.stdout or result.stderr or "").strip()
        return output or "No active Ollama sessions."

    def _build_ollama_env(self, use_highest_vram_gpu: bool) -> dict[str, str]:
        env = dict(os.environ)
        if not use_highest_vram_gpu:
            return env

        best_index = self._pick_highest_vram_gpu_index()
        if best_index is None:
            return env

        env["CUDA_VISIBLE_DEVICES"] = str(best_index)
        return env

    def _pick_highest_vram_gpu_index(self) -> int | None:
        command = [
            "nvidia-smi",
            "--query-gpu=index,memory.total",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except OSError:
            return None

        if result.returncode != 0:
            return None

        best_index: int | None = None
        best_mem = -1
        for line in result.stdout.splitlines():
            row = line.strip()
            if not row:
                continue
            parts = [p.strip() for p in row.split(",")]
            if len(parts) < 2:
                continue
            try:
                idx = int(parts[0])
                mem = int(parts[1])
            except ValueError:
                continue
            if mem > best_mem:
                best_mem = mem
                best_index = idx
        return best_index

    def summarize_messages(self, lines: list[str]) -> str:
        if not lines:
            return "No recent messages to summarize."
        prompt = group_summary_prompt(lines)
        return self._generate(prompt)

    def generate_text(self, prompt: str) -> str:
        return self._generate(prompt)

    def summarize_image(self, image_bytes: bytes, user_instruction: str = "") -> str:
        model = self.get_vision_model()
        if not model:
            return "Image summary unavailable (no active vision model)."

        prompt = image_summary_prompt(user_instruction)
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [encoded_image],
            "stream": False,
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.request_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            text = (data.get("response") or "").strip()
            return text or "I could not extract a useful summary from that image."
        except Exception as exc:
            return f"Image summary unavailable (Ollama error: {exc})."

    def extract_reminder_from_image(self, image_bytes: bytes, user_instruction: str) -> dict[str, str]:
        model = self.get_vision_model()
        if not model:
            return {
                "title": "Review image details",
                "notes": "Vision summary unavailable (no active vision model).",
            }

        prompt = image_reminder_extract_prompt(user_instruction)
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [encoded_image],
            "stream": False,
        }
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.request_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            raw = (data.get("response") or "").strip()
        except Exception as exc:
            return {
                "title": "Review image details",
                "notes": f"Vision summary unavailable (Ollama error: {exc}).",
            }

        parsed = self._parse_json_response(raw)
        if parsed:
            title = (parsed.get("title") or "").strip()
            notes = (parsed.get("notes") or "").strip()
            if title:
                return {
                    "title": self._clamp_words(title, max_words=12),
                    "notes": notes[:280],
                }

        fallback_title = self._clamp_words(self._first_nonempty_line(raw) or "Review image details", max_words=12)
        return {
            "title": fallback_title,
            "notes": raw[:280],
        }

    def _generate(self, prompt: str) -> str:
        model = self._resolve_text_model()
        if not model:
            return "Summary unavailable (set OLLAMA_TEXT_MODEL or OLLAMA_MODEL, or install at least one Ollama model)."

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                response = requests.post(url, json=payload, timeout=self.request_timeout_seconds)
                response.raise_for_status()
                data = response.json()
                text = data.get("response", "").strip()
                return text or "(No summary returned by model.)"
            except Exception as exc:
                last_error = exc
                time.sleep(0.4)
        return f"Summary unavailable (Ollama error: {last_error})."

    def _resolve_text_model(self) -> str:
        if self.text_model:
            return self.text_model
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            models = data.get("models", [])
            if not models:
                return ""
            first_name = (models[0].get("name") or "").strip()
            if first_name:
                self.text_model = first_name
            return self.text_model
        except Exception:
            return ""

    def _parse_json_response(self, text: str) -> dict[str, str] | None:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return {str(k): str(v) for k, v in loaded.items()}
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        return {str(k): str(v) for k, v in loaded.items()}

    def _clamp_words(self, text: str, max_words: int) -> str:
        words = text.split()
        if len(words) <= max_words:
            return text.strip()
        return " ".join(words[:max_words]).strip()

    def _first_nonempty_line(self, text: str) -> str:
        for line in text.splitlines():
            cleaned = line.strip("- *\t ")
            if cleaned:
                return cleaned
        return ""

    def _is_server_ready(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return response.status_code == 200
        except Exception:
            return False
