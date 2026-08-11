from uuid import NAMESPACE_URL, UUID, uuid5

from campaign_contracts.enums import CampaignEventType

# Fixed namespace so deterministic_event_id is reproducible across process restarts --
# duplicate SQS delivery or redelivery-after-partial-completion must derive the exact
# same event_id for the exact same logical occurrence, per the frozen contract's
# "duplicate writers use event_id idempotency" rule (docs/contracts/campaign-lifecycle.md).
_NAMESPACE = uuid5(NAMESPACE_URL, "ai-marketing-campaign-agent/worker-events")


def deterministic_event_id(
    campaign_id: UUID, campaign_version: int, event_type: CampaignEventType, discriminator: str
) -> UUID:
    return uuid5(_NAMESPACE, f"{campaign_id}:{campaign_version}:{event_type.value}:{discriminator}")
