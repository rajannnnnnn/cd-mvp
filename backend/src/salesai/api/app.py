"""FastAPI application factory. Roles select which routers are mounted: `api` (REST + simulator), `ingress`
(Meta webhooks only), `web` / `all` (both)."""
from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from salesai import __version__
from salesai.api import errors
from salesai.api.hub import ChangeHub
from salesai.api.routes import auth, business, catalog, contacts, conversations, events, metrics, operator, simulator, system, workflow
from salesai.obs import bind
from salesai.runtime import Runtime


def create_app(rt: Runtime | None = None, role: str = "web", *, owns_runtime: bool = False) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = rt or await Runtime.create()
        app.state.rt = runtime
        if role in ("api", "web", "all"):
            runtime.hub = ChangeHub(runtime.settings.system_database_url)
            await runtime.hub.start()
        try:
            yield
        finally:
            if getattr(runtime, "hub", None):
                await runtime.hub.stop()
            if owns_runtime or rt is None:
                await runtime.close()

    app = FastAPI(title="WhatsApp AI Sales Assistant API", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  description="Versioned REST API. The tenant is always derived from the authenticated session; floor prices are write-only.")
    if rt is not None:
        app.state.rt = rt
        if not hasattr(rt, "hub"):
            rt.hub = ChangeHub(rt.settings.system_database_url)
    settings_origins = (rt.settings.origins if rt else None)
    if settings_origins is None:
        from salesai.config import get_settings
        settings_origins = get_settings().origins
    app.add_middleware(CORSMiddleware, allow_origins=settings_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-Id"], max_age=600)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        with bind(request_id=rid):
            resp = await call_next(request)
        resp.headers["X-Request-Id"] = rid
        resp.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - t0) * 1000:.1f}"
        return resp

    errors.install(app)
    app.include_router(system.system)
    if role in ("ingress", "web", "all"):
        app.include_router(system.webhooks)
    if role in ("api", "web", "all"):
        v1 = "/api/v1"
        app.include_router(system.public, prefix=v1)
        for r in (auth.router, business.router, catalog.router, contacts.router, conversations.router, workflow.router, metrics.router, events.router, simulator.router, operator.router):
            app.include_router(r, prefix=v1)
    return app
