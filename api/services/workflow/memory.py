"""Consent-gated, local cross-session memory helpers.

This module deliberately keeps memory small and inspectable: a completed run may
contribute only one explicitly consented summary, and the next run receives that
summary through its ordinary ``initial_context``.  No transcript or raw personal
data is copied between sessions.
"""

import hashlib
import re
from typing import Any, Protocol

MEMORY_SUBJECT_ID_CONTEXT_KEY = "memory_subject_id"
REMEMBERED_SUMMARY_CONTEXT_KEY = "remembered_summary"
HAS_REMEMBERED_SUMMARY_CONTEXT_KEY = "has_remembered_summary"
MEMORY_HYDRATION_VERSION_CONTEXT_KEY = "_memory_hydration_version"
MEMORY_HYDRATION_VERSION = 1

_CONSENT_KEYS = ("memory_consent", "explicit_memory_consent")
_SUMMARY_KEYS = ("memory_summary", "session_summary")
_PHONE_KEYS = (
    # Inbound callers and explicit customer fields are the strongest signals.
    "caller_number",
    "caller_phone_number",
    "customer_phone_number",
    "customer_number",
    "customer_phone",
    # Outbound workflows conventionally store the customer under phone_number.
    "phone_number",
    "caller",
    "customer",
    "phone",
)
_OPAQUE_ID_KEYS = ("visitor_id", "customer_id", "external_user_id")
_NON_PHONE_RE = re.compile(r"[^0-9+]")


class _MemoryDBClient(Protocol):
    async def get_latest_consented_memory_summary(
        self,
        *,
        organization_id: int,
        workflow_id: int,
        memory_subject_id: str,
        before_run_id: int,
    ) -> str | None: ...


def is_memory_enabled(workflow_configurations: dict[str, Any] | None) -> bool:
    """Return true only for the explicit opt-in workflow setting."""

    memory_configuration = (workflow_configurations or {}).get("memory_configuration")
    return bool(
        isinstance(memory_configuration, dict)
        and memory_configuration.get("enabled") is True
    )


def extract_consented_memory_summary(
    gathered_context: dict[str, Any] | None,
) -> str | None:
    """Return a minimized summary only when consent is an actual JSON boolean.

    Strings such as ``"true"`` are intentionally rejected.  Memory is a privacy
    boundary, so malformed extraction output must fail closed.
    """

    context = gathered_context or {}
    if not any(context.get(key) is True for key in _CONSENT_KEYS):
        return None

    for key in _SUMMARY_KEYS:
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested_phone_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None
    for key in ("phone_number", "phone", "number", "caller_number"):
        nested = value.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _normalize_phone(value: str) -> str | None:
    # Reject names/opaque customer IDs rather than stripping their letters and
    # accidentally turning e.g. ``customer-42`` into a shared phone identity.
    if re.search(r"[A-Za-z]", value):
        return None
    normalized = _NON_PHONE_RE.sub("", value.strip())
    if normalized.startswith("00"):
        normalized = f"+{normalized[2:]}"
    if normalized.count("+") > 1 or (
        "+" in normalized and not normalized.startswith("+")
    ):
        return None
    digits = normalized.lstrip("+")
    if not 7 <= len(digits) <= 15:
        return None
    return f"+{digits}" if normalized.startswith("+") else digits


def resolve_stable_memory_identity(
    *,
    initial_context: dict[str, Any] | None,
    gathered_context: dict[str, Any] | None,
    fallback_user_id: int | str | None,
) -> str | None:
    """Resolve a stable identity, preferring customer phone fields over web user."""

    # Initial context is authoritative for call routing.  Gathered context is
    # included for providers that populate the customer number after call setup.
    for context in (initial_context or {}, gathered_context or {}):
        for key in _PHONE_KEYS:
            raw_value = _nested_phone_value(context.get(key))
            if raw_value is None:
                continue
            phone = _normalize_phone(raw_value)
            if phone:
                return f"phone:{phone}"

    # Public web demos should provide a stable random visitor_id. Keep the
    # original value out of persisted memory keys by hashing it below, and
    # reject unbounded or control-character identifiers.
    for context in (initial_context or {}, gathered_context or {}):
        for key in _OPAQUE_ID_KEYS:
            value = context.get(key)
            if not isinstance(value, str):
                continue
            value = value.strip()
            if value and len(value) <= 128 and value.isprintable():
                return f"{key}:{value}"

    if fallback_user_id is None:
        return None
    normalized_user_id = str(fallback_user_id).strip()
    if not normalized_user_id:
        return None
    return f"user:{normalized_user_id}"


def build_memory_subject_id(
    *, organization_id: int, workflow_id: int, stable_identity: str
) -> str:
    """Hash tenant, workflow, and identity into a non-plaintext lookup key."""

    material = (
        f"dograh-memory:v1:{organization_id}:{workflow_id}:{stable_identity}"
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _is_hydrated_for_subject(context: dict[str, Any], subject_id: str) -> bool:
    """Recognize context previously persisted by this hydration version."""

    return (
        context.get(MEMORY_HYDRATION_VERSION_CONTEXT_KEY) == MEMORY_HYDRATION_VERSION
        and context.get(MEMORY_SUBJECT_ID_CONTEXT_KEY) == subject_id
        and REMEMBERED_SUMMARY_CONTEXT_KEY in context
        and HAS_REMEMBERED_SUMMARY_CONTEXT_KEY in context
    )


async def hydrate_memory_context(
    *,
    db_client: _MemoryDBClient,
    workflow_configurations: dict[str, Any] | None,
    organization_id: int,
    workflow_id: int,
    workflow_run_id: int,
    initial_context: dict[str, Any] | None,
    gathered_context: dict[str, Any] | None,
    fallback_user_id: int | str | None,
) -> dict[str, Any]:
    """Inject the latest explicitly consented summary into a run context.

    The persisted hydration marker makes repeated text-chat turns idempotent.
    Disabled workflows are returned untouched, and anonymous sessions never
    share a fallback identity.
    """

    context = dict(initial_context or {})
    if not is_memory_enabled(workflow_configurations):
        return context

    stable_identity = resolve_stable_memory_identity(
        initial_context=context,
        gathered_context=gathered_context,
        fallback_user_id=fallback_user_id,
    )
    if stable_identity is None:
        # Do not collapse anonymous visitors into one shared subject.
        return {
            **context,
            MEMORY_SUBJECT_ID_CONTEXT_KEY: None,
            REMEMBERED_SUMMARY_CONTEXT_KEY: None,
            HAS_REMEMBERED_SUMMARY_CONTEXT_KEY: False,
            MEMORY_HYDRATION_VERSION_CONTEXT_KEY: MEMORY_HYDRATION_VERSION,
        }

    subject_id = build_memory_subject_id(
        organization_id=organization_id,
        workflow_id=workflow_id,
        stable_identity=stable_identity,
    )
    if _is_hydrated_for_subject(context, subject_id):
        return context

    summary = await db_client.get_latest_consented_memory_summary(
        organization_id=organization_id,
        workflow_id=workflow_id,
        memory_subject_id=subject_id,
        before_run_id=workflow_run_id,
    )
    return {
        **context,
        MEMORY_SUBJECT_ID_CONTEXT_KEY: subject_id,
        REMEMBERED_SUMMARY_CONTEXT_KEY: summary,
        HAS_REMEMBERED_SUMMARY_CONTEXT_KEY: summary is not None,
        MEMORY_HYDRATION_VERSION_CONTEXT_KEY: MEMORY_HYDRATION_VERSION,
    }
