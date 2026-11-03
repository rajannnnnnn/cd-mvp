"""FastAPI application factory. Roles select which routers are mounted: `api` (the shop-facing REST + simulator), `ingress`
(Meta webhooks only), `admin` (the founder's console: its own sign-in and the operator routes, nothing of the shop app),
`web` (api + ingress) and `all` (everything, for development). The shop-facing application has no admin surface at all
(ADR 0028): a compromise of it cannot reach the console, because the console's routes are not there."""
from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from salesai import __version__
from salesai.api import errors
from salesai.api.deps import admin_gate
from salesai.api.hub import ChangeHub
from salesai.api.routes import (
    admin_auth,
    analytics,
    auth,
    billing,
    boards,
    business,
    catalog,
    contacts,
    conversations,
    events,
    metrics,
    onboarding,
    operator,
    pricing,
    simulator,
    support,
    system,
    workflow,
)
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
    cfg = rt.settings if rt else None
    if cfg is None:
        from salesai.config import get_settings
        cfg = get_settings()
    # the admin application answers cross-origin calls from the console's own origin only, never from the shop app's
    settings_origins = cfg.origins if role != "admin" else cfg.admin_origins
    app.add_middleware(CORSMiddleware, allow_origins=settings_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-Id"], max_age=600)

    if role == "admin":
        @app.middleware("http")
        async def admin_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            resp = await call_next(request)
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "no-referrer"
            resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            return resp

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
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
        for r in (auth.router, business.router, catalog.router, contacts.router, conversations.router, workflow.router, metrics.router, events.router, pricing.router, simulator.router, onboarding.router, billing.router, analytics.router, support.router):
            app.include_router(r, prefix=v1)
    if role in ("admin", "all"):
        app.include_router(admin_auth.router, dependencies=[Depends(admin_gate)])
        app.include_router(operator.router, prefix="/api/v1", dependencies=[Depends(admin_gate)])
        app.include_router(boards.router, prefix="/api/v1", dependencies=[Depends(admin_gate)])
    return app
