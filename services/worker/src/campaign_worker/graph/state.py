from typing import TypedDict

from campaign_contracts.campaign import CampaignVersion


class GraphState(TypedDict):
    version: CampaignVersion
