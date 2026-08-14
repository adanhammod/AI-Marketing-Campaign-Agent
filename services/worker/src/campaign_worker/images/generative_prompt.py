import hashlib
from dataclasses import dataclass

from campaign_contracts.campaign import CampaignVersion, StoryboardScene

_NEGATIVE_SUFFIX = (
    "no text, no typography, no captions, no subtitles, no words, no letters, no numbers, "
    "no logos, no watermark, no signature, no UI elements"
)


@dataclass(frozen=True, slots=True)
class GenerativePrompt:
    positive: str
    negative: str
    fingerprint: str


def _key_message(version: CampaignVersion) -> str:
    if version.strategy is not None and version.strategy.key_message:
        return version.strategy.key_message
    return version.brief.key_message or version.brief.campaign_goal


def build_generative_prompt(version: CampaignVersion, scene: StoryboardScene) -> GenerativePrompt:
    brief = version.brief
    positive = (
        f"Professional advertising photography for {brief.business_name}, "
        f"promoting {brief.product_or_service}. Tone: {brief.tone}. "
        f"Key message: {_key_message(version)}. "
        f"Scene {scene.scene_number} of 3 -- {scene.purpose}. "
        f"Visual direction: {scene.visual_prompt}. "
        "Vertical 9:16 portrait composition, cinematic advertising lighting, "
        "high production value, photorealistic."
    )
    fingerprint = hashlib.sha256(f"{positive}\n{_NEGATIVE_SUFFIX}".encode()).hexdigest()
    return GenerativePrompt(positive=positive, negative=_NEGATIVE_SUFFIX, fingerprint=fingerprint)
