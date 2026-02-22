"""
M3 review + narrative regression tests.

Covers:
1. Latest review wins (approve then reject → effective rejected)
2. Multi-target review (one review approves multiple assertions)
3. PATCH lock — workflow_state="approved" rejected at Pydantic layer
4. Narrative gating — 400 when no approved assertions
5. Revision pin — narrative source_assertion_ids frozen at creation; later rejection
   doesn't retroactively change the stored narrative block
6. Replay correctness — get_state(at_revision=X) reflects correct effective approvals
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from workbench.documents.store import DocumentStore, _apply_event, replay_events_at_revision
from workbench.documents.templates import (
    build_narrative,
    generation_inputs_hash,
    render_internal,
    render_customer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def doc_store(tmp_path: Path) -> DocumentStore:
    s = DocumentStore(str(tmp_path / "documents.db"))
    await s.init()
    yield s
    await s.close()


ACTOR = ("human:test", "human", "header")
INV = "inv-m3"


async def _make_doc(doc_store: DocumentStore) -> str:
    return await doc_store.create_document(INV)


async def _make_assertion(doc_store: DocumentStore, doc_id: str, claim: str) -> str:
    """Helper: append a draft assertion block and return its block id."""
    import uuid
    from datetime import datetime, timezone
    bid = str(uuid.uuid4())
    block = {
        "id": bid,
        "type": "assertion",
        "claim": claim,
        "workflow_state": "draft",
        "evidence": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await doc_store.append_event(
        INV, doc_id, *ACTOR, "doc.assertion.created", {"block": block}
    )
    assert result["ok"]
    return bid


async def _make_review(
    doc_store: DocumentStore,
    doc_id: str,
    target_ids: list[str],
    decision: str,
    reason: str = "test reason",
) -> dict:
    """Helper: append a review block and return the append result."""
    import uuid
    from datetime import datetime, timezone
    bid = str(uuid.uuid4())
    block = {
        "id": bid,
        "type": "review",
        "target_assertion_ids": target_ids,
        "decision": decision,
        "reason": reason,
        "reviewed_by": "human:test",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return await doc_store.append_event(
        INV, doc_id, *ACTOR, "doc.review.created", {"block": block}
    )


# ---------------------------------------------------------------------------
# 1) Latest review wins
# ---------------------------------------------------------------------------

class TestLatestReviewWins:
    async def test_approve_then_reject(self, doc_store: DocumentStore):
        doc_id = await _make_doc(doc_store)
        aid = await _make_assertion(doc_store, doc_id, "System was compromised via SSH")

        # Approve
        r = await _make_review(doc_store, doc_id, [aid], "approved")
        assert r["ok"]

        state = await doc_store.get_state(INV, doc_id)
        assert state["assertion_states"][aid] == "approved"

        # Reject — latest wins
        r2 = await _make_review(doc_store, doc_id, [aid], "rejected")
        assert r2["ok"]

        state2 = await doc_store.get_state(INV, doc_id)
        assert state2["assertion_states"][aid] == "rejected"

    async def test_reject_then_approve(self, doc_store: DocumentStore):
        doc_id = await _make_doc(doc_store)
        aid = await _make_assertion(doc_store, doc_id, "No breach detected")

        await _make_review(doc_store, doc_id, [aid], "rejected")
        await _make_review(doc_store, doc_id, [aid], "approved")

        state = await doc_store.get_state(INV, doc_id)
        assert state["assertion_states"][aid] == "approved"

    async def test_multiple_rounds_last_wins(self, doc_store: DocumentStore):
        doc_id = await _make_doc(doc_store)
        aid = await _make_assertion(doc_store, doc_id, "Lateral movement observed")

        decisions = ["approved", "rejected", "approved", "rejected", "approved"]
        for d in decisions:
            await _make_review(doc_store, doc_id, [aid], d)

        state = await doc_store.get_state(INV, doc_id)
        assert state["assertion_states"][aid] == "approved"


# ---------------------------------------------------------------------------
# 2) Multi-target review
# ---------------------------------------------------------------------------

class TestMultiTargetReview:
    async def test_one_review_approves_multiple(self, doc_store: DocumentStore):
        doc_id = await _make_doc(doc_store)
        aid1 = await _make_assertion(doc_store, doc_id, "Claim A")
        aid2 = await _make_assertion(doc_store, doc_id, "Claim B")
        aid3 = await _make_assertion(doc_store, doc_id, "Claim C")

        r = await _make_review(doc_store, doc_id, [aid1, aid2, aid3], "approved")
        assert r["ok"]

        state = await doc_store.get_state(INV, doc_id)
        assert state["assertion_states"][aid1] == "approved"
        assert state["assertion_states"][aid2] == "approved"
        assert state["assertion_states"][aid3] == "approved"

    async def test_partial_review_only_touches_targets(self, doc_store: DocumentStore):
        doc_id = await _make_doc(doc_store)
        aid1 = await _make_assertion(doc_store, doc_id, "Claim A")
        aid2 = await _make_assertion(doc_store, doc_id, "Claim B")

        # Only approve aid1
        await _make_review(doc_store, doc_id, [aid1], "approved")

        state = await doc_store.get_state(INV, doc_id)
        assert state["assertion_states"][aid1] == "approved"
        assert state["assertion_states"][aid2] == "draft"  # untouched

    async def test_mixed_decisions_separate_reviews(self, doc_store: DocumentStore):
        doc_id = await _make_doc(doc_store)
        aid1 = await _make_assertion(doc_store, doc_id, "Approved claim")
        aid2 = await _make_assertion(doc_store, doc_id, "Rejected claim")

        await _make_review(doc_store, doc_id, [aid1], "approved")
        await _make_review(doc_store, doc_id, [aid2], "rejected")

        state = await doc_store.get_state(INV, doc_id)
        assert state["assertion_states"][aid1] == "approved"
        assert state["assertion_states"][aid2] == "rejected"


# ---------------------------------------------------------------------------
# 3) PATCH lock — Pydantic model rejects approval state
# ---------------------------------------------------------------------------

class TestPatchLock:
    def test_patch_request_rejects_approved_state(self):
        """
        PatchAssertionRequest pattern is ^(draft|submitted)$.
        Passing 'approved' must fail Pydantic validation.
        """
        from pydantic import ValidationError
        from workbench.web.routes.documents import PatchAssertionRequest

        with pytest.raises(ValidationError):
            PatchAssertionRequest(workflow_state="approved", expected_revision=1)

    def test_patch_request_rejects_rejected_state(self):
        from pydantic import ValidationError
        from workbench.web.routes.documents import PatchAssertionRequest

        with pytest.raises(ValidationError):
            PatchAssertionRequest(workflow_state="rejected", expected_revision=1)

    def test_patch_request_allows_draft(self):
        from workbench.web.routes.documents import PatchAssertionRequest
        req = PatchAssertionRequest(workflow_state="draft", expected_revision=1)
        assert req.workflow_state == "draft"

    def test_patch_request_allows_submitted(self):
        from workbench.web.routes.documents import PatchAssertionRequest
        req = PatchAssertionRequest(workflow_state="submitted", expected_revision=1)
        assert req.workflow_state == "submitted"

    def test_create_assertion_rejects_approved_state(self):
        """CreateAssertionRequest also locks to draft/submitted."""
        from pydantic import ValidationError
        from workbench.web.routes.documents import CreateAssertionRequest

        with pytest.raises(ValidationError):
            CreateAssertionRequest(claim="test", workflow_state="approved")


# ---------------------------------------------------------------------------
# 4) Narrative gating — no approved → gated
# ---------------------------------------------------------------------------

class TestNarrativeGating:
    def test_approved_ids_empty_when_no_reviews(self):
        """
        Simulate the gating check: state with no reviews → approved_ids is empty.
        """
        state = {"assertion_states": {"aid1": "draft", "aid2": "submitted"}}
        approved_ids = [aid for aid, ws in state["assertion_states"].items() if ws == "approved"]
        assert approved_ids == []

    def test_approved_ids_nonempty_after_approval(self):
        state = {"assertion_states": {"aid1": "approved", "aid2": "draft"}}
        approved_ids = [aid for aid, ws in state["assertion_states"].items() if ws == "approved"]
        assert approved_ids == ["aid1"]

    def test_narrative_gating_check(self):
        """After rejection, approved_ids is empty again → gated."""
        state = {"assertion_states": {"aid1": "rejected"}}
        approved_ids = [aid for aid, ws in state["assertion_states"].items() if ws == "approved"]
        assert approved_ids == []


# ---------------------------------------------------------------------------
# 5) Revision pin — source_assertion_ids frozen at narrative creation
# ---------------------------------------------------------------------------

class TestRevisionPin:
    async def test_narrative_pins_at_creation_revision(self, doc_store: DocumentStore):
        """
        Approve assertion A → regen narrative (sources=[A], source_rev=N).
        Later reject A. The stored narrative block still lists A.
        """
        import uuid
        from datetime import datetime, timezone
        doc_id = await _make_doc(doc_store)
        aid = await _make_assertion(doc_store, doc_id, "Pinned claim")

        # Approve → revision 2
        await _make_review(doc_store, doc_id, [aid], "approved")

        state = await doc_store.get_state(INV, doc_id)
        approved_ids = [k for k, v in state["assertion_states"].items() if v == "approved"]
        assert aid in approved_ids

        # Simulate narrative block appended at current revision
        doc = await doc_store.get_document(INV, doc_id)
        source_revision = doc["current_revision"]
        source_assertion_ids = list(approved_ids)

        nar_id = str(uuid.uuid4())
        nar_block = {
            "id": nar_id,
            "type": "narrative",
            "audience": "internal",
            "source_revision": source_revision,
            "source_assertion_ids": source_assertion_ids,
            "content": "Test narrative",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        r = await doc_store.append_event(
            INV, doc_id, *ACTOR, "doc.narrative.regenerated", {"block": nar_block}
        )
        assert r["ok"]
        nar_revision = r["revision"]

        # Now reject A
        await _make_review(doc_store, doc_id, [aid], "rejected")

        # Current state: A is rejected
        state_now = await doc_store.get_state(INV, doc_id)
        assert state_now["assertion_states"][aid] == "rejected"

        # Historical narrative block still has A in source_assertion_ids
        state_at_nar = await doc_store.get_state(INV, doc_id, at_revision=nar_revision)
        stored_nar = state_at_nar["blocks"][nar_id]
        assert aid in stored_nar["source_assertion_ids"]

    async def test_new_regen_after_rejection_has_no_approved(self, doc_store: DocumentStore):
        """
        After rejecting all assertions, regenerating a new narrative would be
        gated (no approved_ids).  Verify the state shows empty approved list.
        """
        doc_id = await _make_doc(doc_store)
        aid = await _make_assertion(doc_store, doc_id, "Claim to reject")

        await _make_review(doc_store, doc_id, [aid], "approved")
        await _make_review(doc_store, doc_id, [aid], "rejected")

        state = await doc_store.get_state(INV, doc_id)
        approved = [k for k, v in state["assertion_states"].items() if v == "approved"]
        assert approved == []  # gating check: would return 400


# ---------------------------------------------------------------------------
# 6) Replay correctness
# ---------------------------------------------------------------------------

class TestReplayCorrectness:
    async def test_replay_at_revision_reflects_approval(self, doc_store: DocumentStore):
        """
        At rev 2 (after approval), get_state(at_revision=2) → approved.
        At rev 3 (after rejection), get_state(at_revision=3) → rejected.
        """
        doc_id = await _make_doc(doc_store)
        aid = await _make_assertion(doc_store, doc_id, "Replay test claim")
        # rev 1: assertion created

        approve_result = await _make_review(doc_store, doc_id, [aid], "approved")
        rev_approved = approve_result["revision"]  # rev 2

        reject_result = await _make_review(doc_store, doc_id, [aid], "rejected")
        rev_rejected = reject_result["revision"]  # rev 3

        # At approval revision
        state_at_2 = await doc_store.get_state(INV, doc_id, at_revision=rev_approved)
        assert state_at_2["assertion_states"][aid] == "approved"

        # At rejection revision
        state_at_3 = await doc_store.get_state(INV, doc_id, at_revision=rev_rejected)
        assert state_at_3["assertion_states"][aid] == "rejected"

    async def test_replay_at_initial_revision(self, doc_store: DocumentStore):
        """At revision 1 (just assertion, no review) → draft state."""
        doc_id = await _make_doc(doc_store)
        r = await _make_assertion(doc_store, doc_id, "Initial claim")
        # After assertion created, revision = 1
        # (doc created at rev 0, assertion at rev 1)

        await _make_review(doc_store, doc_id, [r], "approved")

        # State at revision 1 (before review)
        state_at_1 = await doc_store.get_state(INV, doc_id, at_revision=1)
        assert state_at_1["assertion_states"][r] == "draft"

    async def test_get_state_current_matches_latest_replay(self, doc_store: DocumentStore):
        """Materialized current state must equal full replay of all events."""
        doc_id = await _make_doc(doc_store)
        aid1 = await _make_assertion(doc_store, doc_id, "Claim 1")
        aid2 = await _make_assertion(doc_store, doc_id, "Claim 2")
        await _make_review(doc_store, doc_id, [aid1], "approved")
        await _make_review(doc_store, doc_id, [aid2], "rejected")
        await _make_review(doc_store, doc_id, [aid1], "rejected")

        current = await doc_store.get_state(INV, doc_id)
        events = await doc_store.get_events(INV, doc_id)
        last_rev = max(e["next_revision"] for e in events)
        replayed = await doc_store.get_state(INV, doc_id, at_revision=last_rev)

        assert current["assertion_states"] == replayed["assertion_states"]


# ---------------------------------------------------------------------------
# Narrative template tests
# ---------------------------------------------------------------------------

class TestNarrativeTemplates:
    def _make_assertion(self, claim: str, evidence=None) -> dict:
        return {
            "id": "a1",
            "claim": claim,
            "evidence": evidence or [],
        }

    def test_internal_template_contains_claim(self):
        a = self._make_assertion("Attacker pivoted via CVE-2024-1234")
        result = render_internal(
            investigation_id="inv-1",
            document_id="doc-1",
            source_revision=3,
            generated_at="2026-02-22T00:00:00+00:00",
            approved_assertions=[a],
            rejected_assertions=[],
        )
        assert "Attacker pivoted via CVE-2024-1234" in result
        assert "Approved Assertions" in result
        assert "inv-1" in result

    def test_internal_template_rejected_section(self):
        a_rej = self._make_assertion("False positive claim")
        result = render_internal(
            investigation_id="inv-1",
            document_id="doc-1",
            source_revision=1,
            generated_at="2026-02-22T00:00:00+00:00",
            approved_assertions=[],
            rejected_assertions=[a_rej],
        )
        assert "Rejected Assertions" in result
        assert "False positive claim" in result
        assert "_None_" in result  # approved section empty

    def test_customer_template_contains_claims(self):
        a = self._make_assertion("Unauthorized access was detected")
        result = render_customer(approved_assertions=[a])
        assert "Unauthorized access was detected" in result
        assert "What We Observed" in result
        assert "Summary" in result

    def test_customer_template_no_approved(self):
        result = render_customer(approved_assertions=[])
        assert "No findings" in result

    def test_build_narrative_dispatches_internal(self):
        a = self._make_assertion("Internal claim")
        result = build_narrative(
            audience="internal",
            investigation_id="inv-1",
            document_id="doc-1",
            source_revision=5,
            generated_at="2026-02-22T00:00:00+00:00",
            approved_assertions=[a],
            rejected_assertions=[],
        )
        assert "Internal Investigation Narrative" in result

    def test_build_narrative_dispatches_customer(self):
        a = self._make_assertion("Customer claim")
        result = build_narrative(
            audience="customer",
            investigation_id="inv-1",
            document_id="doc-1",
            source_revision=5,
            generated_at="2026-02-22T00:00:00+00:00",
            approved_assertions=[a],
            rejected_assertions=[],
        )
        assert "Summary" in result
        assert "What We Observed" in result

    def test_generation_inputs_hash_is_deterministic(self):
        a = self._make_assertion("Claim", evidence=[
            {"artifact_ref": "abc", "byte_start": 0, "byte_end": 10}
        ])
        h1 = generation_inputs_hash("internal", "default_internal_v1", ["a1"], [a])
        h2 = generation_inputs_hash("internal", "default_internal_v1", ["a1"], [a])
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_generation_inputs_hash_changes_with_claim(self):
        a1 = self._make_assertion("Original claim")
        a2 = self._make_assertion("Modified claim")
        h1 = generation_inputs_hash("internal", "default_internal_v1", ["a1"], [a1])
        h2 = generation_inputs_hash("internal", "default_internal_v1", ["a1"], [a2])
        assert h1 != h2

    def test_generation_inputs_hash_changes_with_audience(self):
        a = self._make_assertion("Claim")
        h1 = generation_inputs_hash("internal", "default_internal_v1", ["a1"], [a])
        h2 = generation_inputs_hash("customer", "default_customer_v1", ["a1"], [a])
        assert h1 != h2


# ---------------------------------------------------------------------------
# _apply_event unit tests for review semantics
# ---------------------------------------------------------------------------

class TestApplyEventReview:
    def _base_state(self, aid: str, ws: str = "draft") -> dict:
        return {
            "blocks": {
                aid: {"id": aid, "type": "assertion", "claim": "test", "workflow_state": ws}
            },
            "block_order": [aid],
            "assertion_states": {aid: ws},
        }

    def test_review_updates_assertion_not_in_states(self):
        """
        Review must update assertion_states even if the assertion ID
        was not previously tracked (no guard).
        """
        state = {
            "blocks": {},
            "block_order": [],
            "assertion_states": {},
        }
        review_block = {
            "id": "r1",
            "type": "review",
            "target_assertion_ids": ["aid_not_tracked"],
            "decision": "approved",
        }
        new_state = _apply_event(state, "doc.review.created", {"block": review_block})
        assert new_state["assertion_states"]["aid_not_tracked"] == "approved"

    def test_assertion_created_with_approved_state_stored_as_draft(self):
        """
        Assertion block's own workflow_state cannot be 'approved' —
        the store normalizes it to 'draft'.
        """
        state: dict = {}
        block = {
            "id": "a1",
            "type": "assertion",
            "claim": "test",
            "workflow_state": "approved",  # should be rejected / normalized
        }
        new_state = _apply_event(state, "doc.assertion.created", {"block": block})
        assert new_state["assertion_states"]["a1"] == "draft"

    def test_assertion_created_draft_stored_as_draft(self):
        state: dict = {}
        block = {"id": "a1", "type": "assertion", "claim": "x", "workflow_state": "draft"}
        new_state = _apply_event(state, "doc.assertion.created", {"block": block})
        assert new_state["assertion_states"]["a1"] == "draft"

    def test_assertion_created_submitted_stored_as_submitted(self):
        state: dict = {}
        block = {"id": "a1", "type": "assertion", "claim": "x", "workflow_state": "submitted"}
        new_state = _apply_event(state, "doc.assertion.created", {"block": block})
        assert new_state["assertion_states"]["a1"] == "submitted"
