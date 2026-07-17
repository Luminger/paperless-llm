"""One error shape for the whole API.

Every error response body is ``{"detail": {"code", "message", ...}}``.
Routes keep raising plain ``HTTPException(status, "message")`` — the
handler here normalizes; a dict detail may carry an explicit ``code``
and extra context fields.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CODE_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid",
}


def _payload(status_code: int, detail: object) -> dict:
    if isinstance(detail, dict):
        code = detail.get("code") or CODE_BY_STATUS.get(status_code, "error")
        message = detail.get("message") or ""
        extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
        return {"code": code, "message": message, **extra}
    return {
        "code": CODE_BY_STATUS.get(status_code, "error"),
        "message": str(detail),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": _payload(exc.status_code, exc.detail)},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = first.get("msg", "invalid request")
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation",
                    "message": f"{loc}: {msg}" if loc else msg,
                    "errors": jsonable_encoder(errors),
                }
            },
        )
