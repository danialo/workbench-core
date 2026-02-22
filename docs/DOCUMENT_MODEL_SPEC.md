# ISE Document Model Build Spec (repo-aligned v1.2, fixed + complete)

## Purpose

This spec defines a repo-aligned document model that can be implemented with minimal churn while still delivering a trustworthy evidence and review loop.

Primary vertical slice (v1):

`command -> output(artifact + index) -> assertion(with stable evidence pointers) -> review -> narrative (approved assertions only)`

Core principle:

Evidence bytes are immutable. Interpretation is editable. Customer text is derived.

---

## A/B comparison summary (proposal vs repo reality)

### A) Keep as-is (already compatible)
- Content-addressed immutable artifacts with checksum verification.
- Append-only event logging model.
- Portable core schema with company-specific overlays.
- Dual evidence pointers: display line spans + stable byte offsets.

### B) Redefine for repo fit (required)
1. **Identity model**
   - Anchor all docs under `investigation_id`.
   - Keep `session_id` as a provenance dimension, not a primary key.
   - Introduce `document_id` scoped to an investigation.

2. **API prefix**
   - Use existing web domain root: `/api/investigations/{investigation_id}/documents/...`.

3. **Event namespace**
   - Namespace document events to avoid collisions: `doc.*`.

4. **Concurrency contract**
   - Preserve append-only inserts without revision checks.
   - Require `expected_revision` only for mutating operations (assertion edits, narrative regen, any future mutation).

### C) Remove from v1 (too early for this repo)
- Mandatory prompt/model hashes for all narratives (keep optional provenance fields).
- Cross-investigation joins.
- Complex policy orchestration in the document layer (phase 2+).

---

## Repository constraints this spec respects

- The repo already has a content-addressed `ArtifactStore` with SHA-256 identity and immutable-by-hash behavior.
- The repo already uses append-only session events (`SessionStore.append_event` in SQLite). Document history should follow the same philosophy.
- The existing domain entity is `investigation` (`/api/investigations`), so documents nest under investigations.

---

## Storage decisions (repo-resolved)

- **Documents DB**: Separate `documents.db` — clean domain boundary, independent migration chain, no cross-domain entanglement with session/investigation data.
- **Actor identity**: `X-Actor-Id` / `X-Actor-Type` request headers take precedence; derive from session context if available; deterministic placeholder fallback (`human:unknown`, `agent:ise`, `system:ise`). Every `doc.*` event records `actor_id`, `actor_type`, `actor_source`.

---

## v1 scope and non-goals

### In scope (v1)
- Core block graph for:
  - `command`
  - `output`
  - `assertion`
  - `review`
  - `narrative` (derived)
  - `derivation` (only when transforms exist)
- Immutable artifact references backed by existing artifact store
- Output indexing with dual evidence pointers:
  - display: `line_start`, `line_end`
  - stable: `byte_start`, `byte_end` relative to the cited `artifact_ref`
- Evidence resolver path (assertion -> span -> output artifact)
- Review workflow
- Narrative regeneration from approved assertions only
- Deterministic replay of document state by revision

### Explicitly deferred to phase 2+
- `decision` orchestration and policy gates
- Full `validation` framework (allow placeholder block only)
- Autonomy/auto-remediation workflows
- Cross-investigation graph joins
- Advanced semantic indexing beyond lightweight search/highlight

---

## Architecture decisions

## 1) Storage domains (repo-aligned)

### A. Document graph store (new — separate `documents.db`)
Stores blocks and references only. No raw output bodies.

Keyed by:
- `investigation_id`
- `document_id`
- `revision` (monotonic integer)

Data shape:
- Blocks are identified by `block_id` and referenced by ID only, never by array position.
- Graph is treated as append-only; "updates" are new events plus current-state materialization.

### B. Artifact store (existing)
Use `workbench.session.artifacts.ArtifactStore` as the source of truth for immutable bytes.

Requirements:
- Output artifacts must be stored as raw bytes in ArtifactStore.
- The `artifact_ref` must be stable and content-addressed (SHA-256 identity).
- Retrieval must verify checksum.

### C. Index store (new, derived — same `documents.db`)
Derived per artifact; rebuildable.

Per-artifact index record must include:
- `artifact_ref`
- `index_ref`
- `index_version`
- `indexer_build`
- `indexed_at`
- `line_map` (line -> byte start/end)
- `reverse_map` (byte -> line range)
- optional `search_index` (for highlight/search)

---

## 2) Event-first write model (repo-aligned)

All document changes are persisted as append-only events. The system must be able to reconstruct document state for any revision.

