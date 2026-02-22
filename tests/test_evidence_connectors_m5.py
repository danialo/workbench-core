"""
M5 evidence connector tests.

Covers:
1.  IngestCommandOutputTool creates command + output blocks
2.  Artifact stored, checksum correct, dedup works (same bytes = same ref)
3.  Index built for text output; not built for binary-flagged output
4.  Truncation: output > max_bytes is clamped and truncated=True recorded
5.  Backend truncation flag propagates to output block
6.  IngestRemoteFileTool (localhost) reads file directly
7.  IngestRemoteFileTool (SSH) fetches via cat
8.  IngestRemoteFileTool rejects binary on SSH (file -b returns non-text MIME)
9.  IngestRemoteFileTool: local file not found
10. Evidence resolver (resolve_span) works on command-ingested output
11. Actor fields recorded correctly (agent:ise)
12. Block shapes match M4: command.tool, output.artifact_ref, provenance
13. process_bytes_ingest is importable from workbench.documents.ingest (API test)
14. Backward-compat: _process_file_ingest, _is_indexable still importable from routes
15. MAX_COMMAND_BYTES constant exported from ingest module
16. Empty output is rejected (tool returns success=False)
17. Missing required params returns success=False without crashing
18. Document-not-found guard works on both tools
19. Backend error surfaced in ToolResult
20. IngestCommandOutputTool: combined stream merges stdout + stderr
21. IngestCommandOutputTool: stderr stream
22. Revision increments correctly across two ingests
23. index_ref is None for non-indexable content type on command output
24. process_bytes_ingest: tool= and command_str= fields land in blocks
25. process_bytes_ingest: run_context overridable
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from workbench.documents.store import DocumentStore
from workbench.documents.indexer import resolve_span, index_bytes
from workbench.documents.ingest import (
    process_bytes_ingest,
    is_indexable,
    INDEX_SIZE_THRESHOLD,
    MAX_UPLOAD_SIZE,
    MAX_COMMAND_BYTES,
)
from workbench.session.artifacts import ArtifactStore
from workbench.tools.evidence_tools import IngestCommandOutputTool, IngestRemoteFileTool


# ---------------------------------------------------------------------------
# Fake backend
# ---------------------------------------------------------------------------

class FakeBackend:
    """Configurable fake backend for testing without real processes or SSH."""

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        truncated: dict | None = None,
        timed_out: bool = False,
        file_mime: str = "text/plain",
        raise_on: str | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.truncated = truncated or {}
        self.timed_out = timed_out
        self.file_mime = file_mime
        self.raise_on: str | None = raise_on
        # Tracks calls for assertion
        self.calls: list[dict] = []

    async def run_shell(self, command: str, target: str, **kwargs: Any) -> dict:
        self.calls.append({"command": command, "target": target})
        if self.raise_on and self.raise_on in command:
            raise RuntimeError(f"Backend error on: {command}")

        # Simulate `file -b --mime-type` response
        if command.startswith("file -b --mime-type"):
            return {
                "exit_code": 0,
                "stdout": self.file_mime,
                "stderr": "",
                "duration_ms": 1,
            }

        result: dict[str, Any] = {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": 10,
        }
        if self.truncated:
            result["truncated"] = self.truncated
        if self.timed_out:
            result["timed_out"] = True
        return result


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


@pytest.fixture
async def inv_and_doc(doc_store):
    """Create a fresh investigation + document and return both IDs."""
    inv_id = "inv-m5-test"
    doc_id = await doc_store.create_document(inv_id)
    return inv_id, doc_id


def make_command_tool(doc_store, artifact_store, backend):
    return IngestCommandOutputTool(doc_store, artifact_store, backend)


def make_file_tool(doc_store, artifact_store, backend):
    return IngestRemoteFileTool(doc_store, artifact_store, backend)


# ---------------------------------------------------------------------------
# 1. IngestCommandOutputTool: blocks created
# ---------------------------------------------------------------------------

class TestCommandIngestBlocks:
    async def test_creates_command_and_output_blocks(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="line1\nline2\nline3\n")
        tool = make_command_tool(doc_store, artifact_store, backend)

        result = await tool.execute(
            investigation_id=inv_id,
            document_id=doc_id,
            command="journalctl -n 100",
            target="localhost",
            label="journal",
        )

        assert result.success, result.content
        assert result.data["command_id"]
        assert result.data["output_id"]
        assert result.data["byte_length"] > 0

        # Verify blocks in store
        state = await doc_store.get_state(inv_id, doc_id)
        blocks = state["blocks"]
        cmd_block = blocks[result.data["command_id"]]
        out_block = blocks[result.data["output_id"]]

        assert cmd_block["type"] == "command"
        assert cmd_block["tool"] == "command_ingest"
        assert cmd_block["executor"] == "agent"
        assert out_block["type"] == "output"
        assert out_block["source_command_id"] == result.data["command_id"]

    async def test_command_block_has_run_context(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="data\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id, command="df -h", target="localhost"
        )
        assert result.success
        state = await doc_store.get_state(inv_id, doc_id)
        cmd = state["blocks"][result.data["command_id"]]
        assert cmd["run_context"]["workspace"] == "localhost"
        assert cmd["run_context"]["policy_scope"] == "local_read"

    async def test_remote_target_sets_remote_read_scope(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="remote data\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            command="uptime", target="prod-01"
        )
        assert result.success
        state = await doc_store.get_state(inv_id, doc_id)
        cmd = state["blocks"][result.data["command_id"]]
        assert cmd["run_context"]["policy_scope"] == "remote_read"

    async def test_actor_is_agent_ise(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="x\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="echo x")
        assert result.success
        state = await doc_store.get_state(inv_id, doc_id)
        out = state["blocks"][result.data["output_id"]]
        assert out["created_by"] == "agent:ise"


# ---------------------------------------------------------------------------
# 2. Artifact dedup
# ---------------------------------------------------------------------------

class TestArtifactDedup:
    async def test_same_bytes_same_artifact_ref(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="identical output\n")
        tool = make_command_tool(doc_store, artifact_store, backend)

        r1 = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="echo x")
        r2 = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="echo x")
        assert r1.success and r2.success
        assert r1.data["artifact_ref"] == r2.data["artifact_ref"]
        # Different output blocks even for same artifact
        assert r1.data["output_id"] != r2.data["output_id"]

    async def test_different_bytes_different_ref(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        b1 = FakeBackend(stdout="aaa\n")
        b2 = FakeBackend(stdout="bbb\n")
        t1 = make_command_tool(doc_store, artifact_store, b1)
        t2 = make_command_tool(doc_store, artifact_store, b2)
        r1 = await t1.execute(investigation_id=inv_id, document_id=doc_id, command="echo a")
        r2 = await t2.execute(investigation_id=inv_id, document_id=doc_id, command="echo b")
        assert r1.data["artifact_ref"] != r2.data["artifact_ref"]

    async def test_checksum_matches_sha256(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        content = "checksum test line\n"
        backend = FakeBackend(stdout=content)
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="cmd")
        assert result.success
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert result.data["artifact_ref"] == expected


# ---------------------------------------------------------------------------
# 3. Indexing
# ---------------------------------------------------------------------------

class TestIndexing:
    async def test_text_output_is_indexed(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="first\nsecond\nthird\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="cmd")
        assert result.success
        assert result.data["indexed"] is True
        assert result.data["index_ref"] is not None
        assert result.data["line_count"] == 3

    async def test_indexed_output_supports_resolve_span(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        content = "alpha\nbeta\ngamma\n"
        backend = FakeBackend(stdout=content)
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="cmd")
        assert result.success

        raw = content.encode()
        lm, _ = index_bytes(raw, content_encoding="utf-8", newline_mode="lf")
        # "beta\n" spans bytes 6–11
        span = resolve_span(6, 11, lm, len(raw))
        assert span is not None
        line_start, line_end, _ = span
        assert line_start == 1
        assert line_end == 1


# ---------------------------------------------------------------------------
# 4 & 5. Truncation
# ---------------------------------------------------------------------------

class TestTruncation:
    async def test_output_clamped_to_max_bytes(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        content = "x" * 200
        backend = FakeBackend(stdout=content)
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            command="bigcmd", max_bytes=50
        )
        assert result.success
        assert result.data["byte_length"] == 50
        assert result.data["truncated"] is True

    async def test_backend_truncation_flag_propagates(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="some output\n", truncated={"stdout": True})
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="cmd")
        assert result.success
        assert result.data["truncated"] is True

        state = await doc_store.get_state(inv_id, doc_id)
        out = state["blocks"][result.data["output_id"]]
        assert out["truncated"] is True

    async def test_no_truncation_when_within_limit(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        content = "short\n"
        backend = FakeBackend(stdout=content)
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="cmd")
        assert result.success
        assert result.data["truncated"] is False


# ---------------------------------------------------------------------------
# 6. IngestRemoteFileTool — localhost reads directly
# ---------------------------------------------------------------------------

class TestLocalFileIngest:
    async def test_reads_local_text_file(self, doc_store, artifact_store, inv_and_doc, tmp_path):
        inv_id, doc_id = inv_and_doc
        f = tmp_path / "evidence.log"
        f.write_text("log line 1\nlog line 2\nlog line 3\n", encoding="utf-8")

        backend = FakeBackend()
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path=str(f), target="localhost", label="test log"
        )

        assert result.success, result.content
        assert result.data["byte_length"] == f.stat().st_size
        assert result.data["indexed"] is True
        assert result.data["line_count"] == 3

    async def test_local_file_not_found(self, doc_store, artifact_store, inv_and_doc, tmp_path):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend()
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path=str(tmp_path / "nonexistent.log"), target="localhost"
        )
        assert not result.success
        assert "not found" in result.content.lower()

    async def test_local_no_backend_call(self, doc_store, artifact_store, inv_and_doc, tmp_path):
        """For localhost, the tool should not call run_shell to read the file."""
        inv_id, doc_id = inv_and_doc
        f = tmp_path / "data.txt"
        f.write_text("hello\n")
        backend = FakeBackend()
        tool = make_file_tool(doc_store, artifact_store, backend)
        await tool.execute(investigation_id=inv_id, document_id=doc_id, path=str(f), target="localhost")
        # No cat call for localhost
        cat_calls = [c for c in backend.calls if c["command"].startswith("cat ")]
        assert len(cat_calls) == 0

    async def test_local_max_bytes_truncates(self, doc_store, artifact_store, inv_and_doc, tmp_path):
        inv_id, doc_id = inv_and_doc
        f = tmp_path / "big.txt"
        f.write_bytes(b"a" * 500)
        backend = FakeBackend()
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path=str(f), target="localhost", max_bytes=100
        )
        assert result.success
        assert result.data["byte_length"] == 100
        assert result.data["truncated"] is True


# ---------------------------------------------------------------------------
# 7. IngestRemoteFileTool — SSH fetches via cat
# ---------------------------------------------------------------------------

class TestSSHFileIngest:
    async def test_ssh_cat_text_file(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="remote line 1\nremote line 2\n", file_mime="text/plain")
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path="/var/log/app.log", target="prod-01"
        )
        assert result.success, result.content
        assert result.data["line_count"] == 2
        # Ensure cat was called
        cat_calls = [c for c in backend.calls if c["command"].startswith("cat ")]
        assert len(cat_calls) == 1

    async def test_ssh_block_has_remote_tool_name(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="data\n", file_mime="text/plain")
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path="/etc/hosts", target="host-a"
        )
        assert result.success
        state = await doc_store.get_state(inv_id, doc_id)
        cmd = state["blocks"][result.data["command_id"]]
        assert cmd["tool"] == "remote_file_ingest"


# ---------------------------------------------------------------------------
# 8. Binary rejection on SSH
# ---------------------------------------------------------------------------

class TestBinaryRejection:
    async def test_binary_mime_rejected_on_ssh(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(file_mime="application/octet-stream")
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path="/bin/ls", target="prod-01"
        )
        assert not result.success
        assert "binary" in result.content.lower()

    async def test_text_plain_accepted_on_ssh(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="log data\n", file_mime="text/plain")
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path="/var/log/syslog", target="prod-01"
        )
        assert result.success

    async def test_application_json_accepted_on_ssh(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout='{"key": "value"}\n', file_mime="application/json")
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id,
            path="/etc/config.json", target="prod-01"
        )
        assert result.success


# ---------------------------------------------------------------------------
# 9. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    async def test_backend_error_surfaced(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(raise_on="crash_cmd")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="crash_cmd")
        assert not result.success
        assert result.error_code == "backend_error"

    async def test_empty_output_rejected(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="silent_cmd")
        assert not result.success
        assert "no output" in result.content.lower()

    async def test_missing_required_params_command(self, doc_store, artifact_store):
        backend = FakeBackend(stdout="x\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id="", document_id="", command="")
        assert not result.success

    async def test_missing_required_params_file(self, doc_store, artifact_store):
        backend = FakeBackend()
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(investigation_id="", document_id="", path="", target="")
        assert not result.success

    async def test_document_not_found_command(self, doc_store, artifact_store):
        backend = FakeBackend(stdout="x\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id="nonexistent", document_id="nonexistent", command="echo x"
        )
        assert not result.success
        assert "not found" in result.content.lower()

    async def test_document_not_found_file(self, doc_store, artifact_store, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hi\n")
        backend = FakeBackend()
        tool = make_file_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id="nonexistent", document_id="nonexistent",
            path=str(f), target="localhost"
        )
        assert not result.success
        assert "not found" in result.content.lower()


# ---------------------------------------------------------------------------
# 10. Streams
# ---------------------------------------------------------------------------

class TestStreams:
    async def test_stderr_stream(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="", stderr="error line\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id, command="cmd", stream="stderr"
        )
        assert result.success
        assert result.data["byte_length"] == len("error line\n")

    async def test_combined_stream(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="out\n", stderr="err\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        result = await tool.execute(
            investigation_id=inv_id, document_id=doc_id, command="cmd", stream="combined"
        )
        assert result.success
        assert result.data["byte_length"] == len("out\nerr\n")


# ---------------------------------------------------------------------------
# 11. Revision increments
# ---------------------------------------------------------------------------

class TestRevision:
    async def test_revision_increments_across_ingests(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        backend = FakeBackend(stdout="first\n")
        tool = make_command_tool(doc_store, artifact_store, backend)
        r1 = await tool.execute(investigation_id=inv_id, document_id=doc_id, command="cmd1")
        backend2 = FakeBackend(stdout="second\n")
        t2 = make_command_tool(doc_store, artifact_store, backend2)
        r2 = await t2.execute(investigation_id=inv_id, document_id=doc_id, command="cmd2")
        assert r1.success and r2.success
        assert r2.data["revision"] > r1.data["revision"]


# ---------------------------------------------------------------------------
# 12. process_bytes_ingest API
# ---------------------------------------------------------------------------

class TestProcessBytesIngest:
    async def test_tool_name_lands_in_command_block(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        raw = b"some output\n"
        result = await process_bytes_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=inv_id,
            document_id=doc_id,
            actor_id="test-actor",
            actor_type="agent",
            actor_source="test",
            raw=raw,
            filename="test.log",
            content_type="text/plain",
            tool="command_ingest",
            command_str="custom cmd",
        )
        state = await doc_store.get_state(inv_id, doc_id)
        cmd = state["blocks"][result["command_id"]]
        assert cmd["tool"] == "command_ingest"
        assert cmd["input"]["command"] == "custom cmd"

    async def test_run_context_override(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        result = await process_bytes_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=inv_id,
            document_id=doc_id,
            actor_id="a",
            actor_type="agent",
            actor_source="tool",
            raw=b"data\n",
            filename="x.txt",
            content_type="text/plain",
            run_context={"workspace": "remote-01", "policy_scope": "remote_read", "identity": "a", "label": ""},
        )
        state = await doc_store.get_state(inv_id, doc_id)
        cmd = state["blocks"][result["command_id"]]
        assert cmd["run_context"]["workspace"] == "remote-01"
        assert cmd["run_context"]["policy_scope"] == "remote_read"

    async def test_truncated_flag_recorded(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        result = await process_bytes_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=inv_id,
            document_id=doc_id,
            actor_id="a",
            actor_type="agent",
            actor_source="tool",
            raw=b"truncated\n",
            filename="x.txt",
            content_type="text/plain",
            truncated=True,
        )
        state = await doc_store.get_state(inv_id, doc_id)
        out = state["blocks"][result["output_id"]]
        assert out["truncated"] is True

    async def test_non_indexable_content_type(self, doc_store, artifact_store, inv_and_doc):
        inv_id, doc_id = inv_and_doc
        result = await process_bytes_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=inv_id,
            document_id=doc_id,
            actor_id="a",
            actor_type="agent",
            actor_source="tool",
            raw=b"\x89PNG\r\n",
            filename="image.png",
            content_type="image/png",
        )
        assert result["indexed"] is False
        assert result["index_ref"] is None


# ---------------------------------------------------------------------------
# 13. Backward compat imports
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_process_file_ingest_importable_from_routes(self):
        from workbench.web.routes.documents import _process_file_ingest
        assert callable(_process_file_ingest)

    def test_is_indexable_importable_from_routes(self):
        from workbench.web.routes.documents import _is_indexable
        assert callable(_is_indexable)

    def test_constants_importable_from_routes(self):
        from workbench.web.routes.documents import MAX_UPLOAD_SIZE, INDEX_SIZE_THRESHOLD
        assert MAX_UPLOAD_SIZE == 100 * 1024 * 1024
        assert INDEX_SIZE_THRESHOLD == 20 * 1024 * 1024

    def test_max_command_bytes_exported(self):
        assert MAX_COMMAND_BYTES == 10 * 1024 * 1024

    def test_is_indexable_from_ingest_matches_routes(self):
        from workbench.web.routes.documents import _is_indexable
        assert _is_indexable("test.log", "text/plain", 100) is True
        assert _is_indexable("test.png", "image/png", 100) is False
        assert is_indexable("test.log", "text/plain", 100) is True
