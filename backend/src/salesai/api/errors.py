"""Consistent error format for the whole API: {"error": {"code", "message", "details"}} (Technical Design)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import errors as pgerr

from salesai.modules.auth import AuthError
from salesai.modules.billing import BillingError

log = logging.getLogger("salesai.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


_LABELS = {"list_price": "Price", "range_min": "Lowest in range", "range_max": "Highest in range", "floor_price": "Lowest price", "value": "Amount",
           "name": "Name", "round_to": "Round to", "stock_qty": "Stock count", "concession_steps": "Steps down", "phone": "Phone number", "subject": "Title",
           "message": "Message", "body": "Message", "owner_name": "Your name", "slug": "Shop address", "code": "Code"}
_PLAIN = {"Input should be greater than 0": "must be more than 0", "Input should be greater than or equal to 0": "can't be negative",
          "Field required": "is required", "Decimal input should have no more than 2 decimal places": "can have at most 2 decimal places"}


def _label(loc: Any) -> str:
    leaf = str(loc[-1]) if loc else "Value"
    return _LABELS.get(leaf, leaf.replace("_", " ").capitalize())


def _plain(msg: str, kind: str) -> str:
    msg = msg.removeprefix("Value error, ")
    if msg in _PLAIN:
        return _PLAIN[msg]
    if "no more than" in msg and "digits" in msg:
        return "is too large"
    if kind in ("decimal_parsing", "decimal_type", "int_parsing", "int_type"):
        return "must be a number such as 1200 or 1200.50"
    if kind == "string_too_short":
        return "can't be empty"
    return msg[:1].lower() + msg[1:] if msg else "is not valid"


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(body(e.code, e.message, e.details), status_code=e.status)

    @app.exception_handler(AuthError)
    async def _auth(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse(body(e.code, e.message), status_code=e.status, headers={"WWW-Authenticate": "Bearer"} if e.status == 401 else None)

    @app.exception_handler(BillingError)
    async def _billing(_: Request, e: BillingError) -> JSONResponse:
        return JSONResponse(body(e.code, e.message), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in x["loc"][1:]), "label": _label(x["loc"]), "message": _plain(x["msg"], x.get("type", ""))} for x in e.errors()]
        if not details:
            message = "Some fields are invalid."
        elif len(details) == 1:
            message = f"{details[0]['label']} {details[0]['message']}"
        else:
            more = f" (and {len(details) - 3} more)" if len(details) > 3 else ""
            message = "Please fix: " + "; ".join(f"{d['label']} {d['message']}" for d in details[:3]) + more
        return JSONResponse(body("validation_error", message, details), status_code=422)

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
