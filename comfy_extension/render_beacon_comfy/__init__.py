"""Read-only Render Beacon companion extension for ComfyUI.

The extension adds two loopback-only GET routes and stores at most one preview
image in memory. It does not define execution nodes or state-changing routes.
"""

from __future__ import annotations

import threading
from typing import Any

from .core import encode_preview, is_loopback_peer, serialize_progress

NODE_CLASS_MAPPINGS: dict[str, Any] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = "./web"

_preview_lock = threading.Lock()
_preview_image = None
_preview_sequence = 0
_registered = False


def _capture_preview(image_tuple: Any) -> None:
    global _preview_image, _preview_sequence
    if not image_tuple or len(image_tuple) < 2:
        return
    image = image_tuple[1]
    try:
        copied = image.copy()
    except Exception:
        return
    with _preview_lock:
        _preview_image = copied
        _preview_sequence += 1


def _register() -> None:
    global _registered
    if _registered:
        return

    from aiohttp import web
    from comfy_execution.progress import ProgressRegistry, get_progress_state
    from server import PromptServer

    original = getattr(ProgressRegistry.update_progress, "_render_beacon_original", None)
    if original is None:
        original = ProgressRegistry.update_progress

        def update_with_beacon(self, node_id, value, max_value, image=None):
            result = original(self, node_id, value, max_value, image)
            if image is not None:
                _capture_preview(image)
            return result

        update_with_beacon._render_beacon_original = original
        ProgressRegistry.update_progress = update_with_beacon

    def allowed(request) -> bool:
        return is_loopback_peer(request.remote)

    @PromptServer.instance.routes.get("/render-beacon/v1/progress")
    async def render_beacon_progress(request):
        if not allowed(request):
            raise web.HTTPForbidden(text="loopback only")
        try:
            payload = serialize_progress(get_progress_state())
        except RuntimeError:
            payload = serialize_progress(get_progress_state())
        with _preview_lock:
            payload["preview_sequence"] = _preview_sequence
        return web.json_response(payload)

    @PromptServer.instance.routes.get("/render-beacon/v1/preview.jpg")
    async def render_beacon_preview(request):
        if not allowed(request):
            raise web.HTTPForbidden(text="loopback only")
        with _preview_lock:
            image = _preview_image.copy() if _preview_image is not None else None
            sequence = _preview_sequence
        if image is None:
            raise web.HTTPNotFound(text="no preview")
        payload = encode_preview(image)
        return web.Response(
            body=payload,
            content_type="image/jpeg",
            headers={"Cache-Control": "no-store", "ETag": f'"{sequence}"'},
        )

    _registered = True


try:
    _register()
except ImportError:
    # Allows pure security/serialization tests outside a ComfyUI process.
    pass
