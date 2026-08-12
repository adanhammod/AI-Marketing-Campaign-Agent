import asyncio
from datetime import UTC, datetime
from uuid import uuid4, uuid5

import pytest
from campaign_contracts.api import RevisionResponse
from campaign_contracts.artifacts import AudioArtifactReference, FinalPackageArtifactReference, ImageArtifactReference
from campaign_contracts.campaign import (
    CampaignCopy,
    ChannelCopy,
    ImagePrompt,
    ReviewPackage,
    Storyboard,
    StoryboardScene,
    StrategyOutput,
)
from campaign_contracts.enums import CampaignStatus, SQSOperation, WorkflowStep
from fastapi.testclient import TestClient

from campaign_api.dependencies import get_queue
from campaign_api.exceptions import QueueSubmissionAmbiguousFailure, QueueSubmissionFailure, RepositoryFailure
from campaign_api.queue.in_memory_job_queue import InMemoryJobQueue


def _revise(client, campaign_id, version, headers, reason="Make the CTA more direct", scope="COPY", artifact_ids=None):
    body = {"reason": reason, "scope": scope, "affected_artifact_ids": artifact_ids or []}
    return client.post(f"/api/v1/campaigns/{campaign_id}/versions/{version}/revisions", json=body, headers=headers)


def _regenerate_job_id(campaign_id, new_version):
    return uuid5(campaign_id, f"REGENERATE:{new_version}")


def _strategy():
    return StrategyOutput(
        audience="Professionals",
        positioning="Premium",
        objective="Drive signups",
        key_message="Try it now",
        channel_rationale={"instagram": "high engagement"},
    )


def _copy():
    return CampaignCopy(
        headline="Cold brew, delivered",
        caption="Fresh every week",
        call_to_action="Subscribe today",
        hashtags=["#coldbrew"],
        channel_variants=[
            ChannelCopy(
                channel="instagram",
                headline="Cold brew",
                caption="Fresh",
                call_to_action="Subscribe",
                hashtags=["#coldbrew"],
            )
        ],
    )


def _storyboard():
    scenes = [
        StoryboardScene(
            scene_number=1, purpose="hook", duration_seconds=5, narration="n1", visual_prompt="v1", transition="cut"
        ),
        StoryboardScene(
            scene_number=2, purpose="body", duration_seconds=5, narration="n2", visual_prompt="v2", transition="cut"
        ),
        StoryboardScene(
            scene_number=3, purpose="cta", duration_seconds=5, narration="n3", visual_prompt="v3", transition="cut"
        ),
    ]
    return Storyboard(scenes=scenes, total_duration_seconds=15)


def _image_prompts():
    return [ImagePrompt(scene_number=n, prompt=f"prompt {n}") for n in (1, 2, 3)]


def _image_artifacts(campaign_id, campaign_version, now):
    return [
        ImageArtifactReference(
            artifact_id=uuid4(),
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            workflow_step=WorkflowStep.IMAGES,
            mime_type="image/png",
            size_bytes=1024,
            checksum_sha256="a" * 64,
            created_at=now,
        )
        for _ in range(3)
    ]


def _voice_artifact(campaign_id, campaign_version, now):
    return AudioArtifactReference(
        artifact_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        workflow_step=WorkflowStep.VOICEOVER,
        mime_type="audio/mpeg",
        size_bytes=4096,
        checksum_sha256="b" * 64,
        created_at=now,
        provider="polly",
    )


NON_READY_FOR_REVIEW_STATUSES = [
    CampaignStatus.CREATED,
    CampaignStatus.QUEUED,
    CampaignStatus.GENERATING_STRATEGY,
    CampaignStatus.GENERATING_COPY,
    CampaignStatus.GENERATING_STORYBOARD,
    CampaignStatus.GENERATING_IMAGES,
    CampaignStatus.RENDERING_VIDEO,
    CampaignStatus.APPROVED,
    CampaignStatus.FINAL,
    CampaignStatus.FAILED,
    CampaignStatus.REVISION_REQUESTED,
    CampaignStatus.CANCELLED,
]


