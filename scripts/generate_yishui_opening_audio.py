#!/usr/bin/env python3
"""Generate the client-preloaded Cantonese opening asset with MiniStream."""

from __future__ import annotations

import argparse
import asyncio
import csv
import http.client
import hashlib
import hmac
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.configuration.options.ministream import (
    MINISTREAM_HTTP_BASE_URL,
    MINISTREAM_TRIAL_TLS_FINGERPRINT_SHA256,
)
from api.services.configuration.registry import MiniStreamTTSConfiguration
from api.utils.url_security import is_browser_safe_local_public_audio_path


DISCLOSURE = (
    "我係玲玲師傅 AI 語音角色，唔係麥玲玲本人。內容只作傳統文化同生活參考。今日想問咩呢？"
)
OPENING_LANGUAGE = "yue"
OPENING_GENERATION_MODE = "preset_voice"
OPENING_VOICE_PRESET_KEY = "mailinlin"
OPENING_SPEED = 1.0
OPENING_SAMPLE_RATE = 24_000
REDIRECT_STATUS_CODES = frozenset(range(300, 400))


@dataclass(frozen=True)
class OpeningScript:
    """One independently playable Cantonese demo opening."""

    phrase_id: str
    text: str


# These are deliberately complete utterances.  Unlike the legacy CSV pathway,
# they are not appended with the generic disclosure/question suffix: each item
# is the approved wording that is shown as its matching on-screen transcript.
OPENING_SCRIPTS = (
    OpeningScript(
        "01",
        "你好呀，我係玲玲師傅 AI 語音角色。今日見到你好開心，想同你慢慢傾下。內容只作傳統文化同生活參考，今日想問咩呢？",
    ),
    OpeningScript(
        "02",
        "新嘅一日開始啦，願你今日有好心情同一點小幸運。我係玲玲師傅 AI 語音角色，有咩想問，不妨慢慢講。",
    ),
    OpeningScript(
        "03",
        "哈囉，見到你嚟咗，今日嘅氣氛都暖咗好多。我係玲玲師傅 AI 語音角色，想睇運程、家居風水，定係有其他心事想傾呢？",
    ),
    OpeningScript(
        "04",
        "你好呀，今日本身就有一點小幸運等緊你。我係玲玲師傅 AI 語音角色，會用傳統文化同生活角度陪你分析，今日想從邊樣開始？",
    ),
    OpeningScript(
        "05",
        "歡迎你嚟同玲玲師傅 AI 語音角色傾偈。無論你想問家居佈置、運程方向，定係近排有咩煩心事，都可以慢慢同我講。",
    ),
    OpeningScript(
        "06",
        "今日天空好似特別靚，正好停一停，聽下自己心入面想問咩。我係玲玲師傅 AI 語音角色，今日想同你傾咩呢？",
    ),
    OpeningScript(
        "07",
        "好耐冇傾都唔緊要，坐低飲杯茶，慢慢講。我係玲玲師傅 AI 語音角色，內容只作參考，你而家最想了解邊一方面？",
    ),
)


class MiniStreamRequestError(RuntimeError):
    """A MiniStream opening-audio request or conversion failed."""


