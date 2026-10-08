from __future__ import annotations

from typing import Any


CpuSample = tuple[int, int]


def read_cpu_sample(stat_text: str) -> CpuSample | None:
    first_line = stat_text.splitlines()[0] if stat_text.splitlines() else ""
    fields = first_line.split()
    if not fields or fields[0] != "cpu":
        return None
    try:
        values = [int(value) for value in fields[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent(previous: CpuSample | None, current: CpuSample | None) -> int | None:
    if previous is None or current is None:
        return None
    idle_delta = current[0] - previous[0]
    total_delta = current[1] - previous[1]
    if total_delta <= 0:
        return None
    busy = max(0, min(total_delta, total_delta - idle_delta))
    return round(busy * 100 / total_delta)


def _bounded_percent(value: Any) -> int:
    if not isinstance(value, (int, float)):
        return 0
    return max(0, min(100, round(value)))


def _pipeline_profile(active: dict[str, Any]) -> str:
    family = str(active.get("family") or "").lower()
    media = str(active.get("media") or "").lower()
    return "h3" if "minimax h3" in family or media == "video" else "t2i"


def _active_stage(profile: str, node_class: Any) -> int:
    lowered = str(node_class or "").lower()
    if profile == "h3":
        if any(marker in lowered for marker in ("save", "combine", "mux", "finish", "videooutput")):
            return 2
        if any(marker in lowered for marker in ("sampler", "generate", "diffusion")):
            return 1
        return 0
    if any(marker in lowered for marker in ("save", "write")):
        return 2
    if any(marker in lowered for marker in ("grid", "contact", "assemble", "composite", "stitch")):
        return 1
    return 0


def build_pipeline(active: dict[str, Any], prior_master_percent: int = 0) -> dict[str, Any]:
    profile = _pipeline_profile(active)
    names = ("Prepare", "Generate", "Finish") if profile == "h3" else ("Generate", "Assemble", "Save")
    weights = (10, 80, 10) if profile == "h3" else (80, 10, 10)
    stage_index = _active_stage(profile, active.get("node_class"))
    progress = active.get("progress") if isinstance(active.get("progress"), dict) else {}
    local_percent = _bounded_percent(progress.get("percent"))
    stages = []
    for index, name in enumerate(names):
        if index < stage_index:
            state, percent = "complete", 100
        elif index == stage_index:
            state, percent = "active", local_percent
        else:
            state, percent = "upcoming", 0
        stages.append({"name": name, "state": state, "percent": percent})
    weighted = sum(weights[:stage_index]) + round(weights[stage_index] * local_percent / 100)
    master_percent = max(_bounded_percent(prior_master_percent), _bounded_percent(weighted))
    frames = active.get("frames")
    fps = active.get("fps")
    duration_seconds = round(frames / fps) if isinstance(frames, (int, float)) and isinstance(fps, (int, float)) and fps > 0 else None
    return {
        "profile": profile,
        "active_stage": stage_index,
        "master_percent": master_percent,
        "step": {"value": progress.get("value"), "max": progress.get("max")},
        "stages": stages,
        "metadata": {
            "model": active.get("family") or active.get("model"),
            "width": active.get("width"),
            "height": active.get("height"),
            "duration_seconds": duration_seconds,
            "fps": fps,
            "steps": active.get("steps"),
        },
    }


MODEL_REGISTRY = (
    ("minimax_h3", "MiniMax H3"),
    ("qwen_image_edit", "Qwen Image Edit"),
    ("qwen_image", "Qwen Image"),
    ("z_image", "Z-Image"),
    ("ideogram", "Ideogram"),
    ("krea", "Krea"),
    ("flux", "Flux"),
    ("ltx", "LTX"),
    ("wan", "Wan"),
)


def normalize_model_name(model: str | None) -> str:
    """Return only a curated display label; unknown checkpoint names fail closed."""
    if not model:
        return "Unknown model"
    lowered = model.lower()
    for marker, label in MODEL_REGISTRY:
        if marker in lowered:
            return label
    return "Unknown model"


def _family(model: str | None) -> str:
    return normalize_model_name(model)


def infer_graph(graph: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model": None,
        "family": None,
        "width": None,
        "height": None,
        "frames": None,
        "fps": None,
        "steps": None,
        "sampler": None,
        "scheduler": None,
        "cfg": None,
        "denoise": None,
        "seed": None,
        "media": "unknown",
        "node_count": len(graph),
    }
    model_keys = ("unet_name", "ckpt_name", "model_name")
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        class_type = str(node.get("class_type", ""))
        for key in model_keys:
            value = inputs.get(key)
            if isinstance(value, str) and not result["model"]:
                result["model"] = value
        for key in ("width", "height", "frames", "fps", "steps", "cfg", "denoise", "seed"):
            value = inputs.get(key)
            if isinstance(value, (int, float)) and result[key] is None:
                result[key] = value
        if result["frames"] is None:
            for key in ("length", "num_frames", "frame_count"):
                value = inputs.get(key)
                if isinstance(value, int):
                    result["frames"] = value
                    break
        if result["sampler"] is None and isinstance(inputs.get("sampler_name"), str):
            result["sampler"] = inputs["sampler_name"]
        if result["scheduler"] is None and isinstance(inputs.get("scheduler"), str):
            result["scheduler"] = inputs["scheduler"]
        lowered = class_type.lower()
        if "video" in lowered or "vhs_" in lowered:
            result["media"] = "video"
        elif "audio" in lowered and result["media"] == "unknown":
            result["media"] = "audio"
        elif ("saveimage" in lowered or "previewimage" in lowered) and result["media"] == "unknown":
            result["media"] = "image"
    result["family"] = _family(result["model"])
    return result


def _queue_item(item: list[Any]) -> dict[str, Any]:
    graph = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
    extra = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
    return {
        "number": item[0] if item else None,
        "prompt_id": item[1] if len(item) > 1 else None,
        "client": extra.get("client_id", "unknown"),
        "created_at_ms": extra.get("create_time"),
        **infer_graph(graph),
    }


def summarize_queue(queue: dict[str, Any], pending_limit: int = 5) -> dict[str, Any]:
    running_raw = queue.get("queue_running") if isinstance(queue.get("queue_running"), list) else []
    pending_raw = queue.get("queue_pending") if isinstance(queue.get("queue_pending"), list) else []
    return {
        "running_count": len(running_raw),
        "pending_count": len(pending_raw),
        "running": [_queue_item(item) for item in running_raw if isinstance(item, list)],
        "pending": [_queue_item(item) for item in pending_raw[: max(0, pending_limit)] if isinstance(item, list)],
    }


def _messages(status: dict[str, Any]) -> list[list[Any]]:
    value = status.get("messages")
    return value if isinstance(value, list) else []


def _event(messages: list[list[Any]], name: str) -> dict[str, Any] | None:
    for item in reversed(messages):
        if isinstance(item, list) and len(item) > 1 and item[0] == name and isinstance(item[1], dict):
            return item[1]
    return None


def _first_output(outputs: dict[str, Any]) -> dict[str, Any] | None:
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for values in node_output.values():
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("filename"), str):
                    return {
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
    return None


def summarize_history(history: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    summarized: list[tuple[int, dict[str, Any]]] = []
    for prompt_id, entry in history.items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        messages = _messages(status)
        start = _event(messages, "execution_start") or {}
        error = _event(messages, "execution_error")
        success = _event(messages, "execution_success")
        interrupted = _event(messages, "execution_interrupted")
        terminal = error or success or interrupted or {}
        start_ms = start.get("timestamp") if isinstance(start.get("timestamp"), int) else None
        end_ms = terminal.get("timestamp") if isinstance(terminal.get("timestamp"), int) else None
        if status.get("status_str") == "error" or error:
            state = "failed"
        elif interrupted:
            state = "interrupted"
        elif status.get("completed") and status.get("status_str") == "success":
            state = "complete"
        else:
            state = "unknown"
        prompt = entry.get("prompt")
        graph = prompt[2] if isinstance(prompt, list) and len(prompt) > 2 and isinstance(prompt[2], dict) else {}
        item: dict[str, Any] = {
            "prompt_id": prompt_id,
            "state": state,
            "started_at_ms": start_ms,
            "finished_at_ms": end_ms,
            "runtime_ms": end_ms - start_ms if start_ms is not None and end_ms is not None else None,
            "output": _first_output(entry.get("outputs", {})) if isinstance(entry.get("outputs"), dict) else None,
            **infer_graph(graph),
        }
        if error:
            item["error"] = {
                "node_id": error.get("node_id"),
                "type": error.get("exception_type", "ExecutionError"),
                "message": str(error.get("exception_message", "Execution failed"))[:240],
            }
        summarized.append((end_ms or start_ms or 0, item))
    summarized.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in summarized[: max(0, limit)]]
