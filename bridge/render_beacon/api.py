from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI, HTTPException, Query, Response


class Observer(Protocol):
    def health(self) -> dict: ...
    def state(self) -> dict: ...
    def queue(self) -> dict: ...
    def history(self, limit: int) -> list: ...
    def preview(self) -> bytes | None: ...


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

    return app