class _RejectRedirects(HTTPRedirectHandler):
    """Turn every redirect response into an error before another request starts."""

    def http_error_302(self, request, response, code, message, headers):
        raise HTTPError(request.full_url, code, "Redirect rejected", headers, response)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that authenticates the trial's self-signed certificate.

    The fixed trial certificate cannot use the platform CA store.  This
    connection therefore completes the TLS handshake solely to compare the
    peer certificate's SHA-256 digest against the configured public pin.
    """

    def __init__(self, *args: Any, fingerprint: bytes, **kwargs: Any) -> None:
        self._fingerprint = fingerprint
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        super().connect()
        try:
            certificate = self.sock.getpeercert(binary_form=True) if self.sock else None
            received_fingerprint = hashlib.sha256(certificate or b"").digest()
        except Exception as exc:
            self.close()
            raise ssl.SSLCertVerificationError(
                "MiniStream certificate fingerprint verification failed"
            ) from exc
        if not hmac.compare_digest(received_fingerprint, self._fingerprint):
            self.close()
            raise ssl.SSLCertVerificationError(
                "MiniStream certificate fingerprint verification failed"
            )


class _PinnedHTTPSHandler(HTTPSHandler):
    """Use certificate pinning only for the fixed self-signed trial endpoint."""

    def __init__(self, fingerprint: bytes) -> None:
        self.fingerprint = fingerprint
        context = ssl.create_default_context()
        # The self-signed trial certificate cannot pass CA validation.  The
        # connection above replaces that trust decision with an exact SHA-256
        # certificate pin; this context is never selected for custom URLs.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        super().__init__(context=context)

    def https_open(self, request: Request):
        def connection_factory(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            return _PinnedHTTPSConnection(
                host,
                fingerprint=self.fingerprint,
                **kwargs,
            )

        return self.do_open(connection_factory, request, context=self._context)


def _spoken_text(source_phrase: str) -> str:
    cleaned = "".join(
        char
        for char in source_phrase
        if unicodedata.category(char) not in {"So", "Sk"}
        and char not in {"\ufe0f", "\u200d"}
    )
    cleaned = cleaned.replace("～", "，").strip(" ，")
    return f"{cleaned}{DISCLOSURE}"


def _audio_duration_ms(audio_path: Path) -> int:
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(probe.stdout)
        duration_ms = round(float(payload["format"]["duration"]) * 1000)
    except (
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        raise MiniStreamRequestError(
            "Generated opening WAV has no readable duration"
        ) from exc
    if duration_ms <= 0:
        raise MiniStreamRequestError("Generated opening WAV has no audible duration")
    return duration_ms


def _find_phrase(csv_path: Path, category: str, phrase_id: str) -> str:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        for row in rows:
            if row.get("category") == category and row.get("id") == phrase_id:
                phrase = (row.get("phrase") or "").strip()
                if phrase:
                    return phrase
    raise SystemExit(f"Phrase not found: category={category}, id={phrase_id}")


def _tls_fingerprint_for_http_base_url(
    http_base_url: str,
    configured_fingerprint: str,
) -> str | None:
    """Return the trial pin only for the exact fixed public HTTP endpoint."""
    if http_base_url.rstrip("/") == MINISTREAM_HTTP_BASE_URL.rstrip("/"):
        return configured_fingerprint
    return None


def _is_fixed_trial_http_origin(request_url: str) -> bool:
    """Return whether a request remains on the fixed pinned trial origin."""
    request_parts = urlsplit(request_url)
    trial_parts = urlsplit(MINISTREAM_HTTP_BASE_URL)
    return (
        request_parts.scheme == trial_parts.scheme
        and request_parts.netloc == trial_parts.netloc
    )


def _decode_trial_tls_fingerprint(fingerprint: str) -> bytes:
    """Decode only the configured public MiniStream trial certificate pin."""
    if not isinstance(fingerprint, str):
        raise MiniStreamRequestError("MiniStream TLS certificate pin is invalid")
    normalized_fingerprint = fingerprint.replace(":", "").upper()
    if normalized_fingerprint != MINISTREAM_TRIAL_TLS_FINGERPRINT_SHA256:
        raise MiniStreamRequestError("MiniStream TLS certificate pin is invalid")
    return bytes.fromhex(normalized_fingerprint)


def _open_without_redirects(
    request: Request,
    *,
    tls_certificate_fingerprint_sha256: str | None,
):
    """Open one HTTPS request without redirects or an unverified TLS fallback."""
    if urlsplit(request.full_url).scheme != "https":
        raise MiniStreamRequestError("MiniStream requests must use HTTPS")
    if (
        tls_certificate_fingerprint_sha256 is not None
        and not _is_fixed_trial_http_origin(request.full_url)
    ):
        raise MiniStreamRequestError(
            "MiniStream trial TLS pin may only be used for the fixed trial origin"
        )

    handlers: list[Any] = [_RejectRedirects()]
    if tls_certificate_fingerprint_sha256 is None:
        handlers.append(HTTPSHandler(context=ssl.create_default_context()))
    else:
        handlers.append(
            _PinnedHTTPSHandler(
                _decode_trial_tls_fingerprint(tls_certificate_fingerprint_sha256)
            )
        )
    opener = build_opener(*handlers)
    return opener.open(request, timeout=240)


def _synthesis_endpoint(http_base_url: str) -> str:
    parsed_url = urlsplit(http_base_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.query
        or parsed_url.fragment
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise MiniStreamRequestError(
            "MiniStream HTTP base URL must be HTTPS and contain no credentials or query string"
        )
    path = f"{parsed_url.path.rstrip('/')}/tts/synthesize"
    return urlunsplit((parsed_url.scheme, parsed_url.netloc, path, "", ""))


def build_opening_request(tts: MiniStreamTTSConfiguration, input_text: str) -> Request:
    """Build the credential-safe, form-encoded MiniStream synthesis request."""
    if not tts.verify_ssl:
        raise MiniStreamRequestError("Opening synthesis requires verified TLS")
    if (
        tts.language != OPENING_LANGUAGE
        or tts.generation_mode != OPENING_GENERATION_MODE
        or tts.voice_preset_key != OPENING_VOICE_PRESET_KEY
    ):
        raise MiniStreamRequestError(
            "Opening synthesis requires the MiniStream yue/mailinlin preset profile"
        )
    if len(input_text) > tts.max_generate_length:
        raise MiniStreamRequestError(
            "Opening text exceeds MiniStream max_generate_length"
        )

    endpoint = _synthesis_endpoint(tts.http_base_url)
    form_data = {
        "language": OPENING_LANGUAGE,
        "generation_mode": OPENING_GENERATION_MODE,
        "voice_preset_key": OPENING_VOICE_PRESET_KEY,
        "input_text": input_text,
        "speed": str(OPENING_SPEED),
        "max_generate_length": str(tts.max_generate_length),
    }
    return Request(
        endpoint,
        data=urlencode(form_data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {tts.api_key}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "Dograh-Yishui-Opening/1.0",
        },
        method="POST",
    )


def _request_synthesis(
    request: Request,
    *,
    tls_certificate_fingerprint_sha256: str | None,
) -> dict[str, Any]:
    try:
        with _open_without_redirects(
            request,
            tls_certificate_fingerprint_sha256=tls_certificate_fingerprint_sha256,
        ) as response:
            response_data = response.read().decode("utf-8")
    except HTTPError as exc:
        if exc.code in REDIRECT_STATUS_CODES:
            raise MiniStreamRequestError(
                "MiniStream synthesis redirect was rejected"
            ) from exc
        raise MiniStreamRequestError(f"MiniStream synthesis HTTP {exc.code}") from exc
    except URLError as exc:
        raise MiniStreamRequestError("MiniStream synthesis request failed") from exc

    try:
        payload = json.loads(response_data)
    except json.JSONDecodeError as exc:
        raise MiniStreamRequestError(
            "MiniStream synthesis returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MiniStreamRequestError("MiniStream synthesis returned an invalid payload")
    return payload


def _download_audio(
    audio_url: str,
    destination: Path,
    *,
    tls_certificate_fingerprint_sha256: str | None,
) -> None:
    request = Request(audio_url, headers={"User-Agent": "Dograh-Yishui-Opening/1.0"})
    try:
        with _open_without_redirects(
            request,
            tls_certificate_fingerprint_sha256=tls_certificate_fingerprint_sha256,
        ) as response:
            destination.write_bytes(response.read())
    except HTTPError as exc:
        if exc.code in REDIRECT_STATUS_CODES:
            raise MiniStreamRequestError(
                "MiniStream opening audio redirect was rejected"
            ) from exc
        raise MiniStreamRequestError(
            "Unable to download MiniStream opening audio"
        ) from exc
    except (URLError, OSError) as exc:
        raise MiniStreamRequestError(
            "Unable to download MiniStream opening audio"
        ) from exc
    if destination.stat().st_size == 0:
        raise MiniStreamRequestError("Downloaded MiniStream opening audio is empty")


def _convert_mp3_to_wav(source: Path, destination: Path) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                str(OPENING_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MiniStreamRequestError("Unable to convert MiniStream MP3 to WAV") from exc
    if not destination.is_file() or destination.stat().st_size == 0:
        raise MiniStreamRequestError("MiniStream WAV conversion produced an empty file")


def _resolved_audio_url(http_base_url: str, audio_url: str) -> str:
    _synthesis_endpoint(http_base_url)
    parsed_audio_url = urlsplit(audio_url)
    if (
        not parsed_audio_url.path
        or parsed_audio_url.scheme
        or parsed_audio_url.netloc
        or parsed_audio_url.username is not None
        or parsed_audio_url.password is not None
        or parsed_audio_url.query
        or parsed_audio_url.fragment
    ):
        raise MiniStreamRequestError(
            "MiniStream audio_url must be a same-origin relative path"
        )
    base_url = urlsplit(http_base_url)
    same_origin_base = urlunsplit(
        (
            base_url.scheme,
            base_url.netloc,
            f"{base_url.path.rstrip('/')}/",
            "",
            "",
        )
    )
    return urljoin(same_origin_base, audio_url)


def _documented_audio_url(result: dict[str, Any]) -> str:
    """Extract only the documented ``success``/``data.audio_url`` response."""
    if result.get("success") is not True:
        raise MiniStreamRequestError("MiniStream synthesis reported failure")
    data = result.get("data")
    if not isinstance(data, dict):
        raise MiniStreamRequestError("MiniStream synthesis returned no data.audio_url")
    audio_url = data.get("audio_url")
    if not isinstance(audio_url, str) or not audio_url.strip():
        raise MiniStreamRequestError("MiniStream synthesis returned no data.audio_url")
    return audio_url


def _opening_manifest(
    *,
    tts: MiniStreamTTSConfiguration,
    source_file: str,
    source_phrase: str,
    spoken_text: str,
    public_url: str,
    duration_ms: int,
    sha256: str,
    asset_id_prefix: str,
    phrase_id: str,
) -> dict[str, Any]:
    return {
        "version": sha256[:12],
        "created_at": datetime.now(UTC).isoformat(),
        "asset_id": f"{asset_id_prefix}-{sha256[:12]}",
        "category": "greetings",
        "phrase_id": phrase_id,
        "source_file": source_file,
        "source_phrase": source_phrase,
        "spoken_text": spoken_text,
        "url": public_url,
        "duration_ms": duration_ms,
        "sha256": sha256,
        "sample_rate": OPENING_SAMPLE_RATE,
        "provider": "ministream",
        "model": tts.model,
        "language": OPENING_LANGUAGE,
        "generation_mode": OPENING_GENERATION_MODE,
        "voice_preset_key": OPENING_VOICE_PRESET_KEY,
        "speed": OPENING_SPEED,
        "max_generate_length": tts.max_generate_length,
    }


def _publish_assets(
    *,
    temporary_wav: Path,
    temporary_manifest: Path,
    output: Path,
    manifest: Path,
) -> None:
    """Replace both public files and restore their previous versions on failure."""
    previous_wav = temporary_wav.with_name("previous.wav")
    previous_manifest = temporary_manifest.with_name("previous.json")
    output_existed = output.exists()
    manifest_existed = manifest.exists()
    wav_published = False
    manifest_published = False

    try:
        if output_existed:
            shutil.copy2(output, previous_wav)
        if manifest_existed:
            shutil.copy2(manifest, previous_manifest)

        os.replace(temporary_wav, output)
        wav_published = True
        os.replace(temporary_manifest, manifest)
        manifest_published = True
    except OSError as exc:
        try:
            if wav_published:
                if output_existed:
                    os.replace(previous_wav, output)
                else:
                    output.unlink(missing_ok=True)
            if manifest_published:
                if manifest_existed:
                    os.replace(previous_manifest, manifest)
                else:
                    manifest.unlink(missing_ok=True)
        except OSError as restore_exc:
            raise MiniStreamRequestError(
                "Unable to publish opening asset or restore the previous public asset"
            ) from restore_exc
        raise MiniStreamRequestError("Unable to publish opening asset") from exc


def generate_opening(
    *,
    tts: MiniStreamTTSConfiguration,
    source_phrase: str,
    output: Path,
    manifest: Path,
    public_url: str,
    source_file: str = "warm_phrases.csv",
    spoken_text: str | None = None,
    asset_id_prefix: str = "greetings-04",
    phrase_id: str = "4",
) -> dict[str, Any]:
    """Generate and validate both assets before atomically publishing either."""
    if not is_browser_safe_local_public_audio_path(public_url):
        raise MiniStreamRequestError(
            "Opening public URL must be a browser-safe local public audio path"
        )
    spoken_text = spoken_text or _spoken_text(source_phrase)
    request = build_opening_request(tts, spoken_text)
    tls_certificate_fingerprint_sha256 = _tls_fingerprint_for_http_base_url(
        tts.http_base_url,
        tts.tls_certificate_fingerprint_sha256,
    )
    result = _request_synthesis(
        request,
        tls_certificate_fingerprint_sha256=tls_certificate_fingerprint_sha256,
    )
    audio_url = _documented_audio_url(result)

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.stem}-", dir=output.parent
    ) as temp_dir:
        temporary_dir = Path(temp_dir)
        temporary_mp3 = temporary_dir / "opening.mp3"
        temporary_wav = temporary_dir / "opening.wav"
        temporary_manifest = temporary_dir / "opening.json"
        _download_audio(
            _resolved_audio_url(tts.http_base_url, audio_url),
            temporary_mp3,
            tls_certificate_fingerprint_sha256=tls_certificate_fingerprint_sha256,
        )
        _convert_mp3_to_wav(temporary_mp3, temporary_wav)
        duration_ms = _audio_duration_ms(temporary_wav)
        sha256 = hashlib.sha256(temporary_wav.read_bytes()).hexdigest()
        published_manifest = _opening_manifest(
            tts=tts,
            source_file=source_file,
            source_phrase=source_phrase,
            spoken_text=spoken_text,
            public_url=public_url,
            duration_ms=duration_ms,
            sha256=sha256,
            asset_id_prefix=asset_id_prefix,
            phrase_id=phrase_id,
        )
        temporary_manifest.write_text(
            json.dumps(published_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _publish_assets(
            temporary_wav=temporary_wav,
            temporary_manifest=temporary_manifest,
            output=output,
            manifest=manifest,
        )

    return published_manifest


def _publish_opening_index(
    output_directory: Path,
    openings: list[dict[str, Any]],
) -> None:
    """Publish the aggregate public manifest only after every WAV is ready."""
    index_path = output_directory / "openings.json"
    payload = {"version": 1, "count": len(openings), "openings": openings}
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output_directory,
        prefix=".openings-",
        suffix=".json",
        delete=False,
    ) as temporary_file:
        temporary_file.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary_path = Path(temporary_file.name)
    try:
        os.replace(temporary_path, index_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise MiniStreamRequestError("Unable to publish opening audio index") from exc


def generate_opening_set(
    *,
    tts: MiniStreamTTSConfiguration,
    output_directory: Path,
) -> list[dict[str, Any]]:
    """Generate all seven approved openings and then publish their index."""
    output_directory.mkdir(parents=True, exist_ok=True)
    openings: list[dict[str, Any]] = []
    for opening in OPENING_SCRIPTS:
        filename = f"greetings-{opening.phrase_id}"
        openings.append(
            generate_opening(
                tts=tts,
                source_phrase=opening.text,
                spoken_text=opening.text,
                output=output_directory / f"{filename}.wav",
                manifest=output_directory / f"{filename}.json",
                public_url=f"/voice-demo/warm-audio/{filename}.wav",
                source_file="approved_yishui_opening_scripts",
                asset_id_prefix=filename,
                phrase_id=opening.phrase_id,
            )
        )
    _publish_opening_index(output_directory, openings)
    return openings


async def generate(args: argparse.Namespace) -> None:
    resolved = await get_resolved_ai_model_configuration(
        organization_id=args.organization_id
    )
    tts = resolved.effective.tts
    if not isinstance(tts, MiniStreamTTSConfiguration):
        raise SystemExit(
            "Opening audio generation requires MiniStream TTS configuration"
        )

    if args.all:
        openings = generate_opening_set(
            tts=tts,
            output_directory=args.output_directory,
        )
        print(
            json.dumps(
                {
                    "count": len(openings),
                    "output_directory": str(args.output_directory),
                },
                ensure_ascii=False,
            )
        )
        return

    if args.csv is None or args.output is None or args.manifest is None:
        raise SystemExit("--csv, --output, and --manifest are required without --all")
    source_phrase = _find_phrase(args.csv, args.category, args.phrase_id)
    manifest = generate_opening(
        tts=tts,
        source_phrase=source_phrase,
        output=args.output,
        manifest=args.manifest,
        public_url=args.public_url,
        source_file=args.csv.name,
    )
    print(
        json.dumps(
            {
                "asset_id": manifest["asset_id"],
                "source_phrase": manifest["source_phrase"],
                "spoken_text": manifest["spoken_text"],
                "duration_ms": manifest["duration_ms"],
                "sha256": manifest["sha256"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, default=1)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--category", default="greetings")
    parser.add_argument("--phrase-id", default="4")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--public-url",
        default="/voice-demo/warm-audio/greetings-04.wav",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate the seven approved, independently rotating openings.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("ui/public/voice-demo/warm-audio"),
        help="Public directory for --all WAV, manifest, and index assets.",
    )
    args = parser.parse_args()
    asyncio.run(generate(args))


if __name__ == "__main__":
    main()
