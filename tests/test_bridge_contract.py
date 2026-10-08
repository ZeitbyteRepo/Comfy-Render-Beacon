import json
from pathlib import Path

from fastapi.testclient import TestClient

import render_beacon.summarize as summary
from render_beacon.api import create_app
from render_beacon.observer import ComfyObserver
from render_beacon.summarize import infer_graph, summarize_history, summarize_queue

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def test_infer_graph_reports_render_settings_without_prompt_text():
    graph = load_fixture("queue.json")["queue_running"][0][2]

    summary = infer_graph(graph)

    assert summary["model"] == "z_image_turbo_bf16.safetensors"
    assert summary["family"] == "Z-Image"
    assert summary["width"] == 1024
    assert summary["height"] == 768
    assert summary["steps"] == 9
    assert summary["sampler"] == "euler"
    assert summary["scheduler"] == "simple"
    assert "prompt" not in summary
    assert "private prompt" not in json.dumps(summary)


def test_queue_summary_is_bounded_and_omits_workflow_graphs():
    queue = load_fixture("queue.json")

    summary = summarize_queue(queue, pending_limit=1)

    assert summary["running_count"] == 1
    assert summary["pending_count"] == 2
    assert len(summary["pending"]) == 1
    assert summary["running"][0]["prompt_id"] == "prompt-live-123"
    assert summary["running"][0]["client"] == "image-model-suite"
    encoded = json.dumps(summary)
    assert "class_type" not in encoded
    assert "private prompt" not in encoded


def test_history_marks_completed_error_as_failed_and_reports_runtime():
    history = load_fixture("history.json")

    summary = summarize_history(history, limit=5)

    failed = next(item for item in summary if item["prompt_id"] == "prompt-error")
    succeeded = next(item for item in summary if item["prompt_id"] == "prompt-ok")
    assert failed["state"] == "failed"
    assert failed["error"]["node_id"] == "7"
    assert failed["error"]["type"] == "OutOfMemoryError"
    assert failed["error"]["message"] == "CUDA out of memory while allocating tensor"
    assert succeeded["state"] == "complete"
    assert succeeded["runtime_ms"] == 5100
    assert succeeded["output"]["filename"] == "ok.png"


class StaticObserver:
    def state(self):
        return {"schema_version": 1, "mode": "idle", "read_only": True}

    def queue(self):
        return {"running_count": 0, "pending_count": 0, "running": [], "pending": []}

    def history(self, limit: int):
        return []

    def preview(self):
        return None

    def health(self):
        return {"status": "ok", "comfy_reachable": True, "read_only": True}


def test_api_exposes_only_read_routes():
    client = TestClient(create_app(StaticObserver()))

    assert client.get("/v1/health").json()["read_only"] is True
    assert client.get("/v1/state").json()["mode"] == "idle"
    assert client.get("/v1/queue").status_code == 200
    assert client.get("/v1/history?limit=3").status_code == 200
    assert client.get("/v1/preview.jpg").status_code == 404
    for path in ["/v1/state", "/v1/queue", "/v1/history", "/v1/preview.jpg"]:
        assert client.post(path).status_code == 405
    for forbidden in ["prompt", "interrupt", "free", "clear", "delete", "transition"]:
        assert client.post(f"/v1/{forbidden}").status_code == 404


def test_observer_merges_live_read_only_sources():
    import asyncio
    import httpx

    queue = load_fixture("queue.json")
    queue["queue_running"] = []
    queue["queue_pending"] = []
    history = load_fixture("history.json")

    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/system_stats": {
                "system": {"comfyui_version": "0.33.2"},
                "devices": [{"name": "RTX 3090", "vram_total": 25769803776, "vram_free": 24696061952}],
            },
            "/queue": queue,
            "/history": history,
        }
        return httpx.Response(200, json=payloads[request.url.path])

    def command_runner(command: list[str]) -> str:
        if command[0].endswith("gpu-swap.sh"):
            return json.dumps({"state": "comfy", "backend": None, "services": {"comfy": True}})
        return "7, 38, 63.5, 951, 24576\n"

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
            observer = ComfyObserver(
                http_client=client,
                command_runner=command_runner,
                gpu_manager_path="/test/gpu-swap.sh",
            )
            await observer.refresh_once()
            return observer

    observer = asyncio.run(exercise())
    state = observer.state()
    assert state["schema_version"] == 1
    assert state["read_only"] is True
    assert state["mode"] == "idle"
    assert state["comfy"]["version"] == "0.33.2"
    assert state["managed_gpu"]["state"] == "comfy"
    assert state["gpu"]["utilization_percent"] == 7
    assert state["gpu"]["temperature_c"] == 38
    assert state["gpu"]["power_w"] == 63.5
    assert state["gpu"]["memory_used_mib"] == 951
    assert state["queue"] == {"running_count": 0, "pending_count": 0}
    assert observer.queue()["running_count"] == 0
    assert observer.history(1)[0]["prompt_id"] == "prompt-error"


