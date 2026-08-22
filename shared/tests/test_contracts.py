import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID
import pytest
from pydantic import ValidationError
from campaign_contracts.api import CampaignCreationRequest, RevisionRequest
from campaign_contracts.artifacts import ArtifactReference
from campaign_contracts.campaign import CampaignAggregateMetadata, CampaignVersion, assert_version_immutable, earliest_regeneration_step, validate_approval_target
from campaign_contracts.dynamodb import approval_sk,event_sk,meta_sk,pk,serialize_event,serialize_meta,serialize_step,serialize_version,step_sk,version_sk
from campaign_contracts.enums import CampaignStatus, RevisionTarget, WorkflowStep
from campaign_contracts.events import CampaignEvent, validate_event_progress
from campaign_contracts.schema_generation import SCHEMAS, generate
from campaign_contracts.sqs import SQSJobMessage, duplicate_delivery_key
from campaign_contracts.validation import TRANSITIONS,is_cancellable_state,is_legal_transition,is_retryable_state,is_terminal,is_user_action_transition,validate_transition
ROOT=Path(__file__).parents[1]; VALID=ROOT/'fixtures'/'valid'; INVALID=ROOT/'fixtures'/'invalid'
def load(path): return json.loads(path.read_text(encoding='utf-8-sig'))
@pytest.mark.parametrize('name',['new-campaign','queued-campaign','strategy-in-progress','image-generation','ready-for-review','approved-campaign','final-campaign','failed-retryable','failed-non-retryable','cancelled-campaign','revision-version-2'])
def test_campaign_fixtures(name): CampaignVersion.model_validate(load(VALID/f'{name}.json'))
@pytest.mark.parametrize('name',['sqs-start','sqs-resume','sqs-regenerate','sqs-duplicate-delivery'])
def test_valid_sqs(name): SQSJobMessage.model_validate(load(VALID/f'{name}.json'))
@pytest.mark.parametrize('name',['sqs-unknown-schema','sqs-missing-job-id','sqs-invalid-uuid','sqs-invalid-operation','sqs-invalid-version'])
def test_invalid_sqs(name):
    with pytest.raises(ValidationError): SQSJobMessage.model_validate(load(INVALID/f'{name}.json'))
def test_duplicate_delivery_key():
    a=SQSJobMessage.model_validate(load(VALID/'sqs-start.json')); b=SQSJobMessage.model_validate(load(VALID/'sqs-duplicate-delivery.json')); assert duplicate_delivery_key(a)==duplicate_delivery_key(b)
def test_api_fixtures():
    CampaignCreationRequest.model_validate(load(VALID/'api-create.json')); RevisionRequest.model_validate(load(VALID/'api-revision.json'))
    for model,name in [(CampaignCreationRequest,'api-create'),(RevisionRequest,'api-revision')]:
        with pytest.raises(ValidationError): model.model_validate(load(INVALID/f'{name}.json'))
def test_artifact_and_events():
    ArtifactReference.model_validate(load(VALID/'artifacts.json')[0]); events=[CampaignEvent.model_validate(x) for x in load(VALID/'events.json')]; validate_event_progress(events)
    with pytest.raises(ValidationError): ArtifactReference.model_validate(load(INVALID/'artifact-persistent-url-expiry.json'))
    bad=events.copy(); bad[1]=bad[1].model_copy(update={'progress_percent':0}); validate_event_progress(bad)
    bad[1]=bad[1].model_copy(update={'progress_percent':-1});
    with pytest.raises(ValueError): validate_event_progress(bad)
def test_transitions():
    assert is_legal_transition(CampaignStatus.CREATED,CampaignStatus.QUEUED); validate_transition(CampaignStatus.CREATED,CampaignStatus.QUEUED)
    with pytest.raises(ValueError): validate_transition(CampaignStatus.FINAL,CampaignStatus.QUEUED)
    assert is_terminal(CampaignStatus.FINAL) and is_terminal(CampaignStatus.FAILED); assert not is_terminal(CampaignStatus.APPROVED)
    assert is_user_action_transition(CampaignStatus.READY_FOR_REVIEW,CampaignStatus.APPROVED); assert is_cancellable_state(CampaignStatus.QUEUED); assert is_retryable_state(CampaignStatus.FAILED)
def test_approval_immutability_regeneration():
    v=CampaignVersion.model_validate(load(VALID/'ready-for-review.json')); now=datetime(2026,7,28,tzinfo=timezone.utc); agg=CampaignAggregateMetadata(campaign_id=v.campaign_id,current_version=1,title='x',created_at=now,updated_at=now,lock_version=1)
    validate_approval_target(agg,v); wrong=agg.model_copy(update={'current_version':2})
    with pytest.raises(ValueError): validate_approval_target(wrong,v)
    final=CampaignVersion.model_validate(load(VALID/'final-campaign.json')); changed=final.model_copy(update={'progress_percent':99})
    with pytest.raises(ValueError): assert_version_immutable(final,changed)
    ready_changed=v.model_copy(update={'brief':v.brief.model_copy(update={'tone':'serious'})})
    with pytest.raises(ValueError): assert_version_immutable(v,ready_changed)
    assert earliest_regeneration_step(RevisionTarget.SELECTED_IMAGES)==WorkflowStep.IMAGES

def test_cancellation_fields_round_trip():
    v=CampaignVersion.model_validate(load(VALID/'queued-campaign.json'))
    now=datetime(2026,7,28,tzinfo=timezone.utc)
    cancelled=v.model_copy(update={'status':CampaignStatus.CANCELLED,'cancellation_reason':'User stopped generation','cancelled_at':now})
    assert cancelled.cancellation_reason=='User stopped generation' and cancelled.cancelled_at==now
    assert v.cancellation_reason is None and v.cancelled_at is None

def test_public_error_rejects_unsafe_fields():
    from campaign_contracts.errors import SanitizedWorkflowError
    payload={"schema_version":1,"code":"INTERNAL_ERROR","message":"safe","component":"UNKNOWN","workflow_step":None,"attempt":1,"retryable":False,"timestamp":"2026-07-28T09:00:00Z","correlation_id":"018f0000-0000-7000-8000-000000000004","raw_provider_body":"secret"}
    with pytest.raises(ValidationError): SanitizedWorkflowError.model_validate(payload)