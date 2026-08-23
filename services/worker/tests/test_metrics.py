from datetime import UTC, datetime
from uuid import uuid4

import pytest
from campaign_contracts.enums import CampaignStatus, SQSOperation

from campaign_worker import metrics
from campaign_worker.consumer.sqs_consumer import MessageOutcome, SQSConsumer
from campaign_worker.services.job_processor import JobProcessor, NoOpJobProcessor, ProcessingResult
from test_consumer import FakeRepository, StubSQS, job, raw, settings
from test_job_processor import (
    _AlwaysFailsImageProvider,
    _lease,
    _message,
    _processor,
    _version,
)


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _histogram_sample_count(histogram) -> tuple[float, float]:
    samples = {sample.name: sample.value for sample in histogram.collect()[0].samples}
    return samples[f"{histogram._name}_sum"], samples[f"{histogram._name}_count"]


@pytest.mark.asyncio
async def test_jobs_processed_total_counts_by_outcome():
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "worker-a")

    before = _counter_value(metrics.JOBS_PROCESSED_TOTAL, outcome="acknowledged")
    outcome = await consumer.process_raw(raw(value))
    after = _counter_value(metrics.JOBS_PROCESSED_TOTAL, outcome="acknowledged")

    assert outcome == MessageOutcome.ACKNOWLEDGED
    assert after == before + 1


@pytest.mark.asyncio
async def test_jobs_processed_total_counts_invalid_messages():
    before = _counter_value(metrics.JOBS_PROCESSED_TOTAL, outcome="invalid")
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "worker-a")

    outcome = await consumer.process_raw(raw(value, body="not-json"))
    after = _counter_value(metrics.JOBS_PROCESSED_TOTAL, outcome="invalid")

    assert outcome == MessageOutcome.INVALID
    assert after == before + 1


@pytest.mark.asyncio
async def test_job_processing_duration_recorded_around_processor_call():
    value = job()
    queue = StubSQS()
    repository = FakeRepository(value)
    consumer = SQSConsumer(queue, repository, NoOpJobProcessor(), settings(), "worker-a")

    before_sum, before_count = _histogram_sample_count(metrics.JOB_PROCESSING_DURATION_SECONDS)
    await consumer.process_raw(raw(value))
    after_sum, after_count = _histogram_sample_count(metrics.JOB_PROCESSING_DURATION_SECONDS)

    assert after_count == before_count + 1
    assert after_sum >= before_sum


@pytest.mark.asyncio
async def test_jobs_failed_total_counts_failed_status():
    before = _counter_value(metrics.JOBS_FAILED_TOTAL, status="failed")
    repository, processor = _processor(image_provider=_AlwaysFailsImageProvider())
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    after = _counter_value(metrics.JOBS_FAILED_TOTAL, status="failed")
    assert result.completed is True
    assert after == before + 1


@pytest.mark.asyncio
async def test_jobs_failed_total_counts_cancelled_status():
    async def always_cancelled() -> bool:
        return True

    before = _counter_value(metrics.JOBS_FAILED_TOTAL, status="cancelled")
    repository, processor = _processor(is_cancelled=always_cancelled)
    version = _version()
    message = _message(SQSOperation.START, version.campaign_id, version.job_id)

    result = await processor.process(message, version, _lease())

    after = _counter_value(metrics.JOBS_FAILED_TOTAL, status="cancelled")
    assert result.completed is True
    assert after == before + 1
