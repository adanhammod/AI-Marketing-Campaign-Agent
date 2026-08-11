from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID


class ArtifactURLSigner(ABC):
    @abstractmethod
    async def sign_image(self, campaign_id: UUID, campaign_version: int, scene_number: int) -> tuple[str, datetime]: ...
