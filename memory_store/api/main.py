"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..config import load_settings
from ..errors import (
    ConfigError,
    EmbeddingDimensionMismatchError,
    IngestionError,
    LifecycleError,
    MemoryNotFoundError,
    MemoryStoreError,
    PersistenceError,
    PrivacyError,
    RetrievalError,
    SchemaMismatchError,
    ValidationError,
)
from ..store import MemoryStore
from .routes import register_routes


def create_app(
    config_path: str | Path | None = None,
    *,
    store: Any | None = None,
    **overrides: Any,
) -> Any:
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Request
        from fastapi.responses import JSONResponse
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("fastapi is required to run the REST adapter.") from exc

    settings = load_settings(config_path, **overrides)
    managed_store = store if store is not None else MemoryStore(settings=settings)

    @asynccontextmanager
    async def lifespan(_app: Any) -> Any:
        try:
            yield
        finally:
            closer = getattr(managed_store, "close", None)
            if callable(closer):
                closer()

    app = FastAPI(title="Memory Store", version="0.1.0", lifespan=lifespan)

    def register_exception(
        exception_type: type[Exception],
        status_code: int,
    ) -> None:
        @app.exception_handler(exception_type)
        async def handle_exception(_request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    register_exception(MemoryNotFoundError, 404)
    register_exception(PrivacyError, 403)
    register_exception(ConfigError, 422)
    register_exception(ValidationError, 422)
    register_exception(EmbeddingDimensionMismatchError, 422)
    register_exception(SchemaMismatchError, 409)
    register_exception(LifecycleError, 409)
    register_exception(IngestionError, 422)
    register_exception(PersistenceError, 500)
    register_exception(RetrievalError, 500)
    register_exception(MemoryStoreError, 500)

    route_dependencies: list[Any] = []
    if settings.api.local_api_token:
        expected_token = settings.api.local_api_token

        def require_local_api_token(
            x_api_token: str | None = Header(default=None, alias="X-API-Token"),
        ) -> None:
            if x_api_token != expected_token:
                raise HTTPException(status_code=401, detail="Invalid API token.")

        route_dependencies.append(Depends(require_local_api_token))

    register_routes(app, managed_store, dependencies=route_dependencies)
    return app
