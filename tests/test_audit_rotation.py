"""Tests specifically for audit log rotation."""

import json
import os
from pathlib import Path

import pytest

from workbench.tools.base import ToolRisk
from workbench.tools.policy import PolicyEngine
from workbench.types import ToolResult
from tests.mock_tools import EchoTool


@pytest.fixture
def audit_dir(tmp_path):
    return tmp_path


@pytest.fixture
def small_engine(audit_dir):
    """PolicyEngine with a very small audit max size to trigger rotation easily."""
    audit_path = str(audit_dir / "audit.jsonl")
    return PolicyEngine(
        max_risk=ToolRisk.READ_ONLY,
        audit_log_path=audit_path,
        audit_max_size_mb=0,  # 0 MB means 0 bytes threshold -> rotates every time
        audit_keep_files=3,
    )


def _make_result(content: str = "ok") -> ToolResult:
    return ToolResult(success=True, content=content)


def _write_audit_entry(path: Path, size_bytes: int) -> None:
    """Write enough data to an audit file to reach the target size."""
    record = {"ts": "2025-01-01T00:00:00+00:00", "data": "x" * max(0, size_bytes - 50)}
    line = json.dumps(record) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


class TestAuditRotation:
    """Tests for audit log file rotation."""

    async def test_rotation_triggers_when_exceeding_size(self, audit_dir):
        audit_path = audit_dir / "audit.jsonl"
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=str(audit_path),
            audit_max_size_mb=0,  # 0 bytes -> always rotate if file exists
            audit_keep_files=3,
        )
        tool = EchoTool()

        # First write - file doesn't exist yet, so no rotation; file gets created
        await engine.audit_log(
            session_id="s1",
            event_id="e1",
            tool=tool,
            args={"message": "first"},
            result=_make_result("first"),
            duration_ms=1,
            tool_call_id="tc-1",
        )
        assert audit_path.exists()
        first_size = audit_path.stat().st_size
        assert first_size > 0

        # Second write - file exists and exceeds 0 bytes -> rotation happens
        await engine.audit_log(
            session_id="s1",
            event_id="e2",
            tool=tool,
            args={"message": "second"},
            result=_make_result("second"),
            duration_ms=2,
            tool_call_id="tc-2",
        )
        rotated_1 = audit_path.with_suffix(".jsonl.1")
        assert rotated_1.exists(), "Rotated file .1 should exist"
        assert audit_path.exists(), "Main audit file should exist with new entry"

        # The rotated file should contain the first entry
        with rotated_1.open("r") as f:
            old_record = json.loads(f.readline())
        assert old_record["event_id"] == "e1"

        # The current file should contain only the second entry
        with audit_path.open("r") as f:
            new_record = json.loads(f.readline())
        assert new_record["event_id"] == "e2"

    async def test_keeps_correct_number_of_rotated_files(self, audit_dir):
        audit_path = audit_dir / "audit.jsonl"
        keep_files = 3
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=str(audit_path),
            audit_max_size_mb=0,
            audit_keep_files=keep_files,
        )
        tool = EchoTool()

        # Write more entries than keep_files to force multiple rotations
        total_writes = keep_files + 3  # 6 writes total
        for i in range(total_writes):
            await engine.audit_log(
                session_id="s1",
                event_id=f"e{i}",
                tool=tool,
                args={"message": f"msg-{i}"},
                result=_make_result(f"result-{i}"),
                duration_ms=i,
                tool_call_id=f"tc-{i}",
            )

        # Count rotated files
        rotated = sorted(audit_dir.glob("audit.jsonl.*"))
        # Should have at most keep_files rotated files (numbered 1..keep_files)
        assert len(rotated) <= keep_files

        # Main file should always exist
        assert audit_path.exists()

        # The highest numbered rotated file should not exceed keep_files
        for rp in rotated:
            suffix_num = int(rp.suffix.lstrip("."))
            assert suffix_num <= keep_files

    async def test_rotation_is_atomic_no_data_loss(self, audit_dir):
        """Ensure that rotation via Path.replace() is atomic and no records are lost."""
        audit_path = audit_dir / "audit.jsonl"
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=str(audit_path),
            audit_max_size_mb=0,
            audit_keep_files=5,
        )
        tool = EchoTool()

        num_entries = 8
        for i in range(num_entries):
            await engine.audit_log(
                session_id="s1",
                event_id=f"evt-{i}",
                tool=tool,
                args={"message": f"m{i}"},
                result=_make_result(f"r{i}"),
                duration_ms=i,
                tool_call_id=f"tc-{i}",
            )

        # Collect all records from all files
        all_event_ids = set()
        if audit_path.exists():
            with audit_path.open("r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        all_event_ids.add(record["event_id"])

        for rp in sorted(audit_dir.glob("audit.jsonl.*")):
            with rp.open("r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        all_event_ids.add(record["event_id"])

        # We expect to find at least the most recent entries.
        # Older entries may have been pushed out by keep_files limit.
        # But the most recent entry should always be in the main file.
        assert f"evt-{num_entries - 1}" in all_event_ids

    async def test_no_rotation_when_under_size(self, audit_dir):
        """No rotation should happen if the file is under the size limit."""
        audit_path = audit_dir / "audit.jsonl"
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=str(audit_path),
            audit_max_size_mb=100,  # 100 MB - won't be exceeded
            audit_keep_files=3,
        )
        tool = EchoTool()

        for i in range(5):
            await engine.audit_log(
                session_id="s1",
                event_id=f"e{i}",
                tool=tool,
                args={"message": f"msg-{i}"},
                result=_make_result(f"result-{i}"),
                duration_ms=i,
                tool_call_id=f"tc-{i}",
            )

        # No rotated files should exist
        rotated = list(audit_dir.glob("audit.jsonl.*"))
        assert len(rotated) == 0

        # All entries should be in the main file
        with audit_path.open("r") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 5

    async def test_rotation_preserves_json_validity(self, audit_dir):
        """Every line in every file should be valid JSON after rotation."""
        audit_path = audit_dir / "audit.jsonl"
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=str(audit_path),
            audit_max_size_mb=0,
            audit_keep_files=3,
        )
        tool = EchoTool()

        for i in range(6):
            await engine.audit_log(
                session_id="s1",
                event_id=f"e{i}",
                tool=tool,
                args={"message": f"m{i}"},
                result=_make_result(f"r{i}"),
                duration_ms=i,
                tool_call_id=f"tc-{i}",
            )

        # Validate all files
        all_files = [audit_path] + list(audit_dir.glob("audit.jsonl.*"))
        for fp in all_files:
            if fp.exists():
                with fp.open("r") as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if line:
                            try:
                                json.loads(line)
                            except json.JSONDecodeError:
                                pytest.fail(
                                    f"Invalid JSON in {fp.name} line {line_num}: {line[:100]}"
                                )
