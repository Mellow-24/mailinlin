"""Alibaba Cloud Model Studio (DashScope) configuration options.

API keys are region-bound. The ready-to-use shared endpoints remain useful for
OSS installations, while the workspace URL templates are the preferred
production endpoints. Replace ``{workspace_id}`` before using a template.

Model availability differs by region. In particular, the Cantonese CosyVoice
voices are currently most broadly available from the Beijing deployment.
"""

DASHSCOPE_REGIONS = ("beijing", "singapore", "hong_kong")

# Legacy shared domains do not require a workspace ID and remain supported.
DASHSCOPE_COMPATIBLE_BASE_URLS = {
    "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "hong_kong": ("https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1"),
}

# Workspace-dedicated domains are preferred for production traffic.
DASHSCOPE_WORKSPACE_COMPATIBLE_BASE_URL_TEMPLATES = {
    "beijing": (
        "https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    ),
    "singapore": (
        "https://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    ),
    "hong_kong": (
        "https://{workspace_id}.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1"
    ),
}

DASHSCOPE_WEBSOCKET_URLS = {
    "beijing": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
    "singapore": "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference",
    "hong_kong": "wss://cn-hongkong.dashscope.aliyuncs.com/api-ws/v1/inference",
}

DASHSCOPE_WORKSPACE_WEBSOCKET_URL_TEMPLATES = {
    "beijing": (
        "wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    ),
    "singapore": (
        "wss://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference"
    ),
    "hong_kong": (
        "wss://{workspace_id}.cn-hongkong.maas.aliyuncs.com/api-ws/v1/inference"
    ),
}

# Single-value aliases keep the default provider setup simple. Users can select
# a regional or workspace-specific endpoint when their key was created there.
DASHSCOPE_COMPATIBLE_BASE_URL = DASHSCOPE_COMPATIBLE_BASE_URLS["beijing"]
DASHSCOPE_WEBSOCKET_URL = DASHSCOPE_WEBSOCKET_URLS["beijing"]

DASHSCOPE_LLM_MODELS = (
    "qwen-flash",
    "qwen3.7-flash",
    "qwen3.7-flash-2026-07-15",
    "qwen3.7-plus",
    "qwen3.8-flash",
    "qwen3.8-max",
)

# Dograh's current pgvector column is 1536-dimensional. Do not include
# qwen3.7-text-embedding-flash here because it supports at most 1024 dimensions.
DASHSCOPE_EMBEDDING_MODELS = (
    "qwen3.7-text-embedding",
    "text-embedding-v4",
)
DASHSCOPE_EMBEDDING_DIMENSION = 1536

DASHSCOPE_STT_MODELS = (
    "fun-asr-realtime-2026-02-28",
    "fun-asr-realtime",
    "qwen-audio-3.0-asr-flash-streaming",
)

DASHSCOPE_TTS_MODELS = (
    "cosyvoice-v3-flash",
    "cosyvoice-v3-plus",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
)

# CosyVoice v3 preset voices that support Cantonese. The first two are female;
# longanyue_v3 is male and is also available in the Singapore region.
DASHSCOPE_CANTONESE_VOICES = (
    "longjiaxin_v3",
    "longjiayi_v3",
    "longanyue_v3",
)

DASHSCOPE_DEFAULT_LLM_MODEL = DASHSCOPE_LLM_MODELS[0]
DASHSCOPE_DEFAULT_EMBEDDING_MODEL = DASHSCOPE_EMBEDDING_MODELS[0]
DASHSCOPE_DEFAULT_STT_MODEL = DASHSCOPE_STT_MODELS[0]
DASHSCOPE_DEFAULT_TTS_MODEL = DASHSCOPE_TTS_MODELS[0]
DASHSCOPE_DEFAULT_CANTONESE_VOICE = DASHSCOPE_CANTONESE_VOICES[0]