def test_revision_from_ready_for_review_returns_202_and_creates_version_2(
    client, repository, queue, campaign_at_status, headers
):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW, strategy=_strategy()))

    response = _revise(client, campaign_id, 1, headers, scope="COPY")

    assert response.status_code == 202
    body = RevisionResponse.model_validate(response.json())
    assert body.parent_version == 1
    assert body.campaign_version == 2
    assert body.status == CampaignStatus.QUEUED
    assert body.earliest_affected_step == WorkflowStep.COPY
    expected_job_id = _regenerate_job_id(campaign_id, 2)
    assert body.job_id == expected_job_id

    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].campaign_version == 2
    assert record[1].parent_version == 1
    assert record[1].status == CampaignStatus.QUEUED
    assert record[1].current_step == WorkflowStep.COPY
    assert record[1].job_id == expected_job_id
    assert record[1].revision is not None
    assert record[1].revision.scope == "COPY"

    messages = queue.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.REGENERATE
    assert messages[0].job_id == expected_job_id
    assert messages[0].requested_step == WorkflowStep.COPY
    assert messages[0].revision_scope == "COPY"


def test_revision_freezes_the_parent_version_unchanged_except_status(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW, strategy=_strategy()))
    before = asyncio.run(repository.get(campaign_id))
    assert before is not None
    parent_before = before[1]

    response = _revise(client, campaign_id, 1, headers, scope="COPY")
    assert response.status_code == 202

    parent_after = asyncio.run(repository.get_version(campaign_id, 1))
    assert parent_after is not None
    assert parent_after.status == CampaignStatus.REVISION_REQUESTED
    # Every other field is byte-identical to the pre-revision parent -- "v1 remains unchanged".
    assert parent_after.model_dump(exclude={"status", "lock_version", "updated_at"}) == parent_before.model_dump(
        exclude={"status", "lock_version", "updated_at"}
    )
    assert parent_after.lock_version == parent_before.lock_version + 1


