"""
M6 agent investigation tool tests.

No LLM required — tests call tool .execute() directly.

Coverage:
1.  CreateAssertionTool: happy path — assertion block created, actor fields set
2.  CreateAssertionTool: evidence spans validated, line range derived from index
3.  CreateAssertionTool: invalid byte span rejected, no partial write
4.  CreateAssertionTool: artifact_ref mismatch with output_id rejected
5.  CreateAssertionTool: unknown output_id rejected
6.  CreateAssertionTool: missing required params returns failure
7.  CreateAssertionTool: unknown artifact_ref rejected
8.  CreateAssertionTool: workflow_state=submitted requires evidence
9.  CreateAssertionTool: workflow_state=submitted stored correctly
10. SubmitForReviewTool: happy path — draft -> submitted
11. SubmitForReviewTool: idempotent — already submitted skipped, no new event
12. SubmitForReviewTool: approved assertion cannot be resubmitted
13. SubmitForReviewTool: non-existent assertion_id returns error
14. SubmitForReviewTool: pending_review_count reflects submitted count
15. SubmitForReviewTool: missing required params
16. RegenerateNarrativeTool: happy path — narrative created from approved assertions
17. RegenerateNarrativeTool: blocked if no approved assertions
18. RegenerateNarrativeTool: revision conflict returned as structured error
19. RegenerateNarrativeTool: both audiences generate distinct narratives
20. RegenerateNarrativeTool: missing required params
21. End-to-end: ingest -> create assertion -> submit -> approve -> regen narrative
22. CreateAssertionTool: multiple evidence spans — all validated before write
23. build_document_model_context: empty doc returns empty string
24. build_document_model_context: reflects approved, submitted, narrative counts
25. SubmitForReviewTool: rejected assertion cannot be resubmitted
"""

from __future__ import annotations

import pytest
from pathlib import Path

