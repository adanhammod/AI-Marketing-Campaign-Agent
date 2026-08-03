import inspect

from campaign_worker.providers.base import VoiceProvider


def test_voice_provider_is_abstract_with_generate_voice():
    assert inspect.isabstract(VoiceProvider)
    assert "generate_voice" in VoiceProvider.__abstractmethods__
