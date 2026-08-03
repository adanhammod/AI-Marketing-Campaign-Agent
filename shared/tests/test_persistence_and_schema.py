import json
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID
import pytest
from campaign_contracts.campaign import CampaignAggregateMetadata,CampaignVersion
from campaign_contracts.dynamodb import approval_sk,event_sk,meta_sk,pk,serialize_event,serialize_meta,serialize_step,serialize_version,step_sk,version_sk,_ddb
from campaign_contracts.enums import CampaignStatus,WorkflowStep
from campaign_contracts.events import CampaignEvent
from campaign_contracts.schema_generation import SCHEMAS,generate
ROOT=Path(__file__).parents[1]
def load(name):return json.loads((ROOT/'fixtures'/'valid'/name).read_text(encoding='utf-8-sig'))
def test_keys_and_serialization(tmp_path):
    cid=UUID('018f0000-0000-7000-8000-000000000001'); eid=UUID('018f0000-0000-7000-8000-000000000031'); assert pk(cid)==f'CAMPAIGN#{cid}'; assert meta_sk()=='META'; assert version_sk(2)=='VERSION#2'; assert step_sk(2,WorkflowStep.COPY)=='STEP#2#copy'; assert event_sk(12,eid).startswith('EVENT#00000000000000000012#'); assert approval_sk(2)=='APPROVAL#2'
    with pytest.raises(ValueError):version_sk(0)
    with pytest.raises(ValueError):event_sk(0,eid)
    version=CampaignVersion.model_validate(load('queued-campaign.json')); item=serialize_version(version); assert item['SK']=='VERSION#1'; assert isinstance(item['campaign_version'],Decimal); assert not any(isinstance(x,float) for x in item.values())
    now=datetime(2026,7,28,tzinfo=timezone.utc); meta=CampaignAggregateMetadata(campaign_id=cid,current_version=1,title='x',created_at=now,updated_at=now,lock_version=1); assert serialize_meta(meta)['SK']=='META'; assert serialize_step(cid,1,WorkflowStep.COPY,{'attempt':1})['attempt']==Decimal(1)
    with pytest.raises(TypeError):_ddb(1.2)
def test_event_serialize_and_schema_stability(tmp_path):
    event=CampaignEvent.model_validate(load('events.json')[0]); assert serialize_event(event)['SK'].startswith('EVENT#'); first=generate(tmp_path); snapshots={p.name:p.read_bytes() for p in first}; second=generate(tmp_path); assert snapshots=={p.name:p.read_bytes() for p in second}; assert len(first)==8
