from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI, HTTPException, Query, Response


class Observer(Protocol):
    def health(self) -> dict: ...
    def state(self) -> dict: ...
    def queue(self) -> dict: ...
    def history(self, limit: int) -> list: ...
    def preview(self) -> bytes | None: ...
    def state_v2(self) -> dict: ...
    def media_frame(self, media_id: str, index: int) -> bytes | None: ...
    def media_thumbnail(self, media_id: str) -> bytes | None: ...


def create_app(observer: Observer) -> FastAPI:
    app = FastAPI(title="Render Beacon Bridge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/v1/health")
    def health() -> dict:
        return observer.health()

    @app.get("/v1/state")
    def state() -> dict:
        return observer.state()

    @app.get("/v1/queue")
    def queue() -> dict:
        return observer.queue()

    @app.get("/v1/history")
    def history(limit: int = Query(default=5, ge=1, le=10)) -> list:
        return observer.history(limit)

    @app.get("/v1/preview.jpg")
    def preview() -> Response:
        payload = observer.preview()
        if payload is None:
            raise HTTPException(status_code=404, detail="No preview available")
        return Response(payload, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/v2/state")
    def state_v2() -> dict:
        return observer.state_v2()

    @app.get("/v2/media/{media_id}/frame/{index}.jpg")
    def media_frame(media_id: str, index: int) -> Response:
        if index < 0 or index > 23:
            raise HTTPException(status_code=404, detail="Media frame not found")
        payload = observer.media_frame(media_id, index)
        if payload is None:
            raise HTTPException(status_code=404, detail="Media frame not found")
        return Response(
            payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/v2/media/{media_id}/thumb.jpg")
    def media_thumbnail(media_id: str) -> Response:
        payload = observer.media_thumbnail(media_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Media thumbnail not found")
        return Response(
            payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    return app
