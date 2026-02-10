"""
Assembles streaming tool-call deltas into complete ToolCall objects.

Design goals:
  - Accumulate ``RawToolDelta`` fragments keyed by ``call_index``.
  - On ``done=True`` (or an explicit ``flush()``), attempt to JSON-parse the
    accumulated argument string.
  - If parsing fails the call is *dropped* and an error is recorded -- the
    caller can inspect ``self.errors`` and surface the failure to the user or
    log it.
"""

from __future__ import annotations

import json

from workbench.llm.types import RawToolDelta, ToolCall


class ToolCallAssembler:
    """Buffers raw tool-call deltas and emits finished ``ToolCall`` objects."""

    def __init__(self) -> None:
        self._buf: dict[int, dict] = {}
        self.errors: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, delta: RawToolDelta) -> list[ToolCall]:
        """
        Feed a single ``RawToolDelta`` into the assembler.

        Returns a (possibly empty) list of completed ``ToolCall`` objects.
        A call is finalized when its delta has ``done=True``.
        """
        buf = self._buf.setdefault(
            delta.call_index, {"id": None, "name": "", "args": ""}
        )

        if delta.id and not buf["id"]:
            buf["id"] = delta.id

        if delta.name_delta:
            buf["name"] += delta.name_delta

        if delta.args_delta:
            buf["args"] += delta.args_delta

        if delta.done:
            return self._finalize(delta.call_index)

        return []

    def flush(self) -> list[ToolCall]:
        """
        Finalize *all* remaining buffers, regardless of whether a ``done``
        delta was received.  Useful at stream end.

        Returns any successfully assembled ``ToolCall`` objects.
        """
        calls: list[ToolCall] = []
        for idx in sorted(self._buf.keys()):
            calls.extend(self._finalize(idx, allow_incomplete=True))
        return calls

    def reset(self) -> None:
        """Discard all accumulated state."""
        self._buf.clear()
        self.errors.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _finalize(
        self, idx: int, *, allow_incomplete: bool = False
    ) -> list[ToolCall]:
        buf = self._buf.get(idx)
        if buf is None:
            return []

        raw_args = buf["args"] or "{}"
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, ValueError) as exc:
            self.errors.append(
                f"tool_call_json_parse_failed idx={idx} err={exc}"
            )
            # When called from flush (allow_incomplete) we still remove the
            # buffer because we already tried our best.  When called from
            # feed(done=True) we also remove it -- the data is lost.
            if not allow_incomplete:
                del self._buf[idx]
            else:
                # Even in allow_incomplete mode, remove so flush doesn't
                # retry infinitely.
                del self._buf[idx]
            return []

        name = buf["name"].strip()
        call_id = buf["id"] or f"call_{idx}"

        del self._buf[idx]
        return [ToolCall(id=call_id, name=name, arguments=args)]
