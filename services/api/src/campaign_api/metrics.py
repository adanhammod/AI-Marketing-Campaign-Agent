"""Prometheus metric definitions and the request-instrumentation middleware for the API.

Labels are the HTTP method, the *matched route template* (e.g.
`/api/v1/campaigns/{campaign_id}`), and the response status code -- never the raw
request path, which would leak campaign/job IDs into label values and make these
series unbounded.
"""

import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from prometheus_client import Counter, Histogram
from starlette.responses import Response

UNMATCHED_PATH_LABEL = "/unmatched"

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests handled by the API",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)


def route_path_template(request: Request) -> str:
    """The matched route's path pattern, or a bounded fallback for unmatched requests.

    request.scope["route"] is only populated once Starlette's router has matched a
    route; a request that 404s (no route matched) never gets one. Falling back to
    request.url.path here would put the raw, attacker- or client-controlled path --
    potentially containing a UUID or arbitrary junk -- straight into a Prometheus
    label, which is exactly the unbounded-cardinality problem labels must avoid.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_PATH_LABEL


async def metrics_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    started_at = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - started_at
    path = route_path_template(request)
    HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, path=path).observe(duration)
    HTTP_REQUESTS_TOTAL.labels(method=request.method, path=path, status=str(response.status_code)).inc()
    return response