def test_observer_reports_node_progress_as_node_progress():
    observer = ComfyObserver(command_runner=lambda command: "")
    observer.apply_event({"type": "execution_start", "data": {"prompt_id": "abc"}})
    observer.apply_event({"type": "executing", "data": {"prompt_id": "abc", "node": "17"}})
    observer.apply_event({"type": "progress", "data": {"prompt_id": "abc", "node": "17", "value": 6, "max": 9}})

    state = observer.state()
    assert state["mode"] == "running"
    assert state["active"]["prompt_id"] == "abc"
    assert state["active"]["node_id"] == "17"
    assert state["active"]["progress"] == {"scope": "node", "value": 6, "max": 9, "percent": 66}
    assert "overall_percent" not in state["active"]


def test_qwen_model_family_is_human_readable():
    result = infer_graph({"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2511_fp8mixed.safetensors"}}})
    assert result["family"] == "Qwen Image Edit"


def test_observer_holds_terminal_state_after_queue_transition():
    import asyncio
    import httpx

    running_queue = load_fixture("queue.json")
    idle_queue = {"queue_running": [], "queue_pending": []}
    history_entry = {
        "prompt": [1, "prompt-live-123", running_queue["queue_running"][0][2], {"client_id": "suite"}, ["9"]],
        "outputs": {},
        "status": {"status_str": "success", "completed": True, "messages": [["execution_start", {"prompt_id": "prompt-live-123", "timestamp": 1000}], ["execution_success", {"prompt_id": "prompt-live-123", "timestamp": 4000}]]},
    }
    queue_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal queue_calls
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.33.2"}, "devices": []})
        if request.url.path == "/queue":
            queue_calls += 1
            return httpx.Response(200, json=running_queue if queue_calls == 1 else idle_queue)
        if request.url.path == "/history":
            return httpx.Response(200, json={} if queue_calls <= 1 else {"prompt-live-123": history_entry})
        return httpx.Response(404)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
            observer = ComfyObserver(http_client=client, command_runner=lambda command: "")
            await observer.refresh_once()
            assert observer.state()["mode"] == "running"
            await observer.refresh_once()
            return observer.state()

    state = asyncio.run(exercise())
    assert state["mode"] == "complete"
    assert state["active"]["prompt_id"] == "prompt-live-123"
    assert state["active"]["runtime_ms"] == 3000


def test_binary_preview_is_resized_to_device_jpeg():
    import io
    import struct
    from PIL import Image

    source = io.BytesIO()
    Image.new("RGB", (1024, 768), (80, 20, 180)).save(source, format="PNG")
    frame = struct.pack(">II", 1, 2) + source.getvalue()
    observer = ComfyObserver(command_runner=lambda command: "")

    observer.apply_binary_frame(frame)

    preview = observer.preview()
    assert preview is not None
    image = Image.open(io.BytesIO(preview))
    assert image.format == "JPEG"
    assert image.width <= 280
    assert image.height <= 176


def test_observer_merges_companion_progress_and_preview():
    import asyncio
    import io
    import httpx
    from PIL import Image

    queue = load_fixture("queue.json")
    preview = io.BytesIO()
    Image.new("RGB", (280, 176), "navy").save(preview, format="JPEG")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.33.2"}, "devices": []})
        if request.url.path == "/queue":
            return httpx.Response(200, json=queue)
        if request.url.path == "/history":
            return httpx.Response(200, json={})
        if request.url.path == "/render-beacon/v1/progress":
            return httpx.Response(200, json={"prompt_id": "prompt-live-123", "preview_sequence": 4, "nodes": [{"node_id": "4", "class_type": "KSampler", "state": "running", "value": 6, "max": 9, "percent": 66, "scope": "node"}]})
        if request.url.path == "/render-beacon/v1/preview.jpg":
            return httpx.Response(200, content=preview.getvalue(), headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
            observer = ComfyObserver(http_client=client, command_runner=lambda command: "")
            await observer.refresh_once()
            return observer

    observer = asyncio.run(exercise())
    active = observer.state()["active"]
    assert active["prompt_id"] == "prompt-live-123"
    assert active["node_id"] == "4"
    assert active["node_class"] == "KSampler"
    assert active["progress"] == {"scope": "node", "value": 6, "max": 9, "percent": 66}
    assert observer.preview() == preview.getvalue()


def test_observer_replaces_stale_internal_progress_with_fresh_nested_sampler_progress():
    import asyncio
    import httpx

    queue = load_fixture("queue.json")
    progress_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal progress_calls
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.33.2"}, "devices": []})
        if request.url.path == "/queue":
            return httpx.Response(200, json=queue)
        if request.url.path == "/history":
            return httpx.Response(200, json={})
        if request.url.path == "/render-beacon/v1/progress":
            progress_calls += 1
            node = (
                {"node_id": "1.0.0.5", "class_type": "MiniMaxH3TimelineSegment", "state": "running", "value": 0.0, "max": 1.0, "percent": 0, "scope": "node"}
                if progress_calls == 1
                else {"node_id": "1.0.0.8", "class_type": "KSampler", "state": "running", "value": 6, "max": 8, "percent": 75, "scope": "node"}
            )
            return httpx.Response(200, json={"prompt_id": "prompt-live-123", "nodes": [node]})
        return httpx.Response(404)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
            observer = ComfyObserver(http_client=client, command_runner=lambda command: "")
            await observer.refresh_once()
            assert observer.state()["active"]["progress"]["percent"] == 0
            await observer.refresh_once()
            return observer.state()["active"]

    active = asyncio.run(exercise())
    assert active["node_id"] == "1.0.0.8"
    assert active["node_class"] == "KSampler"
    assert active["progress"] == {"scope": "node", "value": 6, "max": 8, "percent": 75}


def test_slow_command_collection_does_not_block_event_loop():
    import asyncio
    import httpx
    import time

    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/system_stats": {"system": {}, "devices": []},
            "/queue": {"queue_running": [], "queue_pending": []},
            "/history": {},
        }
        if request.url.path in payloads:
            return httpx.Response(200, json=payloads[request.url.path])
        return httpx.Response(404)

    def slow_runner(command: list[str]) -> str:
        time.sleep(0.15)
        if command[0].endswith("gpu-swap.sh"):
            return json.dumps({"state": "comfy", "services": {"comfy": True}})
        return "7, 38, 63.5, 951, 24576\n"

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
            observer = ComfyObserver(http_client=client, command_runner=slow_runner)
            refresh = asyncio.create_task(observer.refresh_once())
            await asyncio.sleep(0.02)
            event_loop_remained_responsive = not refresh.done()
            await refresh
            return event_loop_remained_responsive

    assert asyncio.run(exercise()) is True