@pytest.mark.parametrize("status", NON_READY_FOR_REVIEW_STATUSES)
def test_revision_from_every_non_ready_for_review_status_rejected(client, campaign_at_status, headers, status):
    campaign_id, _ = asyncio.run(campaign_at_status(status))

    response = _revise(client, campaign_id, 1, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_revision_campaign_not_found(client, headers):
    response = _revise(client, uuid4(), 1, headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_revision_stale_version_conflicts(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    response = _revise(client, campaign_id, 2, headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_revision_identical_replay_creates_no_second_version_and_zero_writes(
    client, repository, queue, campaign_at_status, headers
):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    first = _revise(client, campaign_id, 1, headers, reason="same reason", scope="COPY")

    calls = 0
    original = repository.revise

    async def counting_revise(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    repository.revise = counting_revise
    second = _revise(client, campaign_id, 1, headers, reason="same reason", scope="COPY")
    repository.revise = original

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert calls == 0  # replay never re-invokes the repository write
    messages = queue.messages()
    assert len(messages) == 1  # deterministic job_id de-duplicates in the fake queue too
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None and record[1].campaign_version == 2  # no accidental N+2


def test_revision_replay_after_child_is_no_longer_queued_is_rejected_not_silently_resubmitted(
    client, repository, queue, campaign_at_status, headers
):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    first = _revise(client, campaign_id, 1, headers, reason="same reason", scope="COPY")
    assert first.status_code == 202

    # Simulate the worker fully processing and finalizing the child (v2) before the client's
    # retried duplicate POST /revisions arrives -- a legitimate race under at-least-once HTTP retry.
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    aggregate, child = record
    assert child.campaign_version == 2
    finalized_child = child.model_copy(
        update={"status": CampaignStatus.FINAL, "job_id": uuid5(campaign_id, "RESUME:2")}
    )
    asyncio.run(repository.replace_current(aggregate, finalized_child))

    second = _revise(client, campaign_id, 1, headers, reason="same reason", scope="COPY")

    assert second.status_code == 409
    messages = queue.messages()
    assert len(messages) == 1  # no new REGENERATE message resubmitted against the finalized version
    record_after = asyncio.run(repository.get(campaign_id))
    assert record_after is not None
    assert record_after[1].status == CampaignStatus.FINAL  # unchanged -- proves no illegal reopening


def test_revision_conflicting_replay_returns_409_idempotency_conflict(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    assert _revise(client, campaign_id, 1, headers, reason="original reason", scope="COPY").status_code == 202

    response = _revise(client, campaign_id, 1, headers, reason="different reason entirely", scope="COPY")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


class _FailOnceQueue:
    """Wraps a real InMemoryJobQueue but raises on the first submit() call only,
    simulating a definite send failure followed by a successful client retry."""

    def __init__(self, failure: Exception):
        self._inner = InMemoryJobQueue()
        self._failure = failure
        self.calls = 0

    async def submit(self, message):
        self.calls += 1
        if self.calls == 1:
            raise self._failure
        return await self._inner.submit(message)

    async def available(self):
        return True

    def messages(self):
        return self._inner.messages()


def test_revision_queue_failure_then_replay_succeeds(app, repository, campaign_at_status, headers):
    fail_once = _FailOnceQueue(QueueSubmissionFailure("simulated failure"))
    app.dependency_overrides[get_queue] = lambda: fail_once
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    test_client = TestClient(app, raise_server_exceptions=False)

    first = _revise(test_client, campaign_id, 1, headers, reason="r", scope="COPY")
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].campaign_version == 2  # durable, no rollback
    assert record[1].status == CampaignStatus.QUEUED

    second = _revise(test_client, campaign_id, 1, headers, reason="r", scope="COPY")
    assert second.status_code == 202
    messages = fail_once.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.REGENERATE


def test_revision_ambiguous_queue_failure_then_replay_succeeds(app, repository, campaign_at_status, headers):
    fail_once = _FailOnceQueue(QueueSubmissionAmbiguousFailure("outcome unknown"))
    app.dependency_overrides[get_queue] = lambda: fail_once
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    test_client = TestClient(app, raise_server_exceptions=False)

    first = _revise(test_client, campaign_id, 1, headers, reason="r", scope="COPY")
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None and record[1].campaign_version == 2

    second = _revise(test_client, campaign_id, 1, headers, reason="r", scope="COPY")
    assert second.status_code == 202
    assert len(fail_once.messages()) == 1


def test_revision_vs_approve_mutual_exclusion(client, campaign_at_status, approval_checksum, headers):
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW,
            review_package=ReviewPackage(
                artifact_id=uuid4(), manifest_checksum=approval_checksum, artifact_ids=[uuid4()]
            ),
        )
    )
    approve_response = client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/1/approve",
        json={"review_manifest_checksum": approval_checksum, "note": None},
        headers=headers,
    )
    assert approve_response.status_code == 202

    revise_response = _revise(client, campaign_id, 1, headers)

    assert revise_response.status_code == 409
    assert revise_response.json()["error"]["code"] == "STATE_CONFLICT"


def test_revision_vs_cancel_mutual_exclusion(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    cancel_response = client.post(
        f"/api/v1/campaigns/{campaign_id}/versions/1/cancel", json={"reason": "no longer needed"}, headers=headers
    )
    assert cancel_response.status_code == 200

    revise_response = _revise(client, campaign_id, 1, headers)

    assert revise_response.status_code == 409
    assert revise_response.json()["error"]["code"] == "STATE_CONFLICT"


def test_revision_correct_deterministic_job_id(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    response = _revise(client, campaign_id, 1, headers, scope="COPY")

    assert response.json()["job_id"] == str(_regenerate_job_id(campaign_id, 2))


def test_revision_strategy_scope_reuses_nothing(client, repository, queue, campaign_at_status, headers):
    now_fields = {"strategy": _strategy(), "copy": _copy(), "storyboard": _storyboard()}
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW, **now_fields))

    response = _revise(client, campaign_id, 1, headers, scope="STRATEGY")

    assert response.status_code == 202
    assert response.json()["earliest_affected_step"] == "strategy"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    child = record[1]
    assert child.strategy is None
    assert child.campaign_copy is None
    assert child.storyboard is None
    assert child.completed_steps == []
    messages = queue.messages()
    assert messages[0].requested_step == WorkflowStep.STRATEGY
    assert messages[0].revision_scope == "STRATEGY"


def test_revision_copy_scope_reuses_strategy_only(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW, strategy=_strategy(), copy=_copy(), storyboard=_storyboard()
        )
    )

    response = _revise(client, campaign_id, 1, headers, scope="COPY")

    assert response.status_code == 202
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    child = record[1]
    assert child.strategy == _strategy()
    assert child.campaign_copy is None
    assert child.storyboard is None
    assert child.completed_steps == [WorkflowStep.STRATEGY]


def test_revision_storyboard_scope_reuses_strategy_and_copy(client, repository, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW, strategy=_strategy(), copy=_copy(), storyboard=_storyboard()
        )
    )

    response = _revise(client, campaign_id, 1, headers, scope="STORYBOARD")

    assert response.status_code == 202
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    child = record[1]
    assert child.strategy == _strategy()
    assert child.campaign_copy == _copy()
    assert child.storyboard is None
    assert child.completed_steps == [WorkflowStep.STRATEGY, WorkflowStep.COPY]