from workbench.documents.store import DocumentStore
from workbench.documents.ingest import process_bytes_ingest
from workbench.session.artifacts import ArtifactStore
from workbench.tools.investigation_tools import (
    CreateAssertionTool,
    SubmitForReviewTool,
    RegenerateNarrativeTool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def doc_store(tmp_path: Path):
    db = str(tmp_path / "docs.db")
    store = DocumentStore(db)
    await store.init()
    return store


@pytest.fixture
def artifact_store(tmp_path: Path):
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir()
    return ArtifactStore(str(arts_dir))


INV_ID = "inv-m6-test"


@pytest.fixture
async def inv_and_doc(doc_store):
    """Create a fresh investigation + document."""
    doc_id = await doc_store.create_document(INV_ID)
    return INV_ID, doc_id


@pytest.fixture
async def inv_with_output(doc_store, artifact_store, inv_and_doc):
    """
    Investigation + document with one ingested output block.
    Returns (inv_id, doc_id, artifact_ref, output_id).
    """
    inv_id, doc_id = inv_and_doc
    raw = b"alpha bravo charlie delta echo foxtrot\ngolf hotel india juliet\n"
    result = await process_bytes_ingest(
        doc_store=doc_store,
        artifact_store=artifact_store,
        investigation_id=inv_id,
        document_id=doc_id,
        actor_id="test",
        actor_type="human",
        actor_source="fixture",
        raw=raw,
        filename="test.log",
        content_type="text/plain",
    )
    return inv_id, doc_id, result["artifact_ref"], result["output_id"]


def make_create_tool(doc_store, artifact_store):
    return CreateAssertionTool(doc_store, artifact_store)


def make_submit_tool(doc_store):
    return SubmitForReviewTool(doc_store)


def make_regen_tool(doc_store):
    return RegenerateNarrativeTool(doc_store)


async def _make_review(doc_store, inv_id, doc_id, assertion_ids, decision, reason="test reason"):
    """Append a review block (doc.review.created) for one or more assertions."""
    import uuid
    from datetime import datetime, timezone
    bid = str(uuid.uuid4())
    block = {
        "id": bid,
        "type": "review",
        "target_assertion_ids": assertion_ids,
        "decision": decision,
        "reason": reason,
        "reviewed_by": "human:test",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await doc_store.append_event(
        inv_id, doc_id,
        "human:test", "human", "test",
        "doc.review.created",
        {"block": block},
    )
    assert result["ok"], f"Review failed: {result}"
    return result["revision"]


async def _approve_assertion(doc_store, inv_id, doc_id, assertion_id):
    return await _make_review(doc_store, inv_id, doc_id, [assertion_id], "approved")


async def _reject_assertion(doc_store, inv_id, doc_id, assertion_id):
    return await _make_review(doc_store, inv_id, doc_id, [assertion_id], "rejected")


# ---------------------------------------------------------------------------
# 1. CreateAssertionTool: happy path
# ---------------------------------------------------------------------------

class TestCreateAssertionHappyPath:
    async def test_creates_assertion_block(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Alpha and bravo are present in the log",
        )

        assert result.success, result.content
        assert result.data["assertion_id"]
        assert result.data["workflow_state"] == "draft"

        state = await doc_store.get_state(inv_id, doc_id)
        assertion_id = result.data["assertion_id"]
        block = state["blocks"][assertion_id]
        assert block["type"] == "assertion"
        assert block["claim"] == "Alpha and bravo are present in the log"
        assert block["created_by"] == "agent:ise"
        assert block["authored_by"] == "agent:ise"

    async def test_evidence_spans_validated_and_stored(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        # "alpha bravo" is bytes 0..11 in the raw payload
        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="First word is alpha",
            evidence=[{
                "artifact_ref": art_ref,
                "byte_start": 0,
                "byte_end": 11,
                "output_id": output_id,
            }],
        )

        assert result.success, result.content
        ev = result.data["evidence"][0]
        assert ev["artifact_ref"] == art_ref
        assert ev["byte_start"] == 0
        assert ev["byte_end"] == 11
        assert ev["excerpt_hash"]  # tamper-detection hash
        # Line range derived from index
        assert ev["line_start"] >= 0
        assert ev["line_end"] >= ev["line_start"]


# ---------------------------------------------------------------------------
# 2. CreateAssertionTool: validation failures
# ---------------------------------------------------------------------------

class TestCreateAssertionValidation:
    async def test_invalid_byte_span_rejected_no_partial_write(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        # byte_end beyond artifact length
        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Should fail",
            evidence=[{
                "artifact_ref": art_ref,
                "byte_start": 0,
                "byte_end": 99999,  # way beyond artifact size
            }],
        )

        assert not result.success
        assert "byte_end" in result.content.lower() or "span" in result.content.lower()

        # No assertion block should have been created
        state = await doc_store.get_state(inv_id, doc_id)
        assertions = [b for b in state["blocks"].values() if b["type"] == "assertion"]
        assert len(assertions) == 0

    async def test_artifact_ref_mismatch_with_output_id_rejected(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Mismatch test",
            evidence=[{
                "artifact_ref": "deadbeef" * 8,  # wrong sha256
                "byte_start": 0,
                "byte_end": 5,
                "output_id": output_id,
            }],
        )

        assert not result.success
        assert "artifact_ref" in result.content.lower()

    async def test_unknown_output_id_rejected(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Bad output_id",
            evidence=[{
                "artifact_ref": art_ref,
                "byte_start": 0,
                "byte_end": 5,
                "output_id": "nonexistent-output-id",
            }],
        )

        assert not result.success
        assert "output_id" in result.content.lower() or "not found" in result.content.lower()

    async def test_unknown_artifact_ref_rejected(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Unknown artifact",
            evidence=[{
                "artifact_ref": "a" * 64,  # valid-looking sha256 but not stored
                "byte_start": 0,
                "byte_end": 5,
            }],
        )

        assert not result.success
        assert "not found" in result.content.lower() or "artifact" in result.content.lower()

    async def test_missing_required_params(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            # claim missing
        )
        assert not result.success

    async def test_workflow_state_submitted_requires_evidence(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="No evidence",
            workflow_state="submitted",
        )
        assert not result.success

    async def test_workflow_state_submitted_stored(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Pre-submitted assertion",
            evidence=[{"artifact_ref": art_ref, "byte_start": 0, "byte_end": 5}],
            workflow_state="submitted",
        )

        assert result.success, result.content
        assert result.data["workflow_state"] == "submitted"

        state = await doc_store.get_state(inv_id, doc_id)
        block = state["blocks"][result.data["assertion_id"]]
        assert block["workflow_state"] == "submitted"


# ---------------------------------------------------------------------------
# 3. SubmitForReviewTool
# ---------------------------------------------------------------------------

class TestSubmitForReviewTool:
    async def test_draft_to_submitted(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        cr = await create_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="System X is compromised",
        )
        assert cr.success
        aid = cr.data["assertion_id"]

        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )

        assert sr.success, sr.content
        assert aid in sr.data["updated_states"]
        assert sr.data["updated_states"][aid] == "submitted"
        assert sr.data["pending_review_count"] >= 1

        # Verify state in store
        state = await doc_store.get_state(inv_id, doc_id)
        assert state["assertion_states"][aid] == "submitted"

    async def test_idempotent_already_submitted_skipped(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        cr = await create_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Idempotent test",
        )
        aid = cr.data["assertion_id"]

        # Submit once
        sr1 = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        assert sr1.success

        # Count events before second submit
        doc_before = await doc_store.get_document(inv_id, doc_id)
        rev_before = doc_before["current_revision"]

        # Submit again — should be idempotent (no new event)
        sr2 = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        assert sr2.success
        assert aid in sr2.data["skipped"]

        # No new event appended
        doc_after = await doc_store.get_document(inv_id, doc_id)
        assert doc_after["current_revision"] == rev_before

    async def test_approved_assertion_cannot_be_resubmitted(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        cr = await create_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Already decided",
        )
        aid = cr.data["assertion_id"]

        await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        await _approve_assertion(doc_store, inv_id, doc_id, aid)

        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        assert not sr.success
        assert "approved" in sr.content.lower()

    async def test_rejected_assertion_cannot_be_resubmitted(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        cr = await create_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Will be rejected",
        )
        aid = cr.data["assertion_id"]

        await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        await _reject_assertion(doc_store, inv_id, doc_id, aid)

        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        assert not sr.success
        assert "rejected" in sr.content.lower()

    async def test_nonexistent_assertion_returns_error(
        self, doc_store, artifact_store, inv_and_doc
    ):
        inv_id, doc_id = inv_and_doc
        submit_tool = make_submit_tool(doc_store)

        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=["does-not-exist"],
        )
        assert not sr.success

    async def test_missing_required_params(self, doc_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        submit_tool = make_submit_tool(doc_store)

        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            # assertion_ids missing
        )
        assert not sr.success

    async def test_pending_review_count_reflects_submitted(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        # Create two assertions, submit both
        cr1 = await create_tool.execute(
            investigation_id=inv_id, document_id=doc_id, claim="Claim one",
        )
        cr2 = await create_tool.execute(
            investigation_id=inv_id, document_id=doc_id, claim="Claim two",
        )

        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[cr1.data["assertion_id"], cr2.data["assertion_id"]],
        )

        assert sr.success
        assert sr.data["pending_review_count"] == 2


