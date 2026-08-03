from abc import ABC, abstractmethod

from .models import ImageGenerationRequest, ImageGenerationResult, VideoRenderRequest, VideoRenderResult


class ImageProvider(ABC):
    @abstractmethod
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


class VideoProvider(ABC):
    @abstractmethod
    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult: ...
