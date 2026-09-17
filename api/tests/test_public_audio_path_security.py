"""Regression coverage for browser-safe client opening audio paths."""

import pytest

from api.utils import url_security


@pytest.mark.parametrize(
    "path",
    [
        "/voice-demo/warm-audio/greetings-04.wav",
        "/assets/audio/opening.mp3",
    ],
)
def test_accepts_root_relative_public_audio_paths(path: str) -> None:
    assert url_security.is_browser_safe_local_public_audio_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/\\evil.example/opening.wav",
        "/%5Cevil.example/opening.wav",
        "//evil.example/opening.wav",
        "///evil.example/opening.wav",
        "/voice-demo/opening.wav?cache=1",
        "/voice-demo/opening.wav#fragment",
        "/voice-demo/../private.wav",
        "/voice-demo/%2E%2E/private.wav",
        "https://evil.example/opening.wav",
        "voice-demo/opening.wav",
    ],
)
def test_rejects_browser_ambiguous_or_nonlocal_public_audio_paths(path: str) -> None:
    assert not url_security.is_browser_safe_local_public_audio_path(path)
