from __future__ import annotations

import asyncio
import contextlib
import os

from .api import create_app
from .observer import ComfyObserver

observer = ComfyObserver(
    comfy_url=os.getenv("RENDER_BEACON_COMFY_URL", "http://127.0.0.1:8188"),
    gpu_manager_path=os.getenv("RENDER_BEACON_GPU_MANAGER_PATH") or None,
)
app = create_app(observer)


async def polling_loop() -> None:
    while True:
        await observer.refresh_once()
        await asyncio.sleep(0.5)


@app.on_event("startup")
async def start_observer() -> None:
    app.state.polling_task = asyncio.create_task(polling_loop(), name="render-beacon-observer")


@app.on_event("shutdown")
async def stop_observer() -> None:
    task = getattr(app.state, "polling_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await observer.close()
