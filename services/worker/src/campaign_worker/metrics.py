"""Prometheus metric definitions for the worker.

Labels are intentionally restricted to small, bounded enum values (message outcome,
terminal campaign status) -- never campaign_id/job_id/correlation_id, which would make
these series unbounded and defeat Prometheus's storage/query model.
"""

from prometheus_client import Counter, Histogram

JOBS_PROCESSED_TOTAL = Counter(
    "worker_jobs_processed_total",
    "SQS job messages processed by the worker, by outcome",
    ["outcome"],
)

JOBS_FAILED_TOTAL = Counter(
    "worker_jobs_failed_total",
    "Campaigns terminally marked FAILED or CANCELLED by the worker",
    ["status"],
)

JOB_PROCESSING_DURATION_SECONDS = Histogram(
    "worker_job_processing_duration_seconds",
    "Wall-clock time spent running the LangGraph workflow for a single SQS message",
)
