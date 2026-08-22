import json
from pathlib import Path
from .api import CampaignCreationRequest, CampaignDetailResponse, RevisionRequest
from .artifacts import ArtifactReference
from .errors import SanitizedWorkflowError
from .events import CampaignEvent
from .sqs import SQSJobMessage
SCHEMAS={"campaign-creation-request":CampaignCreationRequest,"campaign-detail-response":CampaignDetailResponse,"sqs-job-message":SQSJobMessage,"artifact-reference":ArtifactReference,"sanitized-error":SanitizedWorkflowError,"campaign-event":CampaignEvent,"revision-request":RevisionRequest}
def generate(output:Path)->list[Path]:
    output.mkdir(parents=True,exist_ok=True); paths=[]
    for name,model in sorted(SCHEMAS.items()):
        path=output/f"{name}.schema.json"; text=json.dumps(model.model_json_schema(),indent=2,sort_keys=True,ensure_ascii=False)+"\n"; path.write_text(text,encoding="utf-8"); paths.append(path)
    return paths
def main(): generate(Path(__file__).resolve().parents[3]/"docs"/"contracts"/"generated")
if __name__=="__main__":main()
