"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bootstrap.container import ApplicationContainer
from ..bootstrap.lifespan import build_lifespan
from ..config import load_settings
from ..errors import (
    AuthenticationError,
    AuthorizationError,
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
from ..observability import InMemoryMetricsRecorder, NoopMetricsRecorder, configure_logging
from ..store import MemoryStore
from .routes import register_routes
from .security import ApiAuthorizer, ApiContextMiddleware, RequestBodyLimitMiddleware, build_api_error_response


def create_app(
    config_path: str | Path | None = None,
    *,
    store: Any | None = None,
    **overrides: Any,
) -> Any:
    """Create the REST shim app with Swagger and OpenAPI routes enabled."""

    return _create_app(
        config_path,
        store=store,
        enable_docs=True,
        **overrides,
    )


def create_inprocess_app(
    config_path: str | Path | None = None,
    *,
    store: Any | None = None,
    **overrides: Any,
) -> Any:
    """Create an in-process REST app without Swagger and OpenAPI routes."""

    return _create_app(
        config_path,
        store=store,
        enable_docs=False,
        **overrides,
    )


def _create_app(
    config_path: str | Path | None = None,
    *,
    store: Any | None = None,
    enable_docs: bool,
    **overrides: Any,
) -> Any:
    try:
        from fastapi import FastAPI, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("fastapi is required to run the REST adapter.") from exc

    settings_overrides = dict(overrides)
    if enable_docs:
        api_overrides = dict(settings_overrides.get("api") or {})
        api_overrides["docs_enabled"] = True
        settings_overrides["api"] = api_overrides

    settings = load_settings(config_path, **settings_overrides)
    redactor = configure_logging(settings.logging)
    container: ApplicationContainer | None = None
    if store is None:
        container = ApplicationContainer(settings=settings, api_mode=True)
        managed_store = MemoryStore(container=container)
    else:
        managed_store = store
        candidate_container = getattr(store, "_container", None)
        if isinstance(candidate_container, ApplicationContainer):
            candidate_container.api_mode = True
            container = candidate_container

    app = FastAPI(
        title="ByteBox",
        version="0.1.0",
        docs_url="/docs" if enable_docs and settings.api.docs_enabled else None,
        redoc_url="/redoc" if enable_docs and settings.api.docs_enabled else None,
        openapi_url="/openapi.json" if enable_docs and settings.api.docs_enabled else None,
        lifespan=build_lifespan(managed_store=managed_store, container=container),
    )
    app.add_middleware(ApiContextMiddleware, settings=settings.api)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.api.max_request_body_bytes,
    )
    app.state.bytebox_container = container
    app.state.bytebox_metrics = (
        InMemoryMetricsRecorder() if settings.logging.metrics_enabled else NoopMetricsRecorder()
    )
    app.state.bytebox_redactor = redactor
    app.state.bytebox_store = managed_store
    app.state.bytebox_settings = settings

    def register_exception(
        exception_type: type[Exception],
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        @app.exception_handler(exception_type)
        async def handle_exception(_request: Request, exc: Exception) -> JSONResponse:
            del exc
            return build_api_error_response(
                _request,
                status_code=status_code,
                code=code,
                message=message,
            )

    register_exception(
        AuthenticationError,
        401,
        "BYTEBOX_UNAUTHORIZED",
        "Authentication is required for this operation.",
    )
    register_exception(
        AuthorizationError,
        403,
        "BYTEBOX_FORBIDDEN",
        "The caller is not authorized for this operation.",
    )
    register_exception(
        MemoryNotFoundError,
        404,
        "BYTEBOX_RESOURCE_NOT_FOUND",
        "The requested resource was not found.",
    )
    register_exception(
        PrivacyError,
        403,
        "BYTEBOX_PRIVACY_RESTRICTED",
        "The requested privacy operation is not allowed.",
    )
    register_exception(
        ConfigError,
        422,
        "BYTEBOX_CONFIG_INVALID",
        "The request conflicts with the current ByteBox configuration.",
    )
    register_exception(
        ValidationError,
        422,
        "BYTEBOX_INVALID_REQUEST",
        "The request payload was not accepted.",
    )
    register_exception(
        EmbeddingDimensionMismatchError,
        422,
        "BYTEBOX_EMBEDDING_DIMENSION_MISMATCH",
        "The embedding payload does not match the configured model contract.",
    )
    register_exception(
        SchemaMismatchError,
        409,
        "BYTEBOX_SCHEMA_MISMATCH",
        "The ByteBox schema is not compatible with the current request.",
    )
    register_exception(
        LifecycleError,
        409,
        "BYTEBOX_LIFECYCLE_CONFLICT",
        "The requested operation is not valid in the current lifecycle state.",
    )
    register_exception(
        IngestionError,
        422,
        "BYTEBOX_INGESTION_FAILED",
        "The ingestion operation could not be completed.",
    )
    register_exception(
        PersistenceError,
        500,
        "BYTEBOX_PERSISTENCE_FAILED",
        "The storage operation could not be completed.",
    )
    register_exception(
        RetrievalError,
        500,
        "BYTEBOX_RETRIEVAL_FAILED",
        "The retrieval operation could not be completed.",
    )
    register_exception(
        MemoryStoreError,
        500,
        "BYTEBOX_INTERNAL_ERROR",
        "The ByteBox operation could not be completed.",
    )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        return build_api_error_response(
            request,
            status_code=422,
            code="BYTEBOX_INVALID_REQUEST",
            message="The request payload was not accepted.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        mapping = {
            401: ("BYTEBOX_UNAUTHORIZED", "Authentication is required for this operation."),
            403: ("BYTEBOX_FORBIDDEN", "The caller is not authorized for this operation."),
            404: ("BYTEBOX_ROUTE_NOT_FOUND", "The requested API route was not found."),
        }
        code, message = mapping.get(
            exc.status_code,
            ("BYTEBOX_HTTP_ERROR", "The HTTP request could not be completed."),
        )
        return build_api_error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=message,
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        del exc
        return build_api_error_response(
            request,
            status_code=500,
            code="BYTEBOX_INTERNAL_ERROR",
            message="The ByteBox operation could not be completed.",
        )

    authorizer = ApiAuthorizer(settings.api)
    register_routes(app, managed_store, authorizer=authorizer, settings=settings)
    return app