### Event envelope (required)
- `event_id`
- `investigation_id`
- `document_id`
- `actor_id`
- `actor_type`
- `actor_source`
- `event_type`
- `occurred_at`
- `payload`
- `prior_revision`
- `next_revision`

### Event types (required set for v1)
- `doc.command.created`
- `doc.output.created`
- `doc.assertion.created`
- `doc.assertion.patched`
- `doc.review.created`
- `doc.narrative.regenerated`
- `doc.derivation.created` (only when transforms exist)

### Replay requirement
- Given all events for a `(investigation_id, document_id)`, the system must reconstruct the document graph deterministically for any `revision`.

### Revision behavior
- Every accepted event increments the document `revision`.
- Append-only creates can be accepted without `expected_revision`.
- Mutations must enforce `expected_revision` (details below).

---

## 3) Evidence stability rules (non-negotiable)

Evidence spans must remain resolvable even when the UI switches between raw and transformed outputs.

Rules:
1. Evidence byte offsets are always relative to an explicit `artifact_ref`.
2. If output bytes are transformed (redaction, normalization, parsing/chunking), the transform must be represented via a `derivation` block that links input and output artifacts and provides a mapping reference.
3. Customer views must never be able to resolve to redacted bytes.

---

## Canonical blocks (v1)

### Common block fields (required for all blocks)
- `id` (unique within document)
- `type`
- `created_at`
- `created_by`

Blocks are referenced by `id`. Deletes are tombstones via events (no hard deletes).

---

## `command`
Represents a tool execution request and run context.

Required:
- `type="command"`
- `tool`
- `executor` (`human|agent`)
- `run_context`:
  - `workspace`
  - `identity`
  - `policy_scope`
- `input.command`
- `started_at`
- `finished_at`
- `exit_code`

Recommended (optional in first iteration):
- `exec_artifact_ref` (execution metadata artifact, distinct from stdout/stderr)

Optional:
- `input.args`
- `labels[]`
- `error_summary`

---

## `output`
Represents a command output stream backed by an immutable artifact.

Required:
- `type="output"`
- `source_command_id`
- `stream` (`stdout|stderr|combined`)
- `artifact_ref` (ArtifactStore ref)
- `checksum` (sha-256 of artifact bytes)
- `byte_length`
- `line_count`
- `index_ref`
- `index_version`
- `truncated` (boolean)

Required content metadata (for byte offset integrity):
- `content_type` (e.g., `text/plain`, `application/json`)
- `content_encoding` (e.g., `utf-8`)
- `newline_mode` (`lf|crlf|mixed|unknown`)

Optional:
- `indexed_at`
- `indexer_build`
- `provenance` (connector/tool metadata)

---

## `assertion`
A claim backed by immutable evidence spans.

Workflow state (v1):
- `draft|submitted|approved|rejected`

Required:
- `type="assertion"`
- `claim`
- `workflow_state`
- `authored_by`
- `authored_at`
- `evidence[]` required when `workflow_state` is `submitted|approved`

Evidence object required fields:
- `output_id` (convenience pointer to an `output` block)
- `artifact_ref` (authoritative pointer for `byte_*`)
- `line_start`, `line_end` (display)
- `byte_start`, `byte_end` (stable offsets, relative to `artifact_ref`)

Optional evidence fields:
- `excerpt_hash` (hash of the cited bytes for quick integrity checks)
- `note`

Mutation rules (v1):
- Editing an assertion claim or evidence requires `doc.assertion.patched` and `expected_revision`.
- Assertions become `approved` only via `review` decisions, not by direct patching.

---

## `review`
A reviewer decision applied to one or more assertions.

Required:
- `type="review"`
- `target_assertion_ids[]`
- `decision` (`approved|rejected`)
- `reason` (mandatory free text)
- `reviewed_by`
- `reviewed_at`

Optional:
- `reason_code` (for analytics)
- `scope` (default `assertion`, reserved for phase 2+)

Effect rules:
- A review decision updates the effective approval state for each target assertion in materialized view.
- History is preserved as additional review blocks, not overwritten.

---

## `narrative` (derived)
Generated summary text derived only from approved assertions.

Required:
- `type="narrative"`
- `source_assertion_ids[]` (must all be approved at `source_revision`)
- `source_revision`
- `audience` (`internal|customer`)
- `render_format` (`markdown|plain|html`)
- `content`
- `generated_at`
- `generation_inputs_hash`

Optional provenance (allowed in v1, not mandatory):
- `model_id`
- `model_params_hash`
- `prompt_hash`
- `template_id`

Rules:
- Narrative regeneration is blocked if there are zero approved assertions.
- Narrative regeneration must record `source_revision`.

