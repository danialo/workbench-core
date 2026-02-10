"""
Tests for the content-addressed artifact store.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from workbench.session.artifacts import ArtifactStore
from workbench.types import ArtifactPayload, ArtifactRef


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def art_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(str(tmp_path / "artifacts"))


# ===================================================================
# Store and retrieve
# ===================================================================


class TestStoreAndRetrieve:
    def test_store_returns_ref(self, art_store: ArtifactStore):
        payload = ArtifactPayload(
            content=b"hello world",
            original_name="greeting.txt",
            media_type="text/plain",
            description="A greeting",
        )
        ref = art_store.store(payload)
        assert ref.sha256 == hashlib.sha256(b"hello world").hexdigest()
        assert ref.original_name == "greeting.txt"
        assert ref.media_type == "text/plain"
        assert ref.description == "A greeting"
        assert ref.size_bytes == len(b"hello world")

    def test_get_returns_original_bytes(self, art_store: ArtifactStore):
        content = b"binary content \x00\x01\x02\xff"
        payload = ArtifactPayload(content=content, original_name="data.bin")
        ref = art_store.store(payload)
        retrieved = art_store.get(ref)
        assert retrieved == content

    def test_store_empty_content(self, art_store: ArtifactStore):
        payload = ArtifactPayload(content=b"", original_name="empty.txt")
        ref = art_store.store(payload)
        assert ref.size_bytes == 0
        assert art_store.get(ref) == b""

    def test_store_large_content(self, art_store: ArtifactStore):
        content = os.urandom(1024 * 1024)  # 1 MiB
        payload = ArtifactPayload(content=content, original_name="big.bin")
        ref = art_store.store(payload)
        assert art_store.get(ref) == content


# ===================================================================
# Content-addressed deduplication
# ===================================================================


class TestDeduplication:
    def test_same_content_same_sha(self, art_store: ArtifactStore):
        content = b"deduplicate me"
        ref1 = art_store.store(ArtifactPayload(content=content, original_name="a.txt"))
        ref2 = art_store.store(ArtifactPayload(content=content, original_name="b.txt"))
        assert ref1.sha256 == ref2.sha256
        assert ref1.stored_path == ref2.stored_path

    def test_different_content_different_sha(self, art_store: ArtifactStore):
        ref1 = art_store.store(ArtifactPayload(content=b"alpha"))
        ref2 = art_store.store(ArtifactPayload(content=b"beta"))
        assert ref1.sha256 != ref2.sha256

    def test_same_content_different_names(self, art_store: ArtifactStore):
        content = b"shared"
        ref1 = art_store.store(
            ArtifactPayload(content=content, original_name="one.txt")
        )
        ref2 = art_store.store(
            ArtifactPayload(content=content, original_name="two.txt")
        )
        # Different original_name in the ref but same underlying file.
        assert ref1.original_name == "one.txt"
        assert ref2.original_name == "two.txt"
        assert ref1.stored_path == ref2.stored_path

    def test_dedup_no_extra_file(self, art_store: ArtifactStore):
        content = b"only one file"
        art_store.store(ArtifactPayload(content=content))
        art_store.store(ArtifactPayload(content=content))
        # Count files under the base directory (excluding subdirs themselves).
        file_count = sum(
            1
            for p in art_store.base_dir.rglob("*")
            if p.is_file()
        )
        assert file_count == 1


# ===================================================================
# Permissions
# ===================================================================


class TestPermissions:
    def test_base_dir_mode(self, art_store: ArtifactStore):
        mode = stat.S_IMODE(art_store.base_dir.stat().st_mode)
        assert mode == 0o700

    def test_subdir_mode(self, art_store: ArtifactStore):
        art_store.store(ArtifactPayload(content=b"test"))
        # There should be at least one subdirectory.
        subdirs = [p for p in art_store.base_dir.iterdir() if p.is_dir()]
        assert len(subdirs) > 0
        for sd in subdirs:
            mode = stat.S_IMODE(sd.stat().st_mode)
            assert mode == 0o700

    def test_file_mode(self, art_store: ArtifactStore):
        ref = art_store.store(ArtifactPayload(content=b"secret"))
        path = Path(ref.stored_path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600


# ===================================================================
# Path traversal protection
# ===================================================================


class TestPathTraversal:
    def test_original_name_with_dotdot(self, art_store: ArtifactStore):
        """original_name containing ``../`` must not affect storage path."""
        payload = ArtifactPayload(
            content=b"traversal attempt",
            original_name="../../../etc/passwd",
        )
        ref = art_store.store(payload)
        # The stored_path must be under base_dir.
        assert str(art_store.base_dir) in ref.stored_path
        # The original_name is preserved in the ref for display only.
        assert ref.original_name == "../../../etc/passwd"

    def test_original_name_absolute_path(self, art_store: ArtifactStore):
        """original_name with absolute path does not escape base_dir."""
        payload = ArtifactPayload(
            content=b"abs path attempt",
            original_name="/etc/shadow",
        )
        ref = art_store.store(payload)
        assert str(art_store.base_dir) in ref.stored_path

    def test_get_with_forged_path_raises(self, art_store: ArtifactStore, tmp_path: Path):
        """Getting an artifact with a forged stored_path outside base_dir raises."""
        # Create a file outside the artifact store.
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"should not be accessible")

        forged_ref = ArtifactRef(
            sha256="fake",
            stored_path=str(outside),
            original_name="innocent.txt",
        )
        with pytest.raises(ValueError, match="Path traversal"):
            art_store.get(forged_ref)

    def test_exists_traversal_safe(self, art_store: ArtifactStore):
        """exists() with a crafted hash doesn't traverse."""
        # A hash like "../../x" would be caught by validation.
        with pytest.raises(ValueError, match="Path traversal"):
            art_store.exists("../../etc/passwd")


# ===================================================================
# Missing artifact
# ===================================================================


class TestMissingArtifact:
    def test_get_missing_raises_file_not_found(self, art_store: ArtifactStore):
        ref = ArtifactRef(
            sha256="deadbeef" * 8,
            stored_path=str(art_store.base_dir / "de" / ("deadbeef" * 8)),
        )
        with pytest.raises(FileNotFoundError, match="Artifact not found"):
            art_store.get(ref)

    def test_exists_returns_false_for_missing(self, art_store: ArtifactStore):
        assert art_store.exists("deadbeef" * 8) is False


# ===================================================================
# Delete
# ===================================================================


class TestDelete:
    def test_delete_existing(self, art_store: ArtifactStore):
        ref = art_store.store(ArtifactPayload(content=b"delete me"))
        assert art_store.exists(ref.sha256)
        result = art_store.delete(ref.sha256)
        assert result is True
        assert art_store.exists(ref.sha256) is False

    def test_delete_nonexistent(self, art_store: ArtifactStore):
        result = art_store.delete("deadbeef" * 8)
        assert result is False
