import asyncio
import io
import inspect
from pathlib import Path

import httpx
from PIL import Image

from render_beacon import completed_media
from render_beacon.observer import ComfyObserver


def _png_bytes() -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (32, 32), "navy").save(payload, "PNG")
    return payload.getvalue()


def _terminal(prompt_id: str, filename: str, subfolder: str = "", output_type: str = "output"):
    return [
        {
            "prompt_id": prompt_id,
            "state": "complete",
            "media": "image",
            "output": {"filename": filename, "subfolder": subfolder, "type": output_type},
        }
    ]


def test_view_source_is_output_only_and_resolved_below_configured_root(tmp_path: Path):
    root = tmp_path / "output"
    nested = root / "safe"
    nested.mkdir(parents=True)
    (nested / "frame.png").write_bytes(_png_bytes())
    outside = tmp_path / "secret.png"
    outside.write_bytes(_png_bytes())
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)

    observer = ComfyObserver(command_runner=lambda command: "", output_root=root)
    assert observer._safe_output_params(
        {"filename": "frame.png", "subfolder": "safe", "type": "output"}
    ) == (
        {"filename": "frame.png", "subfolder": "safe", "type": "output"},
        "output:safe:frame.png",
    )

    rejected = [
        {"filename": "/etc/passwd", "subfolder": "", "type": "output"},
        {"filename": "../secret.png", "subfolder": "", "type": "output"},
        {"filename": "secret.png", "subfolder": "..", "type": "output"},
        {"filename": "secret.png", "subfolder": "escape", "type": "output"},
        {"filename": "frame.png", "subfolder": "safe", "type": "temp"},
        {"filename": "frame.png?type=temp", "subfolder": "safe", "type": "output"},
        {"filename": "frame.png", "subfolder": "https://host", "type": "output"},
    ]
    for output in rejected:
        assert observer._safe_output_params(output) is None


def test_view_request_has_only_fixed_safe_query_fields(tmp_path: Path):
    root = tmp_path / "output"
    (root / "safe").mkdir(parents=True)
    (root / "safe" / "frame.png").write_bytes(_png_bytes())
    seen_queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(dict(request.url.params))
        return httpx.Response(200, content=_png_bytes())

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://comfy"
        ) as client:
            observer = ComfyObserver(
                http_client=client, command_runner=lambda command: "", output_root=root
            )
            await observer._prepare_completed_media(
                client, _terminal("prompt-1", "frame.png", "safe")
            )
            return observer

    observer = asyncio.run(exercise())
    assert seen_queries == [
        {"filename": "frame.png", "subfolder": "safe", "type": "output"}
    ]
    assert observer.state_v2()["completion_status"] == "ready"


def test_failed_new_conversion_clears_old_media_and_is_not_replayed(tmp_path: Path):
    root = tmp_path / "output"
    root.mkdir()
    (root / "good.png").write_bytes(_png_bytes())
    (root / "bad.png").write_bytes(b"not an image")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        filename = request.url.params["filename"]
        requests.append(filename)
        return httpx.Response(
            200, content=_png_bytes() if filename == "good.png" else b"not an image"
        )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://comfy"
        ) as client:
            observer = ComfyObserver(
                http_client=client, command_runner=lambda command: "", output_root=root
            )
            observer._state["mode"] = "complete"
            await observer._prepare_completed_media(client, _terminal("prompt-1", "good.png"))
            first = observer.state_v2()
            await observer._prepare_completed_media(client, _terminal("prompt-2", "bad.png"))
            failed = observer.state_v2()
            await observer._prepare_completed_media(client, _terminal("prompt-2", "bad.png"))
            repeated = observer.state_v2()
            return first, failed, repeated

    first, failed, repeated = asyncio.run(exercise())
    assert first["completion_sequence"] == 1
    assert first["completion_status"] == "ready"
    assert first["completed_media"] is not None
    assert failed["completion_sequence"] == 2
    assert failed["completion_status"] == "failed"
    assert failed["completed_media"] is None
    assert repeated["completion_sequence"] == 2
    assert repeated["completed_media"] is None
    assert requests == ["good.png", "bad.png"]


def test_audio_derivative_streams_pcm_with_explicit_bounds():
    source = inspect.getsource(completed_media.audio_derivative)
    assert "subprocess.Popen" in source
    assert "os.read" in source
    assert "MAX_AUDIO_SAMPLES" in source
    assert "AUDIO_DECODE_TIMEOUT_SECONDS" in source
    assert "capture_output=True" not in source
    assert ".stdout" not in source.replace("process.stdout", "")
