from abc import ABC, abstractmethod

from .models import ImageGenerationRequest, ImageGenerationResult, VideoRenderRequest, VideoRenderResult
from .voice_models import VoiceGenerationRequest, VoiceGenerationResult


class ImageProvider(ABC):
    @abstractmethod
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


class VideoProvider(ABC):
    @abstractmethod
    async def render_video(self, request: VideoRenderRequest) -> VideoRenderResult: ...


class VoiceProvider(ABC):
    @abstractmethod
    async def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult: ...
