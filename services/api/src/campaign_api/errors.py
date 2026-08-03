import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from campaign_contracts.enums import ErrorComponent
from campaign_contracts.errors import ErrorEnvelope, SanitizedWorkflowError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from campaign_api.dependencies import correlation_id_var
from campaign_api.exceptions import CampaignAPIError


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "service": "campaign-api",
                "event": record.getMessage(),
                "correlation_id": str(correlation_id_var.get()) if correlation_id_var.get() else None,
            }
        )


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def _error(code: str, message: str, component: ErrorComponent, retryable: bool, status_code: int) -> JSONResponse:
    cid = correlation_id_var.get() or uuid4()
    error = SanitizedWorkflowError(
        code=code,
        message=message,
        component=component,
        workflow_step=None,
        attempt=1,
        retryable=retryable,
        timestamp=datetime.now(UTC),
        correlation_id=cid,
    )
    return JSONResponse(
        status_code=status_code,
        content=ErrorEnvelope(error=error).model_dump(mode="json"),
        headers={"X-Request-ID": str(cid)},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error("VALIDATION_ERROR", "Request validation failed.", ErrorComponent.FASTAPI, False, 422)

    @app.exception_handler(CampaignAPIError)
    async def domain_error(_: Request, exc: CampaignAPIError) -> JSONResponse:
        return _error(exc.code, str(exc), ErrorComponent.FASTAPI, exc.retryable, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("unexpected_internal_failure")
        return _error("INTERNAL_ERROR", "Unexpected internal error.", ErrorComponent.FASTAPI, False, 500)
