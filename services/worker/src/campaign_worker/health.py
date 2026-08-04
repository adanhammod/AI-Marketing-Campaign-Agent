from typing import Protocol

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel, ConfigDict

from .config import Settings


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    service: str
    environment: str


class AvailabilityCheck(Protocol):
    async def available(self) -> bool: ...


def build_health_app(consumer: AvailabilityCheck, settings: Settings) -> FastAPI:
    app = FastAPI(title="Campaign Worker Health", version="0.1.0")

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(status="ok", service=settings.service_name, environment=settings.environment)

    @app.get(
        "/health/ready", response_model=HealthResponse, tags=["health"], responses={503: {"model": HealthResponse}}
    )
    async def ready() -> HealthResponse | JSONResponse:
        ok = await consumer.available()
        body = HealthResponse(
            status="ready" if ok else "unavailable", service=settings.service_name, environment=settings.environment
        )
        return body if ok else JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump())

    @app.get("/metrics", tags=["metrics"])
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(REGISTRY))

    return app
