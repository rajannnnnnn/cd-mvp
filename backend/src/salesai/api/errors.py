"""Consistent error format for the whole API: {"error": {"code", "message", "details"}} (Technical Design)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import errors as pgerr

from salesai.modules.auth import AuthError

log = logging.getLogger("salesai.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(body(e.code, e.message, e.details), status_code=e.status)

    @app.exception_handler(AuthError)
    async def _auth(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse(body(e.code, e.message), status_code=e.status, headers={"WWW-Authenticate": "Bearer"} if e.status == 401 else None)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in x["loc"][1:]), "message": x["msg"]} for x in e.errors()]
        return JSONResponse(body("validation_error", "Some fields are invalid.", details), status_code=422)

    @app.exception_handler(LookupError)
    async def _lookup(_: Request, e: LookupError) -> JSONResponse:
        return JSONResponse(body("not_found", str(e) or "Not found."), status_code=404)

    @app.exception_handler(ValueError)
    async def _value(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse(body("invalid_request", str(e)), status_code=422)

    @app.exception_handler(pgerr.UniqueViolation)
    async def _unique(_: Request, e: pgerr.UniqueViolation) -> JSONResponse:
        return JSONResponse(body("conflict", "That already exists."), status_code=409)

    @app.exception_handler(pgerr.ForeignKeyViolation)
    async def _fk(_: Request, e: pgerr.ForeignKeyViolation) -> JSONResponse:
        return JSONResponse(body("invalid_reference", "A referenced item does not exist."), status_code=422)

    @app.exception_handler(pgerr.CheckViolation)
    async def _check(_: Request, e: pgerr.CheckViolation) -> JSONResponse:
        return JSONResponse(body("invalid_request", "A value is outside the allowed range."), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(body("internal_error", "Something went wrong on our side."), status_code=500)
