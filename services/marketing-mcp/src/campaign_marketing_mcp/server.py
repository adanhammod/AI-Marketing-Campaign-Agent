from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .service import MarketingMCPService

mcp = FastMCP("marketing-mcp")
_service = MarketingMCPService()


@mcp.tool()
async def get_campaign(campaign_id: str) -> dict[str, object] | None:
    record = await _service.get_campaign(UUID(campaign_id))
    if record is None:
        return None
    aggregate, version = record
    return {"aggregate": aggregate.model_dump(mode="json"), "version": version.model_dump(mode="json", by_alias=True)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
