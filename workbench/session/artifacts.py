"""
Content-addressed artifact store with hardened file permissions.

Artifacts are stored by their SHA-256 hash in a two-level directory layout
(first two hex chars as subdirectory).  Duplicate content is deduplicated
automatically -- storing the same bytes twice returns the same reference
without writing a second file.

Security measures:
- Base directory created with mode 0o700 (owner-only).
- Individual files written with mode 0o600.
- ``original_name`` is never used as a file-system path segment.
- All stored paths are validated to reside under the base directory (path
  traversal protection).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from workbench.types import ArtifactPayload, ArtifactRef


class ArtifactStore:
    """
    Store and retrieve binary artifacts by content hash.

    Parameters
    ----------
    base_dir:
        Root directory for artifact storage.  Created with ``0o700``
        permissions if it does not exist.
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base_dir, 0o700)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _artifact_path(self, sha256: str) -> Path:
        """Return the canonical file path for a given hash."""
        return self.base_dir / sha256[:2] / sha256

    def _validate_under_base(self, path: Path) -> None:
        """
        Raise ``ValueError`` if *path* escapes the base directory.

        Resolves symlinks and ``..`` segments before comparing.
        """
        resolved = path.resolve()
        base_resolved = self.base_dir.resolve()
        if not str(resolved).startswith(str(base_resolved) + os.sep) and resolved != base_resolved:
            raise ValueError(
                f"Path traversal detected: {path} resolves outside "
                f"base directory {self.base_dir}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, payload: ArtifactPayload) -> ArtifactRef:
        """
        Store artifact content and return a reference.

        If the same content has been stored before, the existing file is
        reused (content-addressed deduplication).

        The ``original_name`` from the payload is recorded in the returned
        ``ArtifactRef`` but is **never** used as a filesystem path.
        """
        sha = hashlib.sha256(payload.content).hexdigest()

        subdir = self.base_dir / sha[:2]
        self._validate_under_base(subdir)

        file_path = subdir / sha
        self._validate_under_base(file_path)

        if not file_path.exists():
            subdir.mkdir(exist_ok=True)
            os.chmod(subdir, 0o700)

            # Write atomically: write to a temp location then rename.
            tmp_path = file_path.with_suffix(".tmp")
            try:
                tmp_path.write_bytes(payload.content)
                os.chmod(tmp_path, 0o600)
                tmp_path.rename(file_path)
            except BaseException:
                # Clean up partial writes on any failure.
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

        return ArtifactRef(
            sha256=sha,
            stored_path=str(file_path),
            original_name=payload.original_name,
            media_type=payload.media_type,
            description=payload.description,
            size_bytes=len(payload.content),
        )

    def get(self, ref: ArtifactRef) -> bytes:
        """
        Retrieve the raw bytes for an artifact reference.

        Raises
        ------
        FileNotFoundError
            If the artifact file does not exist on disk.
        ValueError
            If the stored path escapes the base directory.
        """
        path = Path(ref.stored_path).resolve()
        self._validate_under_base(path)

        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {ref.sha256}")
        return path.read_bytes()

    def exists(self, sha256: str) -> bool:
        """Return ``True`` if an artifact with the given hash is stored."""
        path = self._artifact_path(sha256)
        self._validate_under_base(path)
        return path.exists()

    def delete(self, sha256: str) -> bool:
        """
        Remove a stored artifact.  Returns ``True`` if the file existed.

        The parent subdirectory is removed if it becomes empty.
        """
        path = self._artifact_path(sha256)
        self._validate_under_base(path)

        if not path.exists():
            return False

        path.unlink()

        # Clean up empty subdirectory.
        subdir = path.parent
        try:
            subdir.rmdir()  # only succeeds if empty
        except OSError:
            pass

        return True
