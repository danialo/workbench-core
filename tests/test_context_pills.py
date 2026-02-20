"""Tests for context pill CRUD and context builder."""

import json
import pytest
from pathlib import Path

from workbench.web.routes.context import (
    ensure_context_pills_table,
    build_context_pills_prefix,
    _format_date,
)


@pytest.fixture
async def db_path(tmp_path):
    """Create a temporary database with the context_pills table."""
    db = str(tmp_path / "test.db")
    await ensure_context_pills_table(db)
    return db


async def _insert_pill(db_path, pill_id, workspace_id, pill_type, label, fields, enabled=1):
    """Helper to insert a pill directly."""
    import aiosqlite
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO context_pills
               (pill_id, workspace_id, pill_type, label, enabled, fields, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (pill_id, workspace_id, pill_type, label, enabled, json.dumps(fields), now, now),
        )
        await db.commit()


class TestEnsureTable:
    """Test table creation."""

    async def test_creates_table(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        await ensure_context_pills_table(db)

        import aiosqlite
        async with aiosqlite.connect(db) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='context_pills'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_idempotent(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        await ensure_context_pills_table(db)
        await ensure_context_pills_table(db)  # should not raise


class TestBuildContextPillsPrefix:
    """Test context builder function."""

    async def test_empty_workspace(self, db_path):
        result = await build_context_pills_prefix(db_path, "ws-empty")
        assert result == ""

    async def test_custom_pill(self, db_path):
        await _insert_pill(
            db_path, "p1", "ws1", "custom", "Environment",
            {"value": {"value": "production", "enabled": True}},
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert "## Workspace Context" in result
        assert "Environment: production" in result

    async def test_custom_pill_disabled_field(self, db_path):
        await _insert_pill(
            db_path, "p2", "ws1", "custom", "Region",
            {"value": {"value": "us-east-1", "enabled": False}},
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert result == ""

    async def test_disabled_pill(self, db_path):
        await _insert_pill(
            db_path, "p3", "ws1", "custom", "Team",
            {"value": {"value": "backend", "enabled": True}},
            enabled=0,
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert result == ""

    async def test_timeline_pill(self, db_path):
        await _insert_pill(
            db_path, "p4", "ws1", "timeline", "Incident Window",
            {
                "start_date": {"value": "2026-02-15T00:00:00+00:00", "enabled": True},
                "end_date": {"value": "2026-02-20T23:59:59+00:00", "enabled": True},
            },
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert "Incident Window:" in result
        assert "from 2026-02-15" in result
        assert "to 2026-02-20" in result

    async def test_timeline_partial_fields(self, db_path):
        await _insert_pill(
            db_path, "p5", "ws1", "timeline", "Window",
            {
                "start_date": {"value": "2026-02-15T00:00:00+00:00", "enabled": True},
                "end_date": {"value": "2026-02-20T23:59:59+00:00", "enabled": False},
            },
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert "from 2026-02-15" in result
        assert "to" not in result

    async def test_multiple_pills(self, db_path):
        await _insert_pill(
            db_path, "p6", "ws1", "custom", "Env",
            {"value": {"value": "staging", "enabled": True}},
        )
        await _insert_pill(
            db_path, "p7", "ws1", "custom", "Team",
            {"value": {"value": "platform", "enabled": True}},
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert "Env: staging" in result
        assert "Team: platform" in result

    async def test_workspace_isolation(self, db_path):
        await _insert_pill(
            db_path, "p8", "ws-a", "custom", "A-data",
            {"value": {"value": "alpha", "enabled": True}},
        )
        await _insert_pill(
            db_path, "p9", "ws-b", "custom", "B-data",
            {"value": {"value": "beta", "enabled": True}},
        )
        result_a = await build_context_pills_prefix(db_path, "ws-a")
        result_b = await build_context_pills_prefix(db_path, "ws-b")
        assert "alpha" in result_a
        assert "beta" not in result_a
        assert "beta" in result_b
        assert "alpha" not in result_b

    async def test_empty_value_excluded(self, db_path):
        await _insert_pill(
            db_path, "p10", "ws1", "custom", "Empty",
            {"value": {"value": "", "enabled": True}},
        )
        result = await build_context_pills_prefix(db_path, "ws1")
        assert result == ""


class TestFormatDate:
    """Test date formatting."""

    def test_default_format(self):
        assert _format_date("2026-02-15T00:00:00+00:00", "YYYY-MM-DD") == "2026-02-15"

    def test_us_format(self):
        assert _format_date("2026-02-15T00:00:00+00:00", "MM/DD/YYYY") == "02/15/2026"

    def test_eu_format(self):
        assert _format_date("2026-02-15T00:00:00+00:00", "DD/MM/YYYY") == "15/02/2026"

    def test_datetime_format(self):
        result = _format_date("2026-02-15T14:30:00+00:00", "YYYY-MM-DD HH:mm")
        assert result == "2026-02-15 14:30"

    def test_invalid_date_returns_raw(self):
        assert _format_date("not-a-date", "YYYY-MM-DD") == "not-a-date"

    def test_unknown_format_uses_default(self):
        result = _format_date("2026-02-15T00:00:00+00:00", "UNKNOWN")
        assert result == "2026-02-15"
