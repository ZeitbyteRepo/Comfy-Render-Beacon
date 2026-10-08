from __future__ import annotations

import asyncio
import hashlib
import io
import json
import struct
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import httpx
from PIL import Image, ImageOps

from .completed_media import MAX_SOURCE_BYTES, AtomicMediaCache, build_completed_media
from .summarize import build_pipeline, cpu_percent, read_cpu_sample, summarize_history, summarize_queue

CommandRunner = Callable[[list[str]], str]
SystemReader = Callable[[str], str]


def _run(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, timeout=10).strip()


def _read_system(path: str) -> str:
    return Path(path).read_text()


def _gpu_metrics(output: str) -> dict[str, Any]:
    try:
        values = [part.strip() for part in output.splitlines()[0].split(",")]
        return {
            "utilization_percent": int(values[0]),
            "temperature_c": int(values[1]),
            "power_w": float(values[2]),
            "memory_used_mib": int(values[3]),
            "memory_total_mib": int(values[4]),
        }
    except (IndexError, TypeError, ValueError):
        return {}


class ComfyObserver:
    def __init__(
        self,
        comfy_url: str = "http://127.0.0.1:8188",
        http_client: httpx.AsyncClient | None = None,
        command_runner: CommandRunner = _run,
        system_reader: SystemReader = _read_system,
        gpu_manager_path: str | None = None,
        media_cache: AtomicMediaCache | None = None,
        output_root: str | Path = "/srv/ai/data/comfyui/output",
    ) -> None:
        self.comfy_url = comfy_url.rstrip("/")
        self._http = http_client
        self._owns_http = http_client is None
        self._run = command_runner
        self._read_system = system_reader
        self._gpu_manager_path = gpu_manager_path
        self._media_cache = media_cache or AtomicMediaCache()
        self._output_root = Path(output_root)
        self._completed_media: dict[str, Any] | None = None
        self._completed_source_key: str | None = None
        self._completion_sequence = 0
        self._completion_status = "none"
        self._lock = threading.RLock()
        self._queue = {"running_count": 0, "pending_count": 0, "running": [], "pending": []}
        self._history: list[dict[str, Any]] = []
        self._preview: bytes | None = None
        self._preview_sequence = -1
        self._terminal_until_ms = 0
        self._terminal_state: dict[str, Any] | None = None
        self._cpu_sample = None
        self._state: dict[str, Any] = {
            "schema_version": 1,
            "read_only": True,
            "mode": "offline",
            "clock": time.strftime("%H:%M"),
            "updated_at_ms": None,
            "comfy": {"reachable": False, "version": None},
            "managed_gpu": {"state": None, "backend": None},
            "gpu": {},
            "system": {"cpu_percent": None, "ram_used_bytes": None, "ram_total_bytes": None},
            "queue": {"running_count": 0, "pending_count": 0},
            "active": None,
        }
        self._last_error: str | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.comfy_url, timeout=5.0)
        return self._http

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _safe_output_params(self, output: dict[str, Any]) -> tuple[dict[str, str], str] | None:
        """Accept only a resolved file below the configured Comfy output root."""
        filename = output.get("filename")
        subfolder = output.get("subfolder", "")
        output_type = output.get("type", "output")
        if (
            not isinstance(filename, str)
            or not isinstance(subfolder, str)
            or output_type != "output"
            or not filename
            or len(filename) > 255
            or len(subfolder) > 1024
        ):
            return None
        if any(character in filename + subfolder for character in ("\x00", "\\", "?", "#")):
            return None
        filename_path = PurePosixPath(filename)
        folder_path = PurePosixPath(subfolder)
        if (
            filename_path.is_absolute()
            or folder_path.is_absolute()
            or filename_path.name != filename
            or any(part in ("", ".", "..") for part in filename_path.parts + folder_path.parts)
        ):
            return None
        try:
            root = self._output_root.resolve(strict=True)
            candidate = (root / Path(*folder_path.parts) / filename).resolve(strict=True)
            candidate.relative_to(root)
            if not candidate.is_file():
                return None
        except (OSError, RuntimeError, ValueError):
            return None
        normalized_subfolder = "" if subfolder in ("", ".") else folder_path.as_posix()
        source_key = f"output:{normalized_subfolder}:{filename}"
        return {
            "filename": filename,
            "subfolder": normalized_subfolder,
            "type": "output",
        }, source_key

    async def _prepare_completed_media(
        self, client: httpx.AsyncClient, history: list[dict[str, Any]]
    ) -> None:
        terminal = next(
            (item for item in history if item.get("state") == "complete" and isinstance(item.get("output"), dict)),
            None,
        )
        if terminal is None:
            return
        output = terminal["output"]
        prompt_id = terminal.get("prompt_id")
        if not isinstance(prompt_id, str):
            return
        safe_output = self._safe_output_params(output)
        attempt_key = f"{prompt_id}:invalid-output"
        source_key = f"{prompt_id}:{safe_output[1]}" if safe_output else attempt_key
        with self._lock:
            if source_key == self._completed_source_key:
                return
            # Claim every new completion before conversion. A failed conversion
            # publishes a new sequence with no media instead of replaying stale media.
            self._completed_source_key = source_key
            self._completion_sequence += 1
            self._completion_status = "failed"
            self._completed_media = None
        if safe_output is None:
            return
        params, _ = safe_output
        filename = params["filename"]
        kind = terminal.get("media")
        suffix = Path(filename).suffix.lower()
        if kind not in ("image", "video", "audio"):
            if suffix in (".mp4", ".webm", ".mov", ".mkv"):
                kind = "video"
            elif suffix in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
                kind = "audio"
            else:
                kind = "image"
        try:
            source = bytearray()
            async with client.stream("GET", "/view", params=params) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared is not None and int(declared) > MAX_SOURCE_BYTES:
                    raise ValueError("completed source exceeds bound")
                async for chunk in response.aiter_bytes(64 * 1024):
                    source.extend(chunk)
                    if len(source) > MAX_SOURCE_BYTES:
                        raise ValueError("completed source exceeds bound")
            media_id = hashlib.sha256(source_key.encode()).hexdigest()[:16]
            media = await asyncio.to_thread(
                build_completed_media, media_id, kind, bytes(source), suffix
            )
            if self._media_cache.put(media):
                with self._lock:
                    if self._completed_source_key == source_key:
                        self._completed_media = media.descriptor()
                        self._completion_status = "ready"
        except Exception:
            # Completion media is optional; malformed/unavailable outputs must not
            # take the telemetry observer offline.
            return

    async def refresh_once(self) -> None:
        client = await self._client()
        try:
            stats_response, queue_response, history_response = await asyncio.gather(
                client.get("/system_stats"),
                client.get("/queue"),
                client.get("/history", params={"max_items": 10}),
            )
            for response in (stats_response, queue_response, history_response):
                response.raise_for_status()
            stats = stats_response.json()
            queue_summary = summarize_queue(queue_response.json())
            history_summary = summarize_history(history_response.json(), limit=10)
            system = stats.get("system", {}) if isinstance(stats, dict) else {}
            devices = stats.get("devices", []) if isinstance(stats, dict) else []
            device = devices[0] if devices and isinstance(devices[0], dict) else {}

            managed = {}
            if self._gpu_manager_path:
                try:
                    managed = json.loads(
                        await asyncio.to_thread(
                            self._run, [self._gpu_manager_path, "status", "--json"]
                        )
                    )
                except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError):
                    managed = {}
            try:
                gpu = _gpu_metrics(
                    await asyncio.to_thread(
                        self._run,
                        [
                            "nvidia-smi",
                            "--query-gpu=utilization.gpu,temperature.gpu,power.draw,memory.used,memory.total",
                            "--format=csv,noheader,nounits",
                        ]
                    )
                )
            except (subprocess.SubprocessError, OSError, ValueError):
                gpu = {}
            try:
                current_cpu_sample = read_cpu_sample(
                    await asyncio.to_thread(self._read_system, "/proc/stat")
                )
            except (OSError, ValueError, StopIteration):
                current_cpu_sample = None

            active = queue_summary["running"][0] if queue_summary["running"] else None
            next_preview: bytes | None = None
            next_preview_sequence = self._preview_sequence
            try:
                progress_response = await client.get("/render-beacon/v1/progress")
                if progress_response.status_code == 200:
                    companion = progress_response.json()
                    if active and companion.get("prompt_id") == active.get("prompt_id"):
                        running_nodes = [
                            node for node in companion.get("nodes", [])
                            if isinstance(node, dict) and node.get("state") == "running"
                        ]
                        if running_nodes:
                            node = running_nodes[-1]
                            active["node_id"] = node.get("node_id")
                            active["node_class"] = node.get("class_type")
                            active["progress"] = {
                                "scope": "node",
                                "value": node.get("value"),
                                "max": node.get("max"),
                                "percent": node.get("percent"),
                            }
                    sequence = companion.get("preview_sequence")
                    if isinstance(sequence, int) and sequence != self._preview_sequence:
                        preview_response = await client.get("/render-beacon/v1/preview.jpg")
                        if preview_response.status_code == 200 and preview_response.headers.get("content-type", "").startswith("image/jpeg"):
                            next_preview = preview_response.content
                            next_preview_sequence = sequence
            except Exception:
                pass

            await self._prepare_completed_media(client, history_summary)

            with self._lock:
                prior_active = self._state.get("active")
                prior_mode = self._state.get("mode")
                if active and prior_active and prior_active.get("prompt_id") == active.get("prompt_id"):
                    for key in ("node_id", "node_class", "progress", "started_at_ms"):
                        if key in prior_active and key not in active:
                            active[key] = prior_active[key]
                now_ms = int(time.time() * 1000)
                mode = "running" if active else "idle"
                if active:
                    self._terminal_state = None
                    self._terminal_until_ms = 0
                elif prior_mode == "running" and prior_active:
                    terminal = next(
                        (item for item in history_summary if item.get("prompt_id") == prior_active.get("prompt_id")),
                        None,
                    )
                    if terminal:
                        mode = "failed" if terminal.get("state") == "failed" else "complete"
                        active = terminal
                        self._terminal_state = {"mode": mode, "active": deepcopy(terminal)}
                        self._terminal_until_ms = now_ms + 8000
                elif self._terminal_state and now_ms < self._terminal_until_ms:
                    mode = self._terminal_state["mode"]
                    active = deepcopy(self._terminal_state["active"])
                else:
                    self._terminal_state = None
                    self._terminal_until_ms = 0
                if active:
                    prior_pipeline = (
                        prior_active.get("pipeline")
                        if prior_active and isinstance(prior_active.get("pipeline"), dict)
                        else None
                    )
                    if mode == "running":
                        prior_master = (
                            prior_pipeline.get("master_percent", 0)
                            if prior_pipeline and prior_active.get("prompt_id") == active.get("prompt_id")
                            else 0
                        )
                        active["pipeline"] = build_pipeline(active, prior_master)
                        started_at = active.get("started_at_ms") or active.get("created_at_ms")
                        if isinstance(started_at, (int, float)):
                            if started_at < 10_000_000_000:
                                started_at *= 1000
                            active["pipeline"]["elapsed_ms"] = max(0, int(now_ms - started_at))
                    elif prior_pipeline:
                        active["pipeline"] = deepcopy(prior_pipeline)
                        if mode == "complete":
                            active["pipeline"]["master_percent"] = 100
                            active["pipeline"]["active_stage"] = 2
                            for stage in active["pipeline"].get("stages", []):
                                stage["state"] = "complete"
                                stage["percent"] = 100
                measured_cpu = cpu_percent(self._cpu_sample, current_cpu_sample)
                if current_cpu_sample is not None:
                    self._cpu_sample = current_cpu_sample
                ram_total = system.get("ram_total")
                ram_free = system.get("ram_free")
                if not isinstance(ram_total, (int, float)) or not isinstance(ram_free, (int, float)):
                    ram_total = None
                    ram_free = None
                self._queue = queue_summary
                self._history = history_summary
                if next_preview is not None:
                    self._preview = next_preview
                    self._preview_sequence = next_preview_sequence
                self._state.update(
                    {
                        "mode": mode,
                        "clock": time.strftime("%H:%M"),
                        "updated_at_ms": now_ms,
                        "comfy": {
                            "reachable": True,
                            "version": system.get("comfyui_version"),
                            "device": device.get("name"),
                            "vram_total_bytes": device.get("vram_total"),
                            "vram_free_bytes": device.get("vram_free"),
                        },
                        "managed_gpu": {
                            "state": managed.get("state"),
                            "backend": managed.get("backend"),
                            "services": managed.get("services", {}),
                        },
                        "gpu": gpu,
                        "system": {
                            "cpu_percent": measured_cpu,
                            "ram_used_bytes": int(ram_total - ram_free) if ram_total is not None else None,
                            "ram_total_bytes": int(ram_total) if ram_total is not None else None,
                        },
                        "queue": {
                            "running_count": queue_summary["running_count"],
                            "pending_count": queue_summary["pending_count"],
                        },
                        "active": active,
                    }
                )
                self._last_error = None
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            with self._lock:
                self._state.update(
                    {
                        "mode": "offline",
                        "clock": time.strftime("%H:%M"),
                        "updated_at_ms": int(time.time() * 1000),
                        "comfy": {"reachable": False, "version": None},
                        "active": None,
                    }
                )
                self._last_error = str(exc)[:240]

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        with self._lock:
            if event_type == "execution_start":
                self._state["mode"] = "running"
                self._state["active"] = {
                    "prompt_id": data.get("prompt_id"),
                    "started_at_ms": data.get("timestamp", int(time.time() * 1000)),
                    "node_id": None,
                    "progress": None,
                }
            elif event_type == "executing" and self._state.get("active"):
                if data.get("prompt_id") == self._state["active"].get("prompt_id"):
                    self._state["active"]["node_id"] = data.get("node")
                    self._state["active"]["progress"] = None
            elif event_type == "progress" and self._state.get("active"):
                if data.get("prompt_id") == self._state["active"].get("prompt_id"):
                    value = data.get("value")
                    maximum = data.get("max")
                    percent = int(value * 100 / maximum) if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum else None
                    self._state["active"]["node_id"] = data.get("node")
                    self._state["active"]["progress"] = {
                        "scope": "node",
                        "value": value,
                        "max": maximum,
                        "percent": percent,
                    }
            elif event_type in ("execution_success", "execution_error", "execution_interrupted"):
                if self._state.get("active") and data.get("prompt_id") == self._state["active"].get("prompt_id"):
                    self._state["mode"] = {
                        "execution_success": "complete",
                        "execution_error": "failed",
                        "execution_interrupted": "interrupted",
                    }[event_type]
                    if event_type == "execution_error":
                        self._state["active"]["error"] = {
                            "node_id": data.get("node_id"),
                            "type": data.get("exception_type", "ExecutionError"),
                            "message": str(data.get("exception_message", "Execution failed"))[:240],
                        }
            self._state["updated_at_ms"] = int(time.time() * 1000)

    def apply_binary_frame(self, frame: bytes) -> None:
        if len(frame) < 9:
            return
        event_type = struct.unpack(">I", frame[:4])[0]
        if event_type == 1:
            payload = frame[8:]
        elif event_type == 4 and len(frame) >= 8:
            metadata_length = struct.unpack(">I", frame[4:8])[0]
            payload = frame[8 + metadata_length :]
        else:
            return
        try:
            with Image.open(io.BytesIO(payload)) as source:
                image = ImageOps.contain(source.convert("RGB"), (280, 176))
                canvas = Image.new("RGB", (280, 176), (12, 15, 20))
                canvas.paste(image, ((280 - image.width) // 2, (176 - image.height) // 2))
                encoded = io.BytesIO()
                canvas.save(encoded, format="JPEG", quality=72, optimize=True)
            with self._lock:
                self._preview = encoded.getvalue()
        except (OSError, ValueError):
            return

    def health(self) -> dict[str, Any]:
        with self._lock:
            reachable = bool(self._state.get("comfy", {}).get("reachable"))
            result = {"status": "ok" if reachable else "degraded", "comfy_reachable": reachable, "read_only": True}
            if self._last_error:
                result["error"] = self._last_error
            return result

    def state(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def state_v2(self) -> dict[str, Any]:
        with self._lock:
            active_value = self._state.get("active")
            active: dict[str, Any] = active_value if isinstance(active_value, dict) else {}
            queue_value = self._state.get("queue")
            queue: dict[str, Any] = queue_value if isinstance(queue_value, dict) else {}
            model_name = str(active.get("family") or active.get("model") or "Unknown model")[:24]
            if model_name not in {
                "MiniMax H3", "Qwen Image Edit", "Qwen Image", "Z-Image", "Ideogram",
                "Krea", "Flux", "LTX", "Wan", "Unknown model",
            }:
                model_name = "Unknown model"
            modality = active.get("media")
            if modality not in ("image", "video", "audio"):
                modality = "unknown"
            mode = self._state.get("mode", "offline")
            completed = deepcopy(self._completed_media) if mode in ("complete", "idle") else None
            return {
                "schema_version": 2,
                "read_only": True,
                "mode": {"complete": "completed", "failed": "error"}.get(mode, mode),
                "clock": self._state.get("clock"),
                "updated_at_ms": self._state.get("updated_at_ms"),
                "completion_sequence": self._completion_sequence,
                "completion_status": self._completion_status,
                "rail": {
                    "queue": {
                        "running": int(queue.get("running_count") or 0),
                        "pending": int(queue.get("pending_count") or 0),
                    },
                    "model_name": model_name,
                    "modality_icon": modality,
                },
                "pipeline": deepcopy(active.get("pipeline")) if active else None,
                "completed_media": completed,
                "telemetry": {
                    "gpu": deepcopy(self._state.get("gpu", {})),
                    "system": deepcopy(self._state.get("system", {})),
                },
            }

    def media_frame(self, media_id: str, index: int) -> bytes | None:
        if len(media_id) != 16 or any(char not in "0123456789abcdef" for char in media_id):
            return None
        return self._media_cache.frame(media_id, index)

    def queue(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._queue)

    def history(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._history[: max(0, min(limit, 10))])

    def preview(self) -> bytes | None:
        with self._lock:
            return self._preview