# ---------------------------------------------------------------------------
# 4. RegenerateNarrativeTool
# ---------------------------------------------------------------------------

class TestRegenerateNarrativeTool:
    async def _setup_approved_assertion(self, doc_store, artifact_store, inv_id, doc_id):
        """Helper: create, submit, and approve one assertion."""
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        cr = await create_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Root cause identified in auth service",
        )
        aid = cr.data["assertion_id"]
        await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        await _approve_assertion(doc_store, inv_id, doc_id, aid)
        return aid

    async def test_narrative_created_from_approved_assertions(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        await self._setup_approved_assertion(doc_store, artifact_store, inv_id, doc_id)

        doc = await doc_store.get_document(inv_id, doc_id)
        regen_tool = make_regen_tool(doc_store)

        rr = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            audience="internal",
            expected_revision=doc["current_revision"],
        )

        assert rr.success, rr.content
        assert rr.data["narrative_id"]
        assert rr.data["source_assertion_ids"]
        assert rr.data["audience"] == "internal"

        # Narrative block stored in document
        state = await doc_store.get_state(inv_id, doc_id)
        narr_block = state["blocks"][rr.data["narrative_id"]]
        assert narr_block["type"] == "narrative"
        assert narr_block["audience"] == "internal"
        assert narr_block["content"]

    async def test_blocked_if_no_approved_assertions(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        doc = await doc_store.get_document(inv_id, doc_id)
        regen_tool = make_regen_tool(doc_store)

        rr = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            audience="internal",
            expected_revision=doc["current_revision"],
        )

        assert not rr.success
        assert "approved" in rr.content.lower()

    async def test_revision_conflict_returned_as_structured_error(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        await self._setup_approved_assertion(doc_store, artifact_store, inv_id, doc_id)

        regen_tool = make_regen_tool(doc_store)
        rr = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            audience="internal",
            expected_revision=0,  # deliberately stale
        )

        assert not rr.success
        assert rr.data.get("conflict") is True
        assert "current_revision" in rr.data

    async def test_both_audiences_generate_distinct_narratives(
        self, doc_store, artifact_store, inv_with_output
    ):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        await self._setup_approved_assertion(doc_store, artifact_store, inv_id, doc_id)

        regen_tool = make_regen_tool(doc_store)

        doc1 = await doc_store.get_document(inv_id, doc_id)
        rr_int = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            audience="internal",
            expected_revision=doc1["current_revision"],
        )
        assert rr_int.success

        doc2 = await doc_store.get_document(inv_id, doc_id)
        rr_cust = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            audience="customer",
            expected_revision=doc2["current_revision"],
        )
        assert rr_cust.success

        # Different narrative IDs
        assert rr_int.data["narrative_id"] != rr_cust.data["narrative_id"]

        # Both stored
        state = await doc_store.get_state(inv_id, doc_id)
        narr_int = state["blocks"][rr_int.data["narrative_id"]]
        narr_cust = state["blocks"][rr_cust.data["narrative_id"]]
        assert narr_int["audience"] == "internal"
        assert narr_cust["audience"] == "customer"

    async def test_missing_required_params(self, doc_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        regen_tool = make_regen_tool(doc_store)

        rr = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            # expected_revision missing
        )
        assert not rr.success


