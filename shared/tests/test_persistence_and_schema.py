import json
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from pydantic import HttpUrl
from campaign_contracts.artifacts import ArtifactAttribution,ImageArtifactReference
from campaign_contracts.campaign import CampaignAggregateMetadata,CampaignVersion,CreativeVideoPlan,VideoShot
from campaign_contracts.dynamodb import approval_sk,event_sk,meta_sk,pk,serialize_event,serialize_meta,serialize_step,serialize_version,step_sk,version_sk,_ddb
from campaign_contracts.enums import AssetRole,CameraMotion,CampaignStatus,ShotRole,StepStatus,TransitionType,WorkflowStep
from campaign_contracts.events import CampaignEvent
from campaign_contracts.schema_generation import SCHEMAS,generate
from campaign_contracts.steps import WorkflowStepRecord
ROOT=Path(__file__).parents[1]
def load(name):return json.loads((ROOT/'fixtures'/'valid'/name).read_text(encoding='utf-8-sig'))
def test_keys_and_serialization(tmp_path):
    cid=UUID('018f0000-0000-7000-8000-000000000001'); eid=UUID('018f0000-0000-7000-8000-000000000031'); assert pk(cid)==f'CAMPAIGN#{cid}'; assert meta_sk()=='META'; assert version_sk(2)=='VERSION#2'; assert step_sk(2,WorkflowStep.COPY)=='STEP#2#copy'; assert event_sk(12,eid).startswith('EVENT#00000000000000000012#'); assert approval_sk(2)=='APPROVAL#2'
    with pytest.raises(ValueError):version_sk(0)
    with pytest.raises(ValueError):event_sk(0,eid)
    version=CampaignVersion.model_validate(load('queued-campaign.json')); item=serialize_version(version); assert item['SK']=='VERSION#1'; assert isinstance(item['campaign_version'],Decimal); assert not any(isinstance(x,float) for x in item.values())
    now=datetime(2026,7,28,tzinfo=timezone.utc); meta=CampaignAggregateMetadata(campaign_id=cid,current_version=1,title='x',created_at=now,updated_at=now,lock_version=1); assert serialize_meta(meta)['SK']=='META'
    step_record=WorkflowStepRecord(campaign_id=cid,campaign_version=1,step=WorkflowStep.COPY,status=StepStatus.SUCCEEDED,attempt=1,created_at=now,updated_at=now); assert serialize_step(step_record)['attempt']==Decimal(1) and serialize_step(step_record)['SK']=='STEP#1#copy'
    with pytest.raises(TypeError):_ddb(1.2)
def test_event_serialize_and_schema_stability(tmp_path):
    event=CampaignEvent.model_validate(load('events.json')[0]); assert serialize_event(event)['SK'].startswith('EVENT#'); first=generate(tmp_path); snapshots={p.name:p.read_bytes() for p in first}; second=generate(tmp_path); assert snapshots=={p.name:p.read_bytes() for p in second}; assert len(first)==len(SCHEMAS)
def test_ddb_converts_http_url_to_string():
    converted=_ddb(HttpUrl('https://www.pexels.com/@creator'))
    assert converted=='https://www.pexels.com/@creator'
    assert isinstance(converted,str)
def _video_shot(duration_seconds):
    return VideoShot(shot_number=1,role=ShotRole.HOOK,source_scene_number=1,asset_role=AssetRole.HERO_PRODUCT,visual_description='hero crop',duration_seconds=duration_seconds,camera_motion=CameraMotion.STATIC,transition_in=TransitionType.CUT)
def test_serialize_version_with_creative_video_plan_produces_no_raw_floats():
    # Regression test for a real E2E failure: CREATIVE_PLAN succeeds, then the
    # very next save_version() crashes with "floats are forbidden in DynamoDB
    # contracts" because VideoShot.duration_seconds was a float. Every other
    # duration field in this codebase is int; this is the one place a real
    # (non-whole-second) shot duration needs to round-trip through DynamoDB.
    version=CampaignVersion.model_validate(load('queued-campaign.json'))
    plan=CreativeVideoPlan(concept='c',visual_style='modern',total_duration_seconds=15,shots=[_video_shot(15)])
    updated=version.model_copy(update={'creative_video_plan':plan})
    item=serialize_version(updated)
    shot_duration=item['creative_video_plan']['shots'][0]['duration_seconds']
    assert isinstance(shot_duration,Decimal)
    assert shot_duration==Decimal(15)
def test_serialize_version_converts_nested_http_url_fields():
    version=CampaignVersion.model_validate(load('queued-campaign.json'))
    attribution=ArtifactAttribution(provider_asset_id='123',creator_name='Creator',creator_profile_url='https://www.pexels.com/@creator',source_page_url='https://www.pexels.com/photo/example-123/',provider_url='https://www.pexels.com',attribution_text='Photo by Creator on Pexels')
    artifact=ImageArtifactReference(artifact_id=uuid4(),campaign_id=version.campaign_id,campaign_version=version.campaign_version,workflow_step=WorkflowStep.IMAGES,mime_type='image/jpeg',size_bytes=42,checksum_sha256='a'*64,created_at=datetime.now(timezone.utc),provider='pexels',scene_number=1,attribution=attribution)
    updated=version.model_copy(update={'image_artifacts':[artifact]})
    item=serialize_version(updated)
    stored=item['image_artifacts'][0]['attribution']['creator_profile_url']
    assert stored=='https://www.pexels.com/@creator'
    assert isinstance(stored,str)
    assert not any(isinstance(x,float) for x in item.values())