def test_revision_video_scope_reuses_strategy_copy_storyboard_images_and_voice(
    client, repository, campaign_at_status, headers
):
    now = datetime.now(UTC)
    images = _image_artifacts(uuid4(), 1, now)
    voice = _voice_artifact(uuid4(), 1, now)
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW,
            strategy=_strategy(),
            copy=_copy(),
            storyboard=_storyboard(),
            image_prompts=_image_prompts(),
            image_artifacts=images,
            voice_artifact=voice,
        )
    )

    response = _revise(client, campaign_id, 1, headers, scope="VIDEO")

    assert response.status_code == 202
    assert response.json()["earliest_affected_step"] == "video"
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    child = record[1]
    assert child.strategy == _strategy()
    assert child.campaign_copy == _copy()
    assert child.storyboard == _storyboard()
    assert child.image_artifacts == images
    assert child.image_prompts == _image_prompts()
    # A VIDEO-only revision must not re-trigger Polly synthesis: narration is unchanged.
    assert child.voice_artifact == voice
    assert child.video_artifact is None
    assert child.completed_steps == [
        WorkflowStep.STRATEGY,
        WorkflowStep.COPY,
        WorkflowStep.STORYBOARD,
        WorkflowStep.IMAGES,
        WorkflowStep.VOICEOVER,
    ]


def test_revision_storyboard_scope_does_not_reuse_voice_artifact(client, repository, campaign_at_status, headers):
    now = datetime.now(UTC)
    voice = _voice_artifact(uuid4(), 1, now)
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW,
            strategy=_strategy(),
            copy=_copy(),
            storyboard=_storyboard(),
            voice_artifact=voice,
        )
    )

    response = _revise(client, campaign_id, 1, headers, scope="STORYBOARD")

    assert response.status_code == 202
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    child = record[1]
    # STORYBOARD drives narration, so the previous voiceover must not carry forward.
    assert child.voice_artifact is None


def test_revision_does_not_inherit_approval_review_package_error_cancellation_fields(
    client, repository, campaign_at_status, approval_checksum, headers
):
    campaign_id, _ = asyncio.run(
        campaign_at_status(
            CampaignStatus.READY_FOR_REVIEW,
            strategy=_strategy(),
            review_package=ReviewPackage(
                artifact_id=uuid4(), manifest_checksum=approval_checksum, artifact_ids=[uuid4()]
            ),
            package_artifact=FinalPackageArtifactReference(
                artifact_id=uuid4(),
                campaign_id=uuid4(),
                campaign_version=1,
                workflow_step=WorkflowStep.PACKAGE,
                mime_type="application/zip",
                size_bytes=100,
                checksum_sha256="a" * 64,
                created_at=datetime.now(UTC),
            ),
        )
    )

    response = _revise(client, campaign_id, 1, headers, scope="COPY")

    assert response.status_code == 202
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    child = record[1]
    assert child.approval is None
    assert child.review_package is None
    assert child.package_artifact is None
    assert child.error is None
    assert child.cancellation_reason is None
    assert child.cancelled_at is None
    assert child.retry.attempt == 0
    assert child.retry.retryable is False
    assert child.lock_version == 0
    assert child.checkpoint_version == 0


def test_revision_selected_images_accepted_at_api_layer(client, repository, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(
        campaign_at_status(CampaignStatus.READY_FOR_REVIEW, strategy=_strategy(), copy=_copy())
    )
    artifact_id = uuid4()

    response = _revise(client, campaign_id, 1, headers, scope="SELECTED_IMAGES", artifact_ids=[str(artifact_id)])

    assert response.status_code == 202
    body = RevisionResponse.model_validate(response.json())
    assert body.campaign_version == 2
    assert body.earliest_affected_step == WorkflowStep.IMAGES
    record = asyncio.run(repository.get(campaign_id))
    assert record is not None
    assert record[1].status == CampaignStatus.QUEUED
    messages = queue.messages()
    assert len(messages) == 1
    assert messages[0].operation == SQSOperation.REGENERATE
    assert messages[0].revision_scope == "SELECTED_IMAGES"
    assert messages[0].requested_step == WorkflowStep.IMAGES


def test_revision_selected_images_requires_artifact_ids(client, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))

    response = _revise(client, campaign_id, 1, headers, scope="SELECTED_IMAGES", artifact_ids=[])

    assert response.status_code == 422


def test_revision_persistence_failure_returns_503(app, repository, queue, campaign_at_status, headers):
    campaign_id, _ = asyncio.run(campaign_at_status(CampaignStatus.READY_FOR_REVIEW))
    original = repository.revise

    async def fail_revise(*args, **kwargs):
        raise RepositoryFailure("simulated persistence failure")

    repository.revise = fail_revise
    test_client = TestClient(app, raise_server_exceptions=False)
    response = _revise(test_client, campaign_id, 1, headers)
    repository.revise = original

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert queue.messages() == []
