"""Regression tests for publishing the MiniStream opening asset safely."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit

import pytest

from scripts import generate_yishui_opening_audio as generator
from scripts import provision_yishui_voice_demo as provisioner


_TRIAL_TLS_FINGERPRINT_SHA256 = (
    "9DC0B038C26CF47B35748C4717A50529A9D80636C21E9E170CCB24233144E4FD"
)
_TRIAL_HTTP_BASE_URL = "https://120.209.217.11:30700/ministream-api"


def _ministream_tts_config() -> SimpleNamespace:
    return SimpleNamespace(
        api_key="test-token",
        http_base_url="https://ministream.test/ministream-api/",
        language="yue",
        generation_mode="preset_voice",
        voice_preset_key="mailinlin",
        max_generate_length=500,
        model="ministream-tts",
        verify_ssl=True,
        tls_certificate_fingerprint_sha256=_TRIAL_TLS_FINGERPRINT_SHA256,
    )


def test_opening_request_uses_mailinlin_form_and_bearer_header() -> None:
    request = generator.build_opening_request(_ministream_tts_config(), "哈囉。")

    assert request.get_method() == "POST"
    assert request.full_url == "https://ministream.test/ministream-api/tts/synthesize"
    assert urlsplit(request.full_url).query == ""
    assert "test-token" not in request.full_url
    assert request.get_header("Authorization") == "Bearer test-token"
    assert dict(parse_qsl(request.data.decode("utf-8"))) == {
        "language": "yue",
        "generation_mode": "preset_voice",
        "voice_preset_key": "mailinlin",
        "input_text": "哈囉。",
        "speed": "1.0",
        "max_generate_length": "500",
    }


def test_opening_request_rejects_unverified_tls() -> None:
    tts = _ministream_tts_config()
    tts.verify_ssl = False

    with pytest.raises(generator.MiniStreamRequestError, match="verified TLS"):
        generator.build_opening_request(tts, "哈囉。")


def test_fixed_trial_http_endpoint_selects_only_the_configured_pin() -> None:
    configured_pin = _TRIAL_TLS_FINGERPRINT_SHA256

    assert (
        generator._tls_fingerprint_for_http_base_url(
            _TRIAL_HTTP_BASE_URL, configured_pin
        )
        == configured_pin
    )
    assert (
        generator._tls_fingerprint_for_http_base_url(
            "https://custom.example/ministream-api", configured_pin
        )
        is None
    )


def test_pinned_opener_installs_redirect_rejection_before_sending_credentials(
    monkeypatch,
) -> None:
    handlers = []

    class FakeOpener:
        def open(self, *_args, **_kwargs):
            raise AssertionError("the opener should not make a network request")

    def build_opener(*configured_handlers):
        handlers.extend(configured_handlers)
        return FakeOpener()

    monkeypatch.setattr(generator, "build_opener", build_opener)

    with pytest.raises(AssertionError, match="should not make a network request"):
        generator._open_without_redirects(
            generator.Request(
                "https://120.209.217.11:30700/ministream-api/tts/synthesize"
            ),
            tls_certificate_fingerprint_sha256=_TRIAL_TLS_FINGERPRINT_SHA256,
        )

    assert any(isinstance(handler, generator._RejectRedirects) for handler in handlers)
    pinned_handler = next(
        handler
        for handler in handlers
        if isinstance(handler, generator._PinnedHTTPSHandler)
    )
    assert pinned_handler.fingerprint == bytes.fromhex(_TRIAL_TLS_FINGERPRINT_SHA256)


def test_pinned_opener_rejects_a_custom_origin(monkeypatch) -> None:
    monkeypatch.setattr(
        generator,
        "build_opener",
        lambda *_handlers: pytest.fail("custom origin must not create a pinned opener"),
    )

    with pytest.raises(generator.MiniStreamRequestError, match="fixed trial origin"):
        generator._open_without_redirects(
            generator.Request("https://custom.example/ministream-api/tts/synthesize"),
            tls_certificate_fingerprint_sha256=_TRIAL_TLS_FINGERPRINT_SHA256,
        )


def test_synthesis_rejects_redirect_without_calling_redirect_following_urlopen(
    monkeypatch,
) -> None:
    request = generator.build_opening_request(_ministream_tts_config(), "哈囉。")
    opened_requests = []

    def reject_redirect(request, *, tls_certificate_fingerprint_sha256):
        opened_requests.append((request, tls_certificate_fingerprint_sha256))
        raise HTTPError(request.full_url, 302, "Found", {}, BytesIO())

    monkeypatch.setattr(
        generator, "_open_without_redirects", reject_redirect, raising=False
    )
    monkeypatch.setattr(
        generator,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("redirect-following urlopen was called"),
        raising=False,
    )

    with pytest.raises(generator.MiniStreamRequestError, match="redirect"):
        generator._request_synthesis(
            request,
            tls_certificate_fingerprint_sha256=_TRIAL_TLS_FINGERPRINT_SHA256,
        )

    assert opened_requests == [(request, _TRIAL_TLS_FINGERPRINT_SHA256)]
    assert request.get_header("Authorization") == "Bearer test-token"


def test_audio_download_rejects_redirect_without_calling_redirect_following_urlopen(
    tmp_path, monkeypatch
) -> None:
    def reject_redirect(request, *, tls_certificate_fingerprint_sha256):
        raise HTTPError(request.full_url, 307, "Temporary Redirect", {}, BytesIO())

    monkeypatch.setattr(
        generator, "_open_without_redirects", reject_redirect, raising=False
    )
    monkeypatch.setattr(
        generator,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("redirect-following urlopen was called"),
        raising=False,
    )

    with pytest.raises(generator.MiniStreamRequestError, match="redirect"):
        generator._download_audio(
            "https://ministream.test/ministream-api/opening.mp3",
            tmp_path / "opening.mp3",
            tls_certificate_fingerprint_sha256=_TRIAL_TLS_FINGERPRINT_SHA256,
        )


@pytest.mark.parametrize(
    "audio_url",
    [
        "https://ministream.test/ministream-api/opening.mp3",
        "https://evil.test/opening.mp3",
        "//evil.test/opening.mp3",
        "file:///tmp/opening.mp3",
        "//user:password@ministream.test/opening.mp3",
    ],
)
def test_audio_url_must_be_a_same_origin_relative_path(audio_url) -> None:
    with pytest.raises(
        generator.MiniStreamRequestError, match="same-origin relative path"
    ):
        generator._resolved_audio_url(
            "https://ministream.test/ministream-api", audio_url
        )


def test_synthesis_base_requires_https() -> None:
    tts = _ministream_tts_config()
    tts.http_base_url = "http://ministream.test/ministream-api"

    with pytest.raises(generator.MiniStreamRequestError, match="HTTPS"):
        generator.build_opening_request(tts, "哈囉。")


def test_generation_rejects_a_browser_ambiguous_public_url_before_synthesis(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        generator,
        "_request_synthesis",
        lambda *_args, **_kwargs: pytest.fail(
            "unsafe public URL must be rejected before synthesis"
        ),
    )

    with pytest.raises(generator.MiniStreamRequestError, match="local public"):
        generator.generate_opening(
            tts=_ministream_tts_config(),
            source_phrase="哈囉。",
            output=tmp_path / "greetings-04.wav",
            manifest=tmp_path / "greetings-04.json",
            public_url="/\\evil.example/opening.wav",
        )


def test_failed_generation_preserves_existing_public_asset_and_manifest(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "greetings-04.wav"
    manifest = tmp_path / "greetings-04.json"
    output.write_bytes(b"old-wav")
    manifest.write_text('{"asset_id":"old"}\n', encoding="utf-8")

    def fail_request(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr(generator, "_open_without_redirects", fail_request)

    with pytest.raises(generator.MiniStreamRequestError):
        generator.generate_opening(
            tts=_ministream_tts_config(),
            source_phrase="🌸 哈囉～你嚟咗就已經令氣氛暖咗好多！",
            output=output,
            manifest=manifest,
            public_url="/voice-demo/warm-audio/greetings-04.wav",
        )

    assert output.read_bytes() == b"old-wav"
    assert manifest.read_text(encoding="utf-8") == '{"asset_id":"old"}\n'


def test_generation_uses_documented_nested_audio_url(tmp_path, monkeypatch) -> None:
    downloaded_urls: list[str] = []

    monkeypatch.setattr(
        generator,
        "_request_synthesis",
        lambda *_args, **_kwargs: {
            "success": True,
            "data": {"audio_url": "audio/opening.mp3"},
        },
    )
    monkeypatch.setattr(
        generator,
        "_download_audio",
        lambda url, destination, **_kwargs: (
            downloaded_urls.append(url),
            destination.write_bytes(b"mp3"),
        ),
    )
    monkeypatch.setattr(
        generator,
        "_convert_mp3_to_wav",
        lambda _source, destination: destination.write_bytes(b"new-wav"),
    )
    monkeypatch.setattr(generator, "_audio_duration_ms", lambda _path: 4321)

    generator.generate_opening(
        tts=_ministream_tts_config(),
        source_phrase="🌸 哈囉～你嚟咗就已經令氣氛暖咗好多！",
        output=tmp_path / "greetings-04.wav",
        manifest=tmp_path / "greetings-04.json",
        public_url="/voice-demo/warm-audio/greetings-04.wav",
    )

    assert downloaded_urls == [
        "https://ministream.test/ministream-api/audio/opening.mp3"
    ]


def test_generate_opening_set_publishes_seven_individual_openings_and_index(
    tmp_path, monkeypatch
) -> None:
    published_calls: list[dict[str, object]] = []

    def fake_generate_opening(**kwargs):
        output = kwargs["output"]
        manifest = kwargs["manifest"]
        phrase_id = kwargs["phrase_id"]
        public_url = kwargs["public_url"]
        output.write_bytes(b"wav")
        published = {
            "asset_id": f"greetings-{phrase_id}-test",
            "url": public_url,
            "spoken_text": kwargs["spoken_text"],
            "duration_ms": 4321,
        }
        manifest.write_text(json.dumps(published), encoding="utf-8")
        published_calls.append(kwargs)
        return published

    monkeypatch.setattr(generator, "generate_opening", fake_generate_opening)

    openings = generator.generate_opening_set(
        tts=_ministream_tts_config(),
        output_directory=tmp_path,
    )

    assert len(openings) == 7
    assert [call["phrase_id"] for call in published_calls] == [
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
    ]
    assert all(call["spoken_text"] == call["source_phrase"] for call in published_calls)
    assert [opening["url"] for opening in openings] == [
        f"/voice-demo/warm-audio/greetings-{number:02}.wav"
        for number in range(1, 8)
    ]
    index = json.loads((tmp_path / "openings.json").read_text(encoding="utf-8"))
    assert index["version"] == 1
    assert index["count"] == 7
    assert index["openings"] == openings


@pytest.mark.parametrize(
    ("response", "expected_message"),
    [
        (
            {"success": False, "data": {"audio_url": "secret.mp3", "error": "secret"}},
            "MiniStream synthesis reported failure",
        ),
        ({}, "MiniStream synthesis reported failure"),
        ({"success": True}, "MiniStream synthesis returned no data.audio_url"),
        (
            {"success": True, "data": "not-an-object"},
            "MiniStream synthesis returned no data.audio_url",
        ),
        (
            {"success": True, "data": {}},
            "MiniStream synthesis returned no data.audio_url",
        ),
        (
            {"success": True, "data": {"audio_url": 123}},
            "MiniStream synthesis returned no data.audio_url",
        ),
    ],
)
def test_generation_rejects_invalid_documented_response_without_exposing_data(
    tmp_path, monkeypatch, response, expected_message
) -> None:
    monkeypatch.setattr(
        generator,
        "_request_synthesis",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(generator.MiniStreamRequestError) as error:
        generator.generate_opening(
            tts=_ministream_tts_config(),
            source_phrase="🌸 哈囉～你嚟咗就已經令氣氛暖咗好多！",
            output=tmp_path / "greetings-04.wav",
            manifest=tmp_path / "greetings-04.json",
            public_url="/voice-demo/warm-audio/greetings-04.wav",
        )

    assert str(error.value) == expected_message
    assert "secret" not in str(error.value)


def test_publish_failure_restores_existing_public_asset_and_manifest(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "greetings-04.wav"
    manifest = tmp_path / "greetings-04.json"
    output.write_bytes(b"old-wav")
    manifest.write_text('{"asset_id":"old"}\n', encoding="utf-8")

    monkeypatch.setattr(
        generator,
        "_request_synthesis",
        lambda *_args, **_kwargs: {
            "success": True,
            "data": {"audio_url": "/generated.mp3"},
        },
    )
    monkeypatch.setattr(
        generator,
        "_download_audio",
        lambda _url, destination, **_kwargs: destination.write_bytes(b"mp3"),
    )
    monkeypatch.setattr(
        generator,
        "_convert_mp3_to_wav",
        lambda _source, destination: destination.write_bytes(b"new-wav"),
    )
    monkeypatch.setattr(generator, "_audio_duration_ms", lambda _path: 4321)
    replace = generator.os.replace
    replacement_destinations: list[Path] = []

    def fail_manifest_replace(source, destination):
        replacement_destinations.append(destination)
        if destination == manifest:
            raise OSError("disk full")
        replace(source, destination)

    monkeypatch.setattr(generator.os, "replace", fail_manifest_replace)

    with pytest.raises(generator.MiniStreamRequestError):
        generator.generate_opening(
            tts=_ministream_tts_config(),
            source_phrase="🌸 哈囉～你嚟咗就已經令氣氛暖咗好多！",
            output=output,
            manifest=manifest,
            public_url="/voice-demo/warm-audio/greetings-04.wav",
        )

    assert output.read_bytes() == b"old-wav"
    assert manifest.read_text(encoding="utf-8") == '{"asset_id":"old"}\n'
    assert manifest in replacement_destinations


def test_provisioning_uses_the_published_manifest_metadata(tmp_path) -> None:
    manifest = tmp_path / "greetings-04.json"
    manifest.write_text(
        json.dumps(
            {
                "asset_id": "greetings-04-ministream",
                "url": "/voice-demo/warm-audio/greetings-04.wav",
                "spoken_text": "新嘅開場白。",
                "duration_ms": 4321,
            }
        ),
        encoding="utf-8",
    )

    settings = provisioner.headless_voice_settings(
        provisioner.load_opening_asset_manifest(manifest)
    )

    assert settings["clientOpeningAssetId"] == "greetings-04-ministream"
    assert (
        settings["clientOpeningAudioUrl"] == "/voice-demo/warm-audio/greetings-04.wav"
    )
    assert settings["clientOpeningTranscript"] == "新嘅開場白。"
    assert settings["clientOpeningDurationMs"] == 4321
