"""MiniStream TTS configuration defaults for the trial deployment."""

MINISTREAM_HTTP_BASE_URL = "https://120.209.217.11:30700/ministream-api"
MINISTREAM_WEBSOCKET_URL = "wss://120.209.217.11:30700/ministream-ws/tts"

# The public MiniStream trial uses a self-signed certificate.  This is a
# certificate fingerprint, not a credential.  It is used only when the exact
# fixed trial endpoint is selected; custom endpoints retain normal CA and
# hostname verification.
MINISTREAM_TRIAL_TLS_FINGERPRINT_SHA256 = (
    "9DC0B038C26CF47B35748C4717A50529A9D80636C21E9E170CCB24233144E4FD"
)

# MiniStream's trial deployment currently exposes one supported realtime TTS
# profile. Keep these as option collections so the configuration schema can
# describe the fixed values without pretending they are provider model IDs.
MINISTREAM_TTS_MODELS = ("ministream-tts",)
MINISTREAM_TTS_LANGUAGES = ("yue",)
MINISTREAM_GENERATION_MODES = ("preset_voice",)
MINISTREAM_VOICE_PRESET_KEYS = ("mailinlin",)
MINISTREAM_BUFFER_MODES = ("off",)
