from __future__ import annotations

import subprocess
import time
from typing import Any
import os

import requests


class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()

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

    def set_model(self, model: str) -> None:
        self.model = model.strip()

    def get_model(self) -> str:
        return self._resolve_model()

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

        prompt = (
            "You summarize Telegram group updates. Return concise markdown with sections: "
            "Key updates, Decisions, Action items, Open questions. Keep it practical and brief.\n\n"
            "Messages:\n"
            + "\n".join(lines)
        )
        return self._generate(prompt)

    def generate_text(self, prompt: str) -> str:
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        model = self._resolve_model()
        if not model:
            return "Summary unavailable (set OLLAMA_MODEL or install at least one Ollama model)."

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            text = data.get("response", "").strip()
            return text or "(No summary returned by model.)"
        except Exception as exc:
            return f"Summary unavailable (Ollama error: {exc})."

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            models = data.get("models", [])
            if not models:
                return ""
            first_name = (models[0].get("name") or "").strip()
            if first_name:
                self.model = first_name
            return self.model
        except Exception:
            return ""

    def _is_server_ready(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return response.status_code == 200
        except Exception:
            return False
