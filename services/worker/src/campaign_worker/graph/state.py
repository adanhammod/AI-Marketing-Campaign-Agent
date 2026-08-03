from typing import NotRequired, TypedDict

from campaign_contracts.artifacts import PublicArtifactReference
from campaign_contracts.campaign import CampaignVersion


class GraphState(TypedDict):
    version: CampaignVersion
    voice_artifact: NotRequired[PublicArtifactReference]
