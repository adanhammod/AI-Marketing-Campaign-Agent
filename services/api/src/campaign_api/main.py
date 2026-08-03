from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

from campaign_api.config import Settings
from campaign_api.dependencies import correlation_id_var, get_queue, get_repository
from campaign_api.errors import configure_logging, register_error_handlers
from campaign_api.queue.factory import create_job_queue
from campaign_api.queue.job_queue import JobQueue
from campaign_api.repositories.campaign_repository import CampaignRepository
from campaign_api.repositories.in_memory_campaign_repository import InMemoryCampaignRepository
from campaign_api.routers.campaigns import router as campaigns_router


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    service: str
    environment: str


def create_app(
    settings: Settings | None = None, repository: CampaignRepository | None = None, queue: JobQueue | None = None
) -> FastAPI:
    resolved = settings or Settings.from_env()
    configure_logging(resolved.log_level)
    app = FastAPI(title="Campaign Control Plane", version="0.1.0")
    app.state.settings = resolved
    app.state.repository = repository or InMemoryCampaignRepository()
    app.state.queue = queue or create_job_queue(resolved)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        raw = request.headers.get("X-Request-ID")
        try:
            cid = UUID(raw) if raw else uuid4()
        except ValueError:
            cid = uuid4()
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = str(cid)
            return response
        finally:
            correlation_id_var.reset(token)

    register_error_handlers(app)
    app.include_router(campaigns_router, prefix=resolved.api_prefix)

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(status="ok", service=resolved.service_name, environment=resolved.environment)

    @app.get(
        "/health/ready", response_model=HealthResponse, tags=["health"], responses={503: {"model": HealthResponse}}
    )
    async def ready(
        repo: Annotated[CampaignRepository, Depends(get_repository)], jobs: Annotated[JobQueue, Depends(get_queue)]
    ) -> HealthResponse | JSONResponse:
        ok = await repo.available() and await jobs.available()
        body = HealthResponse(
            status="ready" if ok else "unavailable", service=resolved.service_name, environment=resolved.environment
        )
        return body if ok else JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump())

    return app


app = create_app()
