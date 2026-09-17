from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from api.db.workflow_run_client import WorkflowRunClient
from api.services.workflow.memory import (
    HAS_REMEMBERED_SUMMARY_CONTEXT_KEY,
    MEMORY_HYDRATION_VERSION_CONTEXT_KEY,
    MEMORY_SUBJECT_ID_CONTEXT_KEY,
    REMEMBERED_SUMMARY_CONTEXT_KEY,
    build_memory_subject_id,
    extract_consented_memory_summary,
    hydrate_memory_context,
    is_memory_enabled,
    resolve_stable_memory_identity,
)


class _StubMemoryDB:
    def __init__(self, summary: str | None):
        self.summary = summary
        self.calls: list[dict] = []

    async def get_latest_consented_memory_summary(self, **kwargs):
        self.calls.append(kwargs)
        return self.summary


def test_memory_requires_explicit_workflow_opt_in():
    assert is_memory_enabled(None) is False
    assert is_memory_enabled({"memory_configuration": {"enabled": False}}) is False
    assert is_memory_enabled({"memory_configuration": {"enabled": 1}}) is False
    assert is_memory_enabled({"memory_configuration": {"enabled": True}}) is True


def test_summary_fails_closed_without_boolean_consent():
    assert (
        extract_consented_memory_summary(
            {
                "explicit_memory_consent": "true",
                "session_summary": "must not escape",
            }
        )
        is None
    )
    assert (
        extract_consented_memory_summary(
            {"explicit_memory_consent": False, "session_summary": "not allowed"}
        )
        is None
    )
    assert (
        extract_consented_memory_summary(
            {"memory_consent": True, "memory_summary": "  safe summary  "}
        )
        == "safe summary"
    )
    assert (
        extract_consented_memory_summary(
            {"explicit_memory_consent": True, "session_summary": "session summary"}
        )
        == "session summary"
    )


def test_phone_identity_wins_over_web_user_fallback():
    assert (
        resolve_stable_memory_identity(
            initial_context={"phone_number": "+852 9123-4567"},
            gathered_context={},
            fallback_user_id=91,
        )
        == "phone:+85291234567"
    )
    assert (
        resolve_stable_memory_identity(
            initial_context={}, gathered_context={}, fallback_user_id=91
        )
        == "user:91"
    )


def test_public_visitor_id_prevents_shared_owner_fallback():
    assert (
        resolve_stable_memory_identity(
            initial_context={"visitor_id": "visitor-123"},
            gathered_context={},
            fallback_user_id=91,
        )
        == "visitor_id:visitor-123"
    )
    assert (
        resolve_stable_memory_identity(
            initial_context={"customer": "customer-42"},
            gathered_context={},
            fallback_user_id=91,
        )
        == "user:91"
    )


def test_subject_hash_is_scoped_to_organization_and_workflow():
    common = {"stable_identity": "phone:+85291234567"}
    first = build_memory_subject_id(organization_id=1, workflow_id=2, **common)
    assert len(first) == 64
    assert first != build_memory_subject_id(organization_id=9, workflow_id=2, **common)
    assert first != build_memory_subject_id(organization_id=1, workflow_id=8, **common)


@pytest.mark.asyncio
async def test_disabled_memory_leaves_context_untouched_without_lookup():
    db = _StubMemoryDB("should not be read")
    context = {"phone_number": "+85291234567"}

    hydrated = await hydrate_memory_context(
        db_client=db,
        workflow_configurations={},
        organization_id=1,
        workflow_id=2,
        workflow_run_id=3,
        initial_context=context,
        gathered_context={},
        fallback_user_id=4,
    )

    assert hydrated == context
    assert db.calls == []


@pytest.mark.asyncio
async def test_hydration_injects_summary_and_is_idempotent_for_text_turns():
    db = _StubMemoryDB("上次谈到客厅采光，尚未确认窗户朝向。")
    config = {"memory_configuration": {"enabled": True}}

    first = await hydrate_memory_context(
        db_client=db,
        workflow_configurations=config,
        organization_id=11,
        workflow_id=22,
        workflow_run_id=33,
        initial_context={"user_id": "visitor"},
        gathered_context={},
        fallback_user_id=44,
    )
    db.summary = "a later result must not change the same session"
    second = await hydrate_memory_context(
        db_client=db,
        workflow_configurations=config,
        organization_id=11,
        workflow_id=22,
        workflow_run_id=33,
        initial_context=first,
        gathered_context={},
        fallback_user_id=44,
    )

    assert first[REMEMBERED_SUMMARY_CONTEXT_KEY].startswith("上次谈到")
    assert first[HAS_REMEMBERED_SUMMARY_CONTEXT_KEY] is True
    assert isinstance(first[MEMORY_SUBJECT_ID_CONTEXT_KEY], str)
    assert first[MEMORY_HYDRATION_VERSION_CONTEXT_KEY] == 1
    assert second == first
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_anonymous_session_never_loads_shared_memory():
    db = _StubMemoryDB("must not be shared")

    hydrated = await hydrate_memory_context(
        db_client=db,
        workflow_configurations={"memory_configuration": {"enabled": True}},
        organization_id=1,
        workflow_id=2,
        workflow_run_id=3,
        initial_context={},
        gathered_context={},
        fallback_user_id=None,
    )

    assert hydrated[MEMORY_SUBJECT_ID_CONTEXT_KEY] is None
    assert hydrated[REMEMBERED_SUMMARY_CONTEXT_KEY] is None
    assert hydrated[HAS_REMEMBERED_SUMMARY_CONTEXT_KEY] is False
    assert db.calls == []


class _CurrentResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _CandidateScalars:
    def __init__(self, candidates):
        self._candidates = candidates

    def scalars(self):
        return iter(self._candidates)


class _FakeMemorySession:
    def __init__(self, candidates):
        self._candidates = candidates
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _CurrentResult((datetime.now(UTC), 100))
        return _CandidateScalars(self._candidates)


@pytest.mark.asyncio
async def test_db_lookup_revalidates_consent_and_is_tenant_scoped():
    candidates = [
        SimpleNamespace(
            gathered_context={
                "explicit_memory_consent": "true",
                "session_summary": "string consent is rejected",
            }
        ),
        SimpleNamespace(
            gathered_context={
                "explicit_memory_consent": True,
                "session_summary": "latest valid summary",
            }
        ),
    ]
    fake_session = _FakeMemorySession(candidates)
    client = WorkflowRunClient.__new__(WorkflowRunClient)
    client.async_session = lambda: fake_session

    summary = await client.get_latest_consented_memory_summary(
        organization_id=7,
        workflow_id=8,
        memory_subject_id="subject",
        before_run_id=100,
    )

    assert summary == "latest valid summary"
    assert len(fake_session.statements) == 2
    compiled = str(
        fake_session.statements[1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "workflows.organization_id" in compiled
    assert "workflow_runs.workflow_id" in compiled
    assert "workflow_runs.is_completed IS true" in compiled
    assert "memory_subject_id" in compiled
    assert "explicit_memory_consent" in compiled
