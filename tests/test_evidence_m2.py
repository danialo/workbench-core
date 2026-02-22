"""
M2 evidence regression tests.

Covers:
- LF and CRLF newline modes produce correct line maps
- UTF-8 multibyte: byte offsets land on correct lines
- Span boundaries: 0..len, spans cutting through newline boundaries
- Out-of-range: negative, end > length, start >= end
- Missing index: resolver triggers inline build
- Truncated output: evidence clamped to stored bytes
- validate_span contract
- get_context_lines: correct before/after/highlighted
- Evidence validation at assertion creation: mismatched artifact_ref, bad span
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from workbench.documents.indexer import (
    index_bytes,
    excerpt_bytes,
    resolve_span,
    get_context_lines,
    validate_span,
)
from workbench.documents.store import DocumentStore
from workbench.session.artifacts import ArtifactStore
from workbench.types import ArtifactPayload, ArtifactRef


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def doc_store(tmp_path: Path) -> DocumentStore:
    s = DocumentStore(str(tmp_path / "documents.db"))
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def art_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(str(tmp_path / "artifacts"))


INV_ID = "inv-m2-test"


# ---------------------------------------------------------------------------
# Newline modes
# ---------------------------------------------------------------------------

class TestNewlineModes:
    def test_lf_line_boundaries(self):
        """LF newline: line map byte ranges must not include the \\n byte."""
        raw = b"alpha\nbeta\ngamma"
        lm, _ = index_bytes(raw, newline_mode="lf")
        assert lm[0] == (0, 5)          # "alpha"
        assert lm[1] == (6, 10)         # "beta"
        assert lm[2] == (11, 16)        # "gamma"

    def test_lf_trailing_newline(self):
        raw = b"a\nb\n"
        lm, _ = index_bytes(raw, newline_mode="lf")
        # "a" and "b" — trailing newline creates no extra line
        assert len(lm) == 2
        assert lm[0] == (0, 1)
        assert lm[1] == (2, 3)

    def test_crlf_line_boundaries(self):
        raw = b"alpha\r\nbeta\r\ngamma"
        lm, _ = index_bytes(raw, newline_mode="crlf")
        assert lm[0] == (0, 5)          # "alpha" without \r\n
        assert lm[1] == (7, 11)         # "beta"
        assert lm[2] == (13, 18)        # "gamma"

    def test_crlf_trailing(self):
        raw = b"x\r\ny\r\n"
        lm, _ = index_bytes(raw, newline_mode="crlf")
        assert len(lm) == 2
        assert lm[0] == (0, 1)
        assert lm[1] == (3, 4)

    def test_mixed_lf_and_crlf(self):
        raw = b"a\r\nb\nc"
        lm, _ = index_bytes(raw, newline_mode="mixed")
        assert len(lm) == 3
        assert lm[0] == (0, 1)   # "a"
        assert lm[1] == (3, 4)   # "b"
        assert lm[2] == (5, 6)   # "c"

    def test_unknown_mode_detects_lf(self):
        raw = b"line1\nline2"
        lm, _ = index_bytes(raw, newline_mode="unknown")
        assert len(lm) == 2
        assert lm[0] == (0, 5)

    def test_unknown_mode_detects_crlf_when_no_lf(self):
        raw = b"line1\r\nline2"
        lm, _ = index_bytes(raw, newline_mode="unknown")
        assert len(lm) == 2
        assert lm[0] == (0, 5)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown newline_mode"):
            index_bytes(b"data", newline_mode="banana")


# ---------------------------------------------------------------------------
# UTF-8 multibyte
# ---------------------------------------------------------------------------

class TestUtf8Multibyte:
    def test_multibyte_chars_line_map_correct(self):
        """
        Lines with multibyte chars must map to byte boundaries that decode
        correctly, not land in the middle of a codepoint.
        """
        line0 = "héllo"    # é = 2 bytes in UTF-8
        line1 = "wörld"    # ö = 2 bytes
        raw = (line0 + "\n" + line1).encode("utf-8")

        lm, _ = index_bytes(raw)

        # Line 0: 0..len("héllo".encode())
        l0_bytes = len(line0.encode("utf-8"))
        assert lm[0] == (0, l0_bytes)

        # Line 1: starts after \n
        l1_start = l0_bytes + 1  # skip \n
        l1_bytes = len(line1.encode("utf-8"))
        assert lm[1] == (l1_start, l1_start + l1_bytes)

    def test_excerpt_multibyte_decodes_cleanly(self):
        line0 = "привет"   # Cyrillic: 12 bytes
        line1 = "мир"
        raw = (line0 + "\n" + line1).encode("utf-8")
        lm, _ = index_bytes(raw)

        bs, be = lm[0]
        exc = excerpt_bytes(raw, bs, be)
        assert exc.decode("utf-8") == line0

    def test_evidence_span_within_multibyte_line(self):
        """Byte span slicing a multibyte line must decode without error."""
        text = "日本語テスト\nEnglish line"
        raw = text.encode("utf-8")
        lm, _ = index_bytes(raw)

        bs, be = lm[0]   # entire first line
        exc = excerpt_bytes(raw, bs, be)
        assert exc.decode("utf-8") == "日本語テスト"

    def test_get_context_decodes_multibyte(self):
        lines = ["строка один", "строка два", "строка три"]
        raw = "\n".join(lines).encode("utf-8")
        lm, _ = index_bytes(raw)

        ctx = get_context_lines(lm, raw, 1, 1, encoding="utf-8", before=1, after=1)
        assert ctx["before"] == ["строка один"]
        assert ctx["highlighted"] == ["строка два"]
        assert ctx["after"] == ["строка три"]


# ---------------------------------------------------------------------------
# Span boundaries
# ---------------------------------------------------------------------------

class TestSpanBoundaries:
    def test_span_entire_artifact(self):
        raw = b"hello world"
        lm, _ = index_bytes(raw)
        result = resolve_span(0, len(raw), lm, len(raw))
        assert result is not None
        assert result[0] == 0 and result[1] == 0

    def test_span_single_byte(self):
        raw = b"abc\ndef"
        lm, _ = index_bytes(raw)
        result = resolve_span(0, 1, lm, len(raw))
        assert result is not None
        assert result[0] == 0 and result[1] == 0

    def test_span_crosses_newline_into_next_line(self):
        raw = b"line0\nline1\nline2"
        lm, _ = index_bytes(raw)
        # Span from middle of line0 through middle of line1
        result = resolve_span(2, 9, lm, len(raw))
        assert result is not None
        line_start, line_end, _ = result
        assert line_start == 0
        assert line_end == 1

    def test_span_at_last_byte(self):
        raw = b"abc"
        lm, _ = index_bytes(raw)
        result = resolve_span(2, 3, lm, len(raw))
        assert result is not None

    def test_span_on_newline_byte(self):
        """Span that starts exactly at the \\n byte — should map to next line."""
        raw = b"abc\ndef"
        lm, _ = index_bytes(raw)
        # byte 3 is \n, byte 4 starts "def"
        result = resolve_span(4, 7, lm, len(raw))
        assert result is not None
        assert result[0] == 1


# ---------------------------------------------------------------------------
# Out-of-range
# ---------------------------------------------------------------------------

class TestOutOfRange:
    def test_validate_negative_byte_start(self):
        err = validate_span(-1, 10, 100)
        assert err is not None
        assert "byte_start" in err

    def test_validate_negative_byte_end(self):
        err = validate_span(0, -1, 100)
        assert err is not None

    def test_validate_start_equal_end(self):
        err = validate_span(5, 5, 100)
        assert err is not None

    def test_validate_start_greater_than_end(self):
        err = validate_span(10, 5, 100)
        assert err is not None

    def test_validate_end_exceeds_length(self):
        err = validate_span(0, 101, 100)
        assert err is not None
        assert "byte_end" in err

    def test_validate_valid_span_returns_none(self):
        err = validate_span(0, 10, 100)
        assert err is None

    def test_validate_end_equals_length_valid(self):
        err = validate_span(0, 100, 100)
        assert err is None

    def test_excerpt_bytes_clamps_negative_start(self):
        raw = b"hello"
        exc = excerpt_bytes(raw, -5, 3)
        assert exc == b"hel"

    def test_excerpt_bytes_clamps_end_beyond_length(self):
        raw = b"hello"
        exc = excerpt_bytes(raw, 0, 999)
        assert exc == b"hello"

    def test_resolve_span_empty_map_returns_none(self):
        result = resolve_span(0, 5, {}, 100)
        assert result is None

    def test_resolve_span_start_beyond_last_line(self):
        raw = b"line"
        lm, _ = index_bytes(raw)
        # Byte range that doesn't intersect any line
        result = resolve_span(100, 200, lm, 200)
        assert result is None


# ---------------------------------------------------------------------------
# Missing index — inline build on read
# ---------------------------------------------------------------------------

class TestMissingIndex:
    async def test_missing_index_built_on_store_artifact_index_call(
        self, doc_store: DocumentStore, art_store: ArtifactStore
    ):
        """
        If an artifact exists in the ArtifactStore but has no index in doc DB,
        _ensure_artifact_index should build and persist it.
        """
        from workbench.web.routes.documents import _ensure_artifact_index, _make_artifact_ref

        raw = b"line one\nline two\nline three"
        ref_obj = art_store.store(ArtifactPayload(content=raw, original_name="out.txt"))
        sha = ref_obj.sha256

        # Nothing in doc DB yet
        idx = await doc_store.get_artifact_index(sha)
        assert idx is None

        # Simulate the helper
        result = await _ensure_artifact_index(doc_store, art_store, sha)
        assert result is not None
        assert "0" in result["line_map"]

        # Should now be persisted
        idx2 = await doc_store.get_artifact_index(sha)
        assert idx2 is not None

    async def test_missing_artifact_returns_none(
        self, doc_store: DocumentStore, art_store: ArtifactStore
    ):
        from workbench.web.routes.documents import _ensure_artifact_index
        result = await _ensure_artifact_index(doc_store, art_store, "deadbeef" * 8)
        assert result is None


# ---------------------------------------------------------------------------
# Truncated output
# ---------------------------------------------------------------------------

class TestTruncatedOutput:
    def test_truncated_flag_does_not_change_index(self):
        """
        Truncation is metadata. The indexer works on stored bytes only.
        Evidence must not reference bytes beyond stored length.
        """
        raw = b"part of a longer output\nthat was truncated here"
        lm, _ = index_bytes(raw)
        total = len(raw)

        # Valid span — within stored bytes
        assert validate_span(0, total, total) is None

        # Span beyond stored bytes — invalid even if 'truncated' flag is set
        err = validate_span(0, total + 100, total)
        assert err is not None

    def test_excerpt_of_truncated_artifact(self):
        """excerpt_bytes on truncated content returns only what is stored."""
        raw = b"stored portion\n"
        exc = excerpt_bytes(raw, 0, len(raw))
        assert exc == raw

        # Requesting beyond stored bytes clamps silently
        exc2 = excerpt_bytes(raw, 0, 9999)
        assert exc2 == raw


# ---------------------------------------------------------------------------
# validate_span contract
# ---------------------------------------------------------------------------

class TestValidateSpanContract:
    @pytest.mark.parametrize("start,end,total,ok", [
        (0, 1, 1, True),
        (0, 100, 100, True),
        (50, 99, 100, True),
        (0, 0, 100, False),       # start == end
        (-1, 5, 100, False),      # negative start
        (0, -1, 100, False),      # negative end
        (5, 4, 100, False),       # start > end
        (0, 101, 100, False),     # end > total
        (100, 101, 100, False),   # start == total
    ])
    def test_validate_span(self, start, end, total, ok):
        err = validate_span(start, end, total)
        if ok:
            assert err is None
        else:
            assert err is not None


# ---------------------------------------------------------------------------
# get_context_lines
# ---------------------------------------------------------------------------

class TestGetContextLines:
    def _make_map(self, lines: list[str]) -> tuple[dict, bytes]:
        raw = "\n".join(lines).encode("utf-8")
        lm, _ = index_bytes(raw)
        return lm, raw

    def test_middle_line_context(self):
        lm, raw = self._make_map(["A", "B", "C", "D", "E"])
        ctx = get_context_lines(lm, raw, 2, 2, before=1, after=1)
        assert ctx["before"] == ["B"]
        assert ctx["highlighted"] == ["C"]
        assert ctx["after"] == ["D"]
        assert ctx["context_line_start"] == 1
        assert ctx["context_line_end"] == 3

    def test_first_line_no_before(self):
        lm, raw = self._make_map(["X", "Y", "Z"])
        ctx = get_context_lines(lm, raw, 0, 0, before=3, after=1)
        assert ctx["before"] == []
        assert ctx["highlighted"] == ["X"]
        assert ctx["after"] == ["Y"]

    def test_last_line_no_after(self):
        lm, raw = self._make_map(["A", "B", "C"])
        ctx = get_context_lines(lm, raw, 2, 2, before=1, after=5)
        assert ctx["highlighted"] == ["C"]
        assert ctx["after"] == []
        assert ctx["context_line_end"] == 2

    def test_multi_line_highlight(self):
        lm, raw = self._make_map(["a", "b", "c", "d", "e"])
        ctx = get_context_lines(lm, raw, 1, 3, before=1, after=1)
        assert ctx["highlighted"] == ["b", "c", "d"]
        assert ctx["before"] == ["a"]
        assert ctx["after"] == ["e"]

    def test_empty_map(self):
        ctx = get_context_lines({}, b"", 0, 0)
        assert ctx["before"] == [] and ctx["highlighted"] == [] and ctx["after"] == []

    def test_zero_context(self):
        lm, raw = self._make_map(["x", "y", "z"])
        ctx = get_context_lines(lm, raw, 1, 1, before=0, after=0)
        assert ctx["before"] == []
        assert ctx["after"] == []
        assert ctx["highlighted"] == ["y"]


# ---------------------------------------------------------------------------
# Artifact index store — roundtrip with real line map
# ---------------------------------------------------------------------------

class TestArtifactIndexRoundtrip:
    async def test_store_and_resolve_lf(self, doc_store: DocumentStore):
        raw = b"first\nsecond\nthird"
        lm, rm = index_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        await doc_store.store_artifact_index(sha, lm, rm)

        idx = await doc_store.get_artifact_index(sha)
        assert idx is not None
        stored_lm = {int(k): tuple(v) for k, v in idx["line_map"].items()}
        assert stored_lm[0] == (0, 5)
        assert stored_lm[1] == (6, 12)
        assert stored_lm[2] == (13, 18)

    async def test_store_and_resolve_crlf(self, doc_store: DocumentStore):
        raw = b"first\r\nsecond\r\nthird"
        lm, rm = index_bytes(raw, newline_mode="crlf")
        sha = hashlib.sha256(raw).hexdigest()
        await doc_store.store_artifact_index(sha, lm, rm)

        idx = await doc_store.get_artifact_index(sha)
        stored_lm = {int(k): tuple(v) for k, v in idx["line_map"].items()}
        assert stored_lm[0] == (0, 5)
        assert stored_lm[1] == (7, 13)
        assert stored_lm[2] == (15, 20)