---

## `derivation` (only if transforms exist)
Represents a transformation from one artifact to another with an explicit mapping reference.

Required when present:
- `type="derivation"`
- `input_artifact_ref`
- `output_artifact_ref`
- `transform_type` (`redaction|normalization|parsing|chunking|other`)
- `transform_version`
- `mapping_ref` (redaction map or offset map)

Rules:
- If a transform changes byte layout, evidence that cites the transformed bytes must cite `output_artifact_ref`.
- UI must be able to resolve evidence spans against the cited artifact and, when needed, use `mapping_ref` to relate back to the original.

---

## API contract (repo-aligned)

Base path:
- `/api/investigations/{investigation_id}/documents/{document_id}`

### Minimum endpoints (v1)
- `POST /commands`
- `POST /commands/{command_id}/outputs`
- `POST /assertions`
- `PATCH /assertions/{assertion_id}`
- `POST /reviews`
- `POST /narratives:regenerate`
- `GET  ?include=graph`
- `GET  /evidence/{assertion_id}`

### Concurrency contract
- Append-only creates (`POST` new blocks): no `expected_revision` required.
- Mutations (`PATCH` and `narratives:regenerate`): require `expected_revision`.

Conflict behavior:
- Return `409` with:
  - `current_revision`
  - list of changed block IDs since `expected_revision` (minimal merge hint)

### Immutability rules
- Artifacts are immutable by hash, no updates.
- Indexes are rebuildable, index versions can advance without mutating artifacts.
- The graph is append-only in storage; current state is materialized from events.

---

## Indexing requirements (keystone subsystem)

For each `output` artifact:
1. Persist line map: `line_number -> (byte_start, byte_end)`
2. Persist reverse map: `(byte_start, byte_end) -> (line_start, line_end)`
3. Optionally persist lightweight highlight/search index
4. Persist:
   - `index_ref`
   - `index_version`
   - `indexer_build`
   - `indexed_at`

Evidence objects must carry:
- display pointers: `line_start`, `line_end`
- stable pointers: `byte_start`, `byte_end` relative to `artifact_ref`

Integrity requirements:
- Indexer must honor `content_encoding` and `newline_mode`.
- Evidence resolution must clamp spans safely and reject out-of-range offsets.

---

## UI acceptance (v1)

### Investigation view
- Timeline shows command executions (status, exit code, duration).
- Output viewer shows line numbers and supports span highlight.
- Assertions show evidence badges and "Show evidence" jumps to cited span.

### Review flow
- Approve/reject requires a reason.
- Reviewer identity and timestamp displayed.
- Assertion history shows review events.

### Narrative view
- Regenerate from approved assertions only.
- Display which assertions were used and the `source_revision`.
- Block regeneration when there are zero approved assertions.

---

## Security and compliance

- Verify artifact checksum on retrieval.
- Represent redaction/normalization as explicit `derivation` blocks.
- Require `actor_id` for all mutating operations.
- Customer narrative and evidence views must not expose redacted bytes.

---

## Delivery sequence

### Milestone 1: repository-integrated foundations
- Add document graph tables under investigations domain.
- Reuse existing ArtifactStore for output artifacts.
- Add document event stream and deterministic replay.

Exit criteria:
- can create `command` + `output` referencing ArtifactStore artifacts
- revision increments via document events
- can materialize doc state for any revision

### Milestone 2: indexing + evidence loop
- Implement per-artifact index records (line + byte mapping).
- Implement assertion creation with dual-pointer evidence.
- Implement evidence resolver endpoint and UI jump/highlight.

Exit criteria:
- evidence resolver returns correct excerpt and highlight boundaries
- assertions render evidence reliably across encodings/newline modes

### Milestone 3: review + narrative loop
- Implement `review` blocks and APIs.
- Implement narrative regeneration from approved assertions only.
- Persist narrative provenance (`source_revision`, hashes).

Exit criteria:
- full vertical slice demo works end-to-end
- narrative regeneration blocked with zero approved assertions

### Milestone 4: connector overlay readiness
- Add connector metadata adapter(s) that emit the same command/output contracts.
- Confirm no core block schema change needed.

Exit criteria:
- second data source can emit command/output artifacts into the same model

---

## Definition of done (v1)

1. A command/output pair is captured with immutable artifact refs and checksum verification.
2. Assertions cite stable evidence via line + byte pointers tied to `artifact_ref`.
3. Reviews are first-class blocks with auditable history.
4. Narrative derives only from approved assertions and records `source_revision`.
5. Document state can be replayed deterministically at any revision.
6. Implementation is investigation-domain-native and does not fork core schema for connectors.