# ---------------------------------------------------------------------------
# 5. End-to-end: ingest -> assert -> submit -> approve -> regen
# ---------------------------------------------------------------------------

class TestEndToEnd:
    async def test_full_investigation_loop(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc

        # Step 1: ingest evidence
        raw = b"ERROR 2024-01-15 auth.service: invalid token from 10.0.0.99\nRequest denied\n"
        ingest_result = await process_bytes_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=inv_id,
            document_id=doc_id,
            actor_id="agent:ise",
            actor_type="agent",
            actor_source="tool",
            raw=raw,
            filename="auth.log",
            content_type="text/plain",
        )
        art_ref = ingest_result["artifact_ref"]
        output_id = ingest_result["output_id"]

        # Step 2: create assertion
        create_tool = make_create_tool(doc_store, artifact_store)
        cr = await create_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Invalid token requests from 10.0.0.99 indicate credential compromise",
            evidence=[{
                "artifact_ref": art_ref,
                "byte_start": 0,
                "byte_end": 60,
                "output_id": output_id,
            }],
        )
        assert cr.success, cr.content
        aid = cr.data["assertion_id"]

        # Step 3: submit for review
        submit_tool = make_submit_tool(doc_store)
        sr = await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[aid],
        )
        assert sr.success
        assert sr.data["pending_review_count"] == 1

        # Verify assertion state is submitted
        state = await doc_store.get_state(inv_id, doc_id)
        assert state["assertion_states"][aid] == "submitted"

        # Step 4: human approval (simulated)
        await _approve_assertion(doc_store, inv_id, doc_id, aid)

        state2 = await doc_store.get_state(inv_id, doc_id)
        assert state2["assertion_states"][aid] == "approved"

        # Step 5: regen narrative
        doc = await doc_store.get_document(inv_id, doc_id)
        regen_tool = make_regen_tool(doc_store)
        rr = await regen_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            audience="internal",
            expected_revision=doc["current_revision"],
        )
        assert rr.success, rr.content
        assert rr.data["source_assertion_ids"] == [aid]

        # Verify narrative stored
        final_state = await doc_store.get_state(inv_id, doc_id)
        narr = final_state["blocks"][rr.data["narrative_id"]]
        assert narr["content"]
        assert narr["audience"] == "internal"
        assert aid in narr["source_assertion_ids"]