def test_h3_pipeline_groups_stages_and_weights_master_progress():
    active = {
        "family": "MiniMax H3",
        "media": "video",
        "width": 1280,
        "height": 720,
        "frames": 144,
        "fps": 24,
        "steps": 8,
        "node_class": "KSampler",
        "progress": {"value": 4, "max": 8, "percent": 50},
    }

    pipeline = summary.build_pipeline(active)

    assert pipeline["profile"] == "h3"
    assert pipeline["active_stage"] == 1
    assert pipeline["master_percent"] == 50
    assert pipeline["step"] == {"value": 4, "max": 8}
    assert pipeline["stages"] == [
        {"name": "Prepare", "state": "complete", "percent": 100},
        {"name": "Generate", "state": "active", "percent": 50},
        {"name": "Finish", "state": "upcoming", "percent": 0},
    ]
    assert pipeline["metadata"] == {
        "model": "MiniMax H3",
        "width": 1280,
        "height": 720,
        "duration_seconds": 6,
        "fps": 24,
        "steps": 8,
    }


def test_t2i_pipeline_uses_generate_assemble_save_weights():
    active = {
        "family": "Flux",
        "media": "image",
        "width": 4096,
        "height": 3072,
        "node_class": "ImageGridComposite",
        "progress": {"value": 6, "max": 10, "percent": 60},
    }

    pipeline = summary.build_pipeline(active)

    assert pipeline["profile"] == "t2i"
    assert pipeline["active_stage"] == 1
    assert pipeline["master_percent"] == 86
    assert [stage["name"] for stage in pipeline["stages"]] == ["Generate", "Assemble", "Save"]
    assert [stage["state"] for stage in pipeline["stages"]] == ["complete", "active", "upcoming"]


def test_pipeline_master_progress_never_moves_backward():
    active = {
        "family": "Flux",
        "media": "image",
        "node_class": "KSampler",
        "progress": {"value": 2, "max": 4, "percent": 50},
    }

    assert summary.build_pipeline(active, prior_master_percent=90)["master_percent"] == 90


def test_cpu_percent_uses_proc_stat_deltas():
    previous = summary.read_cpu_sample("cpu  600 0 0 400 0 0 0 0 0 0\n")
    current = summary.read_cpu_sample("cpu  750 0 0 450 0 0 0 0 0 0\n")

    assert summary.cpu_percent(previous, current) == 75


def test_observer_reports_cpu_and_system_ram_read_only():
    import asyncio
    import httpx

    proc_samples = iter(
        [
            "cpu  600 0 0 400 0 0 0 0 0 0\n",
            "cpu  750 0 0 450 0 0 0 0 0 0\n",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/system_stats": {
                "system": {
                    "comfyui_version": "0.33.2",
                    "ram_total": 68_719_476_736,
                    "ram_free": 34_359_738_368,
                },
                "devices": [],
            },
            "/queue": {"queue_running": [], "queue_pending": []},
            "/history": {},
        }
        if request.url.path in payloads:
            return httpx.Response(200, json=payloads[request.url.path])
        return httpx.Response(404)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
            observer = ComfyObserver(
                http_client=client,
                command_runner=lambda command: "",
                system_reader=lambda path: next(proc_samples),
            )
            await observer.refresh_once()
            await observer.refresh_once()
            return observer.state()

    state = asyncio.run(exercise())
    assert state["read_only"] is True
    assert len(state["clock"]) == 5
    assert state["clock"][2] == ":"
    assert state["system"] == {
        "cpu_percent": 75,
        "ram_used_bytes": 34_359_738_368,
        "ram_total_bytes": 68_719_476_736,
    }
