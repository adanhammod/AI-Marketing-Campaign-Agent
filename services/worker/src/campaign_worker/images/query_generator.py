import json
from collections.abc import Awaitable, Callable
from typing import Any

from campaign_contracts.campaign import CampaignVersion

from campaign_worker.graph.boundary import NodeCancelled

from .models import QueryGenerationResult, SceneSearchQuery


def deterministic_queries(version: CampaignVersion) -> list[SceneSearchQuery]:
    if version.storyboard is None:
        raise ValueError("query generation requires a storyboard")
    context = f"{version.brief.product_or_service} {version.brief.business_name}"
    return [
        SceneSearchQuery(scene_number=scene.scene_number, query=f"{context} {scene.visual_prompt}"[:200])
        for scene in version.storyboard.scenes
    ]


class BedrockQueryGenerator:
    def __init__(self, client: Any, model_id: str) -> None:
        self._client = client
        self._model_id = model_id

    async def generate(
        self, version: CampaignVersion, is_cancelled: Callable[[], Awaitable[bool]]
    ) -> QueryGenerationResult:
        if await is_cancelled():
            raise NodeCancelled("generate_images:before_bedrock")
        fallback = deterministic_queries(version)
        payload = {
            "messages": [{"role": "user", "content": [{"text": self._request_text(version)}]}],
            "inferenceConfig": {"maxTokens": 500, "temperature": 0},
        }
        try:
            response = self._client.invoke_model(
                modelId=self._model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(payload).encode(),
            )
            parsed = json.loads(response["body"].read())
            text = parsed["output"]["message"]["content"][0]["text"]
            raw_queries = json.loads(text)
            queries = [
                SceneSearchQuery(scene_number=int(item["scene_number"]), query=str(item["query"]).strip())
                for item in raw_queries
            ]
            if [item.scene_number for item in queries] != [1, 2, 3] or any(not item.query for item in queries):
                raise ValueError("invalid query sequence")
            return QueryGenerationResult(queries=queries, provider="bedrock", model=self._model_id, fallback_used=False)
        except NodeCancelled:
            raise
        except Exception:
            return QueryGenerationResult(queries=fallback, provider="deterministic", model=None, fallback_used=True)

    @staticmethod
    def _request_text(version: CampaignVersion) -> str:
        storyboard = version.storyboard
        assert storyboard is not None
        scenes = [
            {"scene_number": scene.scene_number, "purpose": scene.purpose, "visual": scene.visual_prompt}
            for scene in storyboard.scenes
        ]
        return (
            "Return only a JSON array with exactly three objects containing scene_number and a concise "
            "stock-photo search query. Campaign context: "
            + json.dumps(
                {
                    "business": version.brief.business_name,
                    "product": version.brief.product_or_service,
                    "audience": version.brief.target_audience,
                    "tone": version.brief.tone,
                    "scenes": scenes,
                }
            )
        )