# ---------------------------------------------------------------------------
# 6. CreateAssertionTool: multiple spans — all-or-nothing
# ---------------------------------------------------------------------------

class TestMultipleEvidenceSpans:
    async def test_all_valid_spans_stored(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Two spans from same artifact",
            evidence=[
                {"artifact_ref": art_ref, "byte_start": 0, "byte_end": 5},
                {"artifact_ref": art_ref, "byte_start": 6, "byte_end": 11},
            ],
        )

        assert result.success, result.content
        assert len(result.data["evidence"]) == 2

    async def test_one_bad_span_rejects_all(self, doc_store, artifact_store, inv_with_output):
        inv_id, doc_id, art_ref, output_id = inv_with_output
        tool = make_create_tool(doc_store, artifact_store)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            claim="Partial fail test",
            evidence=[
                {"artifact_ref": art_ref, "byte_start": 0, "byte_end": 5},   # valid
                {"artifact_ref": art_ref, "byte_start": 0, "byte_end": 99999},  # invalid
            ],
        )

        assert not result.success
        # No assertion block written at all
        state = await doc_store.get_state(inv_id, doc_id)
        assertions = [b for b in state["blocks"].values() if b["type"] == "assertion"]
        assert len(assertions) == 0


# ---------------------------------------------------------------------------
# 7. build_document_model_context
# ---------------------------------------------------------------------------

class TestBuildDocumentModelContext:
    async def test_empty_doc_returns_empty_string(self, doc_store, inv_and_doc):
        from workbench.web.routes.investigations import build_document_model_context

        inv_id, doc_id = inv_and_doc
        ctx = await build_document_model_context(doc_store, inv_id, doc_id)
        # Empty doc (no assertions, no narratives) still returns context header
        assert "Agent Investigation Context" in ctx or ctx == ""

    async def test_reflects_counts_after_approve_and_submit(
        self, doc_store, artifact_store, inv_with_output
    ):
        from workbench.web.routes.investigations import build_document_model_context

        inv_id, doc_id, art_ref, output_id = inv_with_output
        create_tool = make_create_tool(doc_store, artifact_store)
        submit_tool = make_submit_tool(doc_store)

        # Create two assertions, submit both, approve one
        cr1 = await create_tool.execute(
            investigation_id=inv_id, document_id=doc_id, claim="Claim A",
        )
        cr2 = await create_tool.execute(
            investigation_id=inv_id, document_id=doc_id, claim="Claim B",
        )

        await submit_tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            assertion_ids=[cr1.data["assertion_id"], cr2.data["assertion_id"]],
        )
        await _approve_assertion(doc_store, inv_id, doc_id, cr1.data["assertion_id"])

        ctx = await build_document_model_context(doc_store, inv_id, doc_id)

        assert "1 approved" in ctx
        assert "1 pending review" in ctx

    async def test_bad_ids_returns_empty(self, doc_store):
        from workbench.web.routes.investigations import build_document_model_context

        ctx = await build_document_model_context(doc_store, "nonexistent-inv", "nonexistent-doc")
        assert ctx == ""
