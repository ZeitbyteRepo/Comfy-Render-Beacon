from __future__ import annotations

import asyncio
import io
import json
import struct
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import httpx
from PIL import Image, ImageOps

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
    ) -> None:
        self.comfy_url = comfy_url.rstrip("/")
        self._http = http_client
        self._owns_http = http_client is None
        self._run = command_runner
        self._read_system = system_reader
        self._gpu_manager_path = gpu_manager_path
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

    def queue(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._queue)

    def history(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._history[: max(0, min(limit, 10))])

    def preview(self) -> bytes | None:
        with self._lock:
            return self._preview
