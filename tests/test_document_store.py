"""
Tests for the document graph store — M1 exit criteria:

- can create command + output referencing ArtifactStore artifacts
- revision increments via document events
- can materialize doc state for any revision (deterministic replay)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.documents.store import (
    DocumentStore,
    resolve_actor,
    replay_events,
    replay_events_at_revision,
    INDEXER_BUILD,
)
from workbench.documents.indexer import index_bytes, excerpt_bytes, resolve_span


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> DocumentStore:
    s = DocumentStore(str(tmp_path / "documents.db"))
    await s.init()
    yield s
    await s.close()


INVESTIGATION_ID = "inv-test-001"


# ---------------------------------------------------------------------------
# Schema and lifecycle
# ---------------------------------------------------------------------------


class TestSchemaAndLifecycle:
    async def test_schema_version(self, store: DocumentStore):
        assert await store.get_schema_version() == 1

    async def test_create_document_returns_id(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        assert isinstance(doc_id, str) and len(doc_id) > 0

    async def test_get_document_after_create(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        doc = await store.get_document(INVESTIGATION_ID, doc_id)
        assert doc is not None
        assert doc["document_id"] == doc_id
        assert doc["investigation_id"] == INVESTIGATION_ID
        assert doc["current_revision"] == 0
        assert doc["state"] == {}

    async def test_get_document_not_found(self, store: DocumentStore):
        doc = await store.get_document(INVESTIGATION_ID, "nonexistent")
        assert doc is None

    async def test_list_documents(self, store: DocumentStore):
        id1 = await store.create_document(INVESTIGATION_ID)
        id2 = await store.create_document(INVESTIGATION_ID)
        docs = await store.list_documents(INVESTIGATION_ID)
        ids = [d["document_id"] for d in docs]
        assert id1 in ids and id2 in ids

    async def test_list_documents_scoped_to_investigation(self, store: DocumentStore):
        id1 = await store.create_document(INVESTIGATION_ID)
        id2 = await store.create_document("other-inv")
        docs1 = await store.list_documents(INVESTIGATION_ID)
        docs2 = await store.list_documents("other-inv")
        assert any(d["document_id"] == id1 for d in docs1)
        assert not any(d["document_id"] == id1 for d in docs2)
        assert any(d["document_id"] == id2 for d in docs2)


# ---------------------------------------------------------------------------
# Event append and revision increment
# ---------------------------------------------------------------------------


class TestEventAppend:
    async def _command_block(self) -> dict:
        return {
            "id": "block-cmd-001",
            "type": "command",
            "tool": "shell",
            "executor": "agent",
            "run_context": {},
            "input": {"command": "ls -la", "args": []},
            "started_at": "2024-01-01T00:00:00Z",
            "finished_at": "2024-01-01T00:00:01Z",
            "exit_code": 0,
            "labels": [],
            "error_summary": "",
            "created_at": "2024-01-01T00:00:00Z",
            "created_by": "agent:test",
        }

    async def test_append_increments_revision(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        block = await self._command_block()
        result = await store.append_event(
            INVESTIGATION_ID, doc_id,
            "agent:test", "agent", "header",
            "doc.command.created",
            {"block": block},
        )
        assert result["ok"] is True
        assert result["revision"] == 1

    async def test_each_event_increments_revision(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        for i in range(3):
            block = await self._command_block()
            block["id"] = f"block-{i}"
            result = await store.append_event(
                INVESTIGATION_ID, doc_id,
                "agent:test", "agent", "header",
                "doc.command.created",
                {"block": block},
            )
            assert result["revision"] == i + 1

    async def test_event_stored_in_log(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        block = await self._command_block()
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:alice", "human", "header",
            "doc.command.created",
            {"block": block},
        )
        events = await store.get_events(INVESTIGATION_ID, doc_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "doc.command.created"
        assert events[0]["actor_id"] == "human:alice"
        assert events[0]["actor_type"] == "human"
        assert events[0]["prior_revision"] == 0
        assert events[0]["next_revision"] == 1

    async def test_append_to_nonexistent_document_raises(self, store: DocumentStore):
        with pytest.raises(ValueError, match="Document not found"):
            await store.append_event(
                INVESTIGATION_ID, "bad-doc-id",
                "human:test", "human", "header",
                "doc.command.created",
                {"block": {"id": "x", "type": "command"}},
            )


# ---------------------------------------------------------------------------
# Materialized state and block graph
# ---------------------------------------------------------------------------


class TestMaterializedState:
    async def test_command_block_appears_in_state(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        block = {
            "id": "cmd-1",
            "type": "command",
            "tool": "bash",
            "executor": "agent",
            "input": {"command": "whoami"},
            "exit_code": 0,
            "created_at": "2024-01-01T00:00:00Z",
            "created_by": "agent:test",
        }
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "agent:test", "agent", "header",
            "doc.command.created",
            {"block": block},
        )
        state = await store.get_state(INVESTIGATION_ID, doc_id)
        assert state is not None
        assert "cmd-1" in state["blocks"]
        assert state["blocks"]["cmd-1"]["tool"] == "bash"
        assert "cmd-1" in state["block_order"]

    async def test_assertion_state_tracked(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        block = {
            "id": "assert-1",
            "type": "assertion",
            "claim": "Server is down",
            "workflow_state": "draft",
            "authored_by": "human:bob",
            "authored_at": "2024-01-01T00:00:00Z",
            "evidence": [],
            "created_at": "2024-01-01T00:00:00Z",
            "created_by": "human:bob",
        }
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:bob", "human", "header",
            "doc.assertion.created",
            {"block": block},
        )
        state = await store.get_state(INVESTIGATION_ID, doc_id)
        assert state["assertion_states"]["assert-1"] == "draft"

    async def test_review_updates_assertion_state(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)

        # Create assertion
        assertion_block = {
            "id": "assert-1",
            "type": "assertion",
            "claim": "DB is down",
            "workflow_state": "submitted",
            "authored_by": "human:bob",
            "authored_at": "2024-01-01T00:00:00Z",
            "evidence": [],
            "created_at": "2024-01-01T00:00:00Z",
            "created_by": "human:bob",
        }
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:bob", "human", "header",
            "doc.assertion.created",
            {"block": assertion_block},
        )

        # Create review approving it
        review_block = {
            "id": "review-1",
            "type": "review",
            "target_assertion_ids": ["assert-1"],
            "decision": "approved",
            "reason": "Evidence is clear",
            "reviewed_by": "human:alice",
            "reviewed_at": "2024-01-01T00:01:00Z",
            "created_at": "2024-01-01T00:01:00Z",
            "created_by": "human:alice",
        }
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:alice", "human", "header",
            "doc.review.created",
            {"block": review_block},
        )

        state = await store.get_state(INVESTIGATION_ID, doc_id)
        assert state["assertion_states"]["assert-1"] == "approved"


# ---------------------------------------------------------------------------
# Deterministic replay at revision
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    async def test_replay_at_revision_0_is_empty(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        block = {
            "id": "cmd-1", "type": "command", "tool": "sh",
            "executor": "agent", "input": {"command": "ls"},
            "exit_code": 0, "created_at": "2024-01-01T00:00:00Z",
            "created_by": "agent:test",
        }
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "agent:test", "agent", "header",
            "doc.command.created",
            {"block": block},
        )
        state_at_0 = await store.get_state(INVESTIGATION_ID, doc_id, at_revision=0)
        assert state_at_0 == {}

    async def test_replay_at_intermediate_revision(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)

        for i in range(3):
            block = {
                "id": f"cmd-{i}", "type": "command", "tool": "sh",
                "executor": "agent", "input": {"command": f"cmd{i}"},
                "exit_code": 0, "created_at": "2024-01-01T00:00:00Z",
                "created_by": "agent:test",
            }
            await store.append_event(
                INVESTIGATION_ID, doc_id,
                "agent:test", "agent", "header",
                "doc.command.created",
                {"block": block},
            )

        state_at_2 = await store.get_state(INVESTIGATION_ID, doc_id, at_revision=2)
        assert "cmd-0" in state_at_2["blocks"]
        assert "cmd-1" in state_at_2["blocks"]
        assert "cmd-2" not in state_at_2["blocks"]

    async def test_replay_current_matches_materialized(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)
        for i in range(5):
            block = {
                "id": f"cmd-{i}", "type": "command", "tool": "sh",
                "executor": "agent", "input": {"command": f"cmd{i}"},
                "exit_code": 0, "created_at": "2024-01-01T00:00:00Z",
                "created_by": "agent:test",
            }
            await store.append_event(
                INVESTIGATION_ID, doc_id,
                "agent:test", "agent", "header",
                "doc.command.created",
                {"block": block},
            )

        doc = await store.get_document(INVESTIGATION_ID, doc_id)
        current_rev = doc["current_revision"]

        materialized = await store.get_state(INVESTIGATION_ID, doc_id)
        replayed = await store.get_state(INVESTIGATION_ID, doc_id, at_revision=current_rev)

        assert materialized == replayed


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------


class TestOptimisticLocking:
    async def test_mutation_with_correct_revision_succeeds(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)

        # Create assertion at revision 1
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:bob", "human", "header",
            "doc.assertion.created",
            {"block": {"id": "assert-1", "type": "assertion", "claim": "x",
                       "workflow_state": "draft", "evidence": [],
                       "created_at": "2024-01-01T00:00:00Z", "created_by": "human:bob"}},
        )

        # Patch at expected_revision=1 — should succeed
        result = await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:bob", "human", "header",
            "doc.assertion.patched",
            {"assertion_id": "assert-1", "patch": {"claim": "updated"}},
            expected_revision=1,
        )
        assert result["ok"] is True
        assert result["revision"] == 2

    async def test_mutation_with_stale_revision_returns_conflict(self, store: DocumentStore):
        doc_id = await store.create_document(INVESTIGATION_ID)

        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:bob", "human", "header",
            "doc.assertion.created",
            {"block": {"id": "assert-1", "type": "assertion", "claim": "x",
                       "workflow_state": "draft", "evidence": [],
                       "created_at": "2024-01-01T00:00:00Z", "created_by": "human:bob"}},
        )
        # Another event bumps revision to 2
        await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:alice", "human", "header",
            "doc.assertion.created",
            {"block": {"id": "assert-2", "type": "assertion", "claim": "y",
                       "workflow_state": "draft", "evidence": [],
                       "created_at": "2024-01-01T00:00:00Z", "created_by": "human:alice"}},
        )

        # Now patch with stale expected_revision=1 — should conflict
        result = await store.append_event(
            INVESTIGATION_ID, doc_id,
            "human:bob", "human", "header",
            "doc.assertion.patched",
            {"assertion_id": "assert-1", "patch": {"claim": "too late"}},
            expected_revision=1,
        )
        assert result["ok"] is False
        assert result["conflict"] is True
        assert result["current_revision"] == 2
        assert "assert-2" in result["changed_block_ids"]


# ---------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------


class TestActorResolution:
    def test_explicit_header(self):
        aid, atype, src = resolve_actor(actor_id="alice", actor_type="human")
        assert aid == "alice"
        assert atype == "human"
        assert src == "header"

    def test_session_fallback(self):
        aid, atype, src = resolve_actor(session_meta={"session_id": "sess-123", "actor_type": "agent"})
        assert aid == "agent:sess-123"
        assert src == "session"

    def test_placeholder_human(self):
        aid, atype, src = resolve_actor()
        assert aid == "human:unknown"
        assert src == "placeholder"

    def test_placeholder_system(self):
        aid, atype, src = resolve_actor(actor_type="system")
        assert aid == "system:ise"
        assert src == "placeholder"

    def test_explicit_beats_session(self):
        aid, _, src = resolve_actor(
            actor_id="explicit-user",
            session_meta={"session_id": "sess-456"},
        )
        assert aid == "explicit-user"
        assert src == "header"


# ---------------------------------------------------------------------------
# Artifact indexer
# ---------------------------------------------------------------------------


class TestIndexer:
    def test_empty_bytes(self):
        lm, rm = index_bytes(b"")
        assert lm == {} and rm == {}

    def test_single_line_no_newline(self):
        raw = b"hello world"
        lm, rm = index_bytes(raw)
        assert 0 in lm
        assert lm[0] == (0, len(raw))

    def test_two_lines(self):
        raw = b"line one\nline two"
        lm, rm = index_bytes(raw)
        assert len(lm) == 2
        assert lm[0] == (0, 8)       # "line one" without \n
        assert lm[1] == (9, len(raw))

    def test_three_lines_with_trailing_newline(self):
        raw = b"a\nb\nc\n"
        lm, rm = index_bytes(raw)
        # 3 lines: "a", "b", "c", plus empty 4th after trailing newline is not added
        assert len(lm) == 3

    def test_crlf_mode(self):
        raw = b"line1\r\nline2\r\n"
        lm, rm = index_bytes(raw, newline_mode="crlf")
        assert len(lm) == 2
        assert lm[0] == (0, 5)    # "line1" without \r\n
        assert lm[1] == (7, 12)   # "line2" without \r\n

    def test_mixed_mode(self):
        raw = b"a\nb\r\nc"
        lm, rm = index_bytes(raw, newline_mode="mixed")
        assert len(lm) == 3
        assert lm[0] == (0, 1)
        assert lm[1] == (2, 3)
        assert lm[2] == (5, 6)

    def test_unknown_mode_with_lf(self):
        raw = b"x\ny"
        lm, rm = index_bytes(raw, newline_mode="unknown")
        assert len(lm) == 2

    def test_excerpt_bytes(self):
        raw = b"hello world"
        assert excerpt_bytes(raw, 0, 5) == b"hello"
        assert excerpt_bytes(raw, 6, 11) == b"world"

    def test_excerpt_bytes_clamps(self):
        raw = b"hi"
        assert excerpt_bytes(raw, -5, 100) == b"hi"

    def test_resolve_span(self):
        raw = b"line one\nline two\nline three"
        lm, _ = index_bytes(raw)
        result = resolve_span(0, 8, lm, len(raw))
        assert result is not None
        line_start, line_end, _ = result
        assert line_start == 0
        assert line_end == 0


# ---------------------------------------------------------------------------
# Artifact index store
# ---------------------------------------------------------------------------


class TestArtifactIndexStore:
    async def test_store_and_retrieve(self, store: DocumentStore):
        raw = b"line one\nline two\nline three"
        lm, rm = index_bytes(raw)
        index_ref = await store.store_artifact_index("deadbeef" * 8, lm, rm)
        assert isinstance(index_ref, str) and len(index_ref) == 64

        retrieved = await store.get_artifact_index("deadbeef" * 8)
        assert retrieved is not None
        assert retrieved["index_ref"] == index_ref
        assert retrieved["indexer_build"] == INDEXER_BUILD
        assert "0" in retrieved["line_map"]  # line 0 stored as string key

    async def test_missing_returns_none(self, store: DocumentStore):
        result = await store.get_artifact_index("notexist" * 8)
        assert result is None

    async def test_replace_on_reindex(self, store: DocumentStore):
        raw1 = b"version one"
        raw2 = b"version two"
        lm1, rm1 = index_bytes(raw1)
        lm2, rm2 = index_bytes(raw2)
        ref1 = await store.store_artifact_index("aabbccdd" * 8, lm1, rm1)
        ref2 = await store.store_artifact_index("aabbccdd" * 8, lm2, rm2)
        retrieved = await store.get_artifact_index("aabbccdd" * 8)
        assert retrieved["index_ref"] == ref2
