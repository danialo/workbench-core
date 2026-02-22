"""
Artifact indexer — builds line/byte maps for output artifacts.

Produces:
  line_map:    { line_number(0-based) -> (byte_start, byte_end) }
  reverse_map: { (byte_start, byte_end) -> (line_start, line_end) }

Honoring content_encoding (default utf-8) and newline_mode (lf/crlf/mixed/unknown).
"""

from __future__ import annotations


def index_bytes(
    raw: bytes,
    *,
    content_encoding: str = "utf-8",
    newline_mode: str = "lf",
) -> tuple[dict[int, tuple[int, int]], dict[tuple[int, int], tuple[int, int]]]:
    """
    Build line and reverse maps from raw artifact bytes.

    Parameters
    ----------
    raw:
        The raw bytes of the artifact (as returned by ArtifactStore.get).
    content_encoding:
        Expected text encoding.  Only affects line splitting; offsets are
        always byte offsets into `raw`.
    newline_mode:
        'lf'      — split on b'\\n'
        'crlf'    — split on b'\\r\\n'
        'mixed'   — split on either (scanned byte-by-byte)
        'unknown' — attempt lf first, fall back to crlf

    Returns
    -------
    line_map:
        { line_index(0-based) -> (byte_start, byte_end) }
        byte_end is exclusive (points one past the last content byte,
        not including the newline).
    reverse_map:
        { (byte_start, byte_end) -> (line_start, line_end) }
        For range lookups: find which lines a byte range intersects.
    """
    if not raw:
        return {}, {}

    if newline_mode == "unknown":
        # CRLF if every \n is part of \r\n; LF if any bare \n exists
        if b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""):
            newline_mode = "crlf"
        else:
            newline_mode = "lf"

    line_map: dict[int, tuple[int, int]] = {}
    line_index = 0
    pos = 0
    n = len(raw)

    if newline_mode == "lf":
        while pos <= n:
            try:
                nl = raw.index(b"\n", pos)
            except ValueError:
                nl = n  # last line, no trailing newline
                if pos < n:
                    line_map[line_index] = (pos, n)
                break
            line_map[line_index] = (pos, nl)
            line_index += 1
            pos = nl + 1

    elif newline_mode == "crlf":
        while pos <= n:
            try:
                nl = raw.index(b"\r\n", pos)
            except ValueError:
                nl = n
                if pos < n:
                    line_map[line_index] = (pos, n)
                break
            line_map[line_index] = (pos, nl)
            line_index += 1
            pos = nl + 2

    elif newline_mode == "mixed":
        line_start = 0
        i = 0
        while i < n:
            ch = raw[i : i + 1]
            if ch == b"\r" and i + 1 < n and raw[i + 1 : i + 2] == b"\n":
                line_map[line_index] = (line_start, i)
                line_index += 1
                i += 2
                line_start = i
            elif ch == b"\n":
                line_map[line_index] = (line_start, i)
                line_index += 1
                i += 1
                line_start = i
            else:
                i += 1
        if line_start < n:
            line_map[line_index] = (line_start, n)
    else:
        raise ValueError(f"Unknown newline_mode: {newline_mode!r}")

    # Build reverse map: (byte_start, byte_end) -> (line_start, line_end)
    # Each entry maps a byte span back to the line range it covers.
    # For v1, one entry per line (identical span, single-line granularity).
    reverse_map: dict[tuple[int, int], tuple[int, int]] = {}
    for ln, (bs, be) in line_map.items():
        span = (bs, be)
        # If multiple lines share the same byte span (degenerate case), keep first.
        if span not in reverse_map:
            reverse_map[span] = (ln, ln)

    return line_map, reverse_map


def resolve_span(
    byte_start: int,
    byte_end: int,
    line_map: dict[int, tuple[int, int]],
    total_bytes: int,
) -> tuple[int, int, bytes] | None:
    """
    Resolve a byte span to (line_start, line_end, excerpt_bytes).

    Clamps byte_start/byte_end to [0, total_bytes].
    Returns None if the span is out of range or the line_map is empty.
    """
    if not line_map or total_bytes == 0:
        return None

    byte_start = max(0, byte_start)
    byte_end = min(total_bytes, byte_end)

    if byte_start >= byte_end:
        return None

    # Find first and last line that overlap the span
    first_line: int | None = None
    last_line: int | None = None

    for ln, (bs, be) in sorted(line_map.items()):
        if be <= byte_start:
            continue
        if bs >= byte_end:
            break
        if first_line is None:
            first_line = ln
        last_line = ln

    if first_line is None or last_line is None:
        return None

    return (first_line, last_line, b"")   # caller slices from raw bytes


def excerpt_bytes(
    raw: bytes,
    byte_start: int,
    byte_end: int,
) -> bytes:
    """Return the exact byte slice for an evidence span."""
    byte_start = max(0, byte_start)
    byte_end = min(len(raw), byte_end)
    return raw[byte_start:byte_end]


def get_context_lines(
    line_map: dict[int, tuple[int, int]],
    raw: bytes,
    line_start: int,
    line_end: int,
    *,
    encoding: str = "utf-8",
    before: int = 3,
    after: int = 3,
) -> dict:
    """
    Return context lines surrounding a highlighted span.

    Parameters
    ----------
    line_map:
        Output of index_bytes().
    raw:
        Raw artifact bytes.
    line_start, line_end:
        0-based inclusive line range of the highlighted span.
    encoding:
        Encoding for decoding lines.
    before, after:
        Number of context lines to include before/after the span.

    Returns
    -------
    {
        "before":             [line text, ...],     # up to `before` lines
        "highlighted":        [line text, ...],     # line_start..line_end inclusive
        "after":              [line text, ...],     # up to `after` lines
        "context_line_start": int,                  # first line index returned
        "context_line_end":   int,                  # last line index returned
    }
    """
    if not line_map:
        return {"before": [], "highlighted": [], "after": [],
                "context_line_start": 0, "context_line_end": 0}

    n_lines = max(line_map.keys()) + 1

    def decode_line(ln: int) -> str:
        span = line_map.get(ln)
        if span is None:
            return ""
        bs, be = span
        return raw[bs:be].decode(encoding, errors="replace")

    ctx_start = max(0, line_start - before)
    ctx_end = min(n_lines - 1, line_end + after)

    return {
        "before":             [decode_line(ln) for ln in range(ctx_start, line_start)],
        "highlighted":        [decode_line(ln) for ln in range(line_start, line_end + 1)],
        "after":              [decode_line(ln) for ln in range(line_end + 1, ctx_end + 1)],
        "context_line_start": ctx_start,
        "context_line_end":   ctx_end,
    }


def validate_span(byte_start: int, byte_end: int, total_bytes: int) -> str | None:
    """
    Validate a byte span.  Returns an error message or None if valid.
    """
    if byte_start < 0:
        return f"byte_start must be >= 0, got {byte_start}"
    if byte_end < 0:
        return f"byte_end must be >= 0, got {byte_end}"
    if byte_start >= byte_end:
        return f"byte_start ({byte_start}) must be < byte_end ({byte_end})"
    if byte_end > total_bytes:
        return f"byte_end ({byte_end}) exceeds artifact length ({total_bytes})"
    return None
