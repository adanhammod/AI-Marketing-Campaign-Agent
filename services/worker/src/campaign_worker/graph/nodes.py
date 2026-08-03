from campaign_contracts.campaign import CampaignCopy, ChannelCopy, Storyboard, StoryboardScene, StrategyOutput

from .state import GraphState


async def receive_request(state: GraphState) -> GraphState:
    return state


async def validate_input(state: GraphState) -> GraphState:
    if not state["version"].brief.business_description.strip():
        raise ValueError("business_description must not be blank")
    return state


async def analyze_campaign(state: GraphState) -> GraphState:
    return state


async def create_strategy(state: GraphState) -> GraphState:
    version = state["version"]
    brief = version.brief
    audience = brief.target_audience or "general audience"
    strategy = StrategyOutput(
        audience=audience,
        positioning=f"{brief.business_name} for {brief.product_or_service}",
        objective=brief.campaign_goal,
        key_message=brief.key_message or brief.campaign_goal,
        channel_rationale={platform: f"Reach {audience} on {platform}" for platform in brief.platforms},
    )
    return {"version": version.model_copy(update={"strategy": strategy})}


async def generate_copy(state: GraphState) -> GraphState:
    version = state["version"]
    brief = version.brief
    strategy = version.strategy
    if strategy is None:
        raise ValueError("generate_copy requires create_strategy to have run first")
    headline = f"{brief.business_name}: {strategy.key_message}"[:120]
    caption = brief.business_description[:200]
    cta = brief.call_to_action or "Learn more"
    hashtags = [f"#{brief.business_name.replace(' ', '')}"]
    copy = CampaignCopy(
        headline=headline,
        caption=caption,
        call_to_action=cta,
        hashtags=hashtags,
        channel_variants=[
            ChannelCopy(channel=platform, headline=headline, caption=caption, call_to_action=cta, hashtags=hashtags)
            for platform in brief.platforms
        ],
    )
    return {"version": version.model_copy(update={"campaign_copy": copy})}


async def create_storyboard(state: GraphState) -> GraphState:
    version = state["version"]
    strategy = version.strategy
    if strategy is None:
        raise ValueError("create_storyboard requires create_strategy to have run first")
    scenes = [
        StoryboardScene(
            scene_number=index,
            purpose=f"Scene {index} for {strategy.objective}",
            duration_seconds=5,
            narration=strategy.key_message,
            visual_prompt=f"{version.brief.product_or_service}, scene {index}",
            transition="cut",
        )
        for index in (1, 2, 3)
    ]
    storyboard = Storyboard(scenes=scenes, total_duration_seconds=15)
    return {"version": version.model_copy(update={"storyboard": storyboard})}
