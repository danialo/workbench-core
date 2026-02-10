"""Tests for workbench.llm.tool_call_assembler.ToolCallAssembler."""

from __future__ import annotations

import json

import pytest

from workbench.llm.tool_call_assembler import ToolCallAssembler
from workbench.llm.types import RawToolDelta, ToolCall


class TestSingleToolCall:
    """Assemble a single tool call from incremental deltas."""

    def test_basic_assembly(self):
        asm = ToolCallAssembler()

        # First delta: id + partial name.
        result = asm.feed(
            RawToolDelta(call_index=0, id="call_1", name_delta="read_")
        )
        assert result == []

        # Second delta: rest of name.
        result = asm.feed(
            RawToolDelta(call_index=0, name_delta="file")
        )
        assert result == []

        # Third delta: partial args.
        result = asm.feed(
            RawToolDelta(call_index=0, args_delta='{"path": ')
        )
        assert result == []

        # Fourth delta: rest of args.
        result = asm.feed(
            RawToolDelta(call_index=0, args_delta='"/etc/hosts"}')
        )
        assert result == []

        # Final delta: done.
        result = asm.feed(RawToolDelta(call_index=0, done=True))
        assert len(result) == 1

        tc = result[0]
        assert tc.id == "call_1"
        assert tc.name == "read_file"
        assert tc.arguments == {"path": "/etc/hosts"}

    def test_single_delta_with_everything(self):
        """A provider may send all data in one delta with done=True."""
        asm = ToolCallAssembler()
        result = asm.feed(
            RawToolDelta(
                call_index=0,
                id="call_x",
                name_delta="ping",
                args_delta='{"host": "localhost"}',
                done=True,
            )
        )
        assert len(result) == 1
        assert result[0].name == "ping"
        assert result[0].arguments == {"host": "localhost"}

    def test_no_errors_on_success(self):
        asm = ToolCallAssembler()
        asm.feed(
            RawToolDelta(
                call_index=0,
                id="ok",
                name_delta="test",
                args_delta="{}",
                done=True,
            )
        )
        assert asm.errors == []


class TestMultipleConcurrentToolCalls:
    """Two or more tool calls assembled in parallel (different call_index)."""

    def test_two_parallel_calls(self):
        asm = ToolCallAssembler()

        # Start call 0.
        asm.feed(RawToolDelta(call_index=0, id="c0", name_delta="alpha"))
        # Start call 1.
        asm.feed(RawToolDelta(call_index=1, id="c1", name_delta="beta"))

        # Args for both.
        asm.feed(RawToolDelta(call_index=0, args_delta='{"x": 1}'))
        asm.feed(RawToolDelta(call_index=1, args_delta='{"y": 2}'))

        # Finish call 0.
        r0 = asm.feed(RawToolDelta(call_index=0, done=True))
        assert len(r0) == 1
        assert r0[0].name == "alpha"
        assert r0[0].arguments == {"x": 1}

        # Finish call 1.
        r1 = asm.feed(RawToolDelta(call_index=1, done=True))
        assert len(r1) == 1
        assert r1[0].name == "beta"
        assert r1[0].arguments == {"y": 2}

    def test_three_interleaved_calls(self):
        asm = ToolCallAssembler()

        for idx in range(3):
            asm.feed(
                RawToolDelta(
                    call_index=idx,
                    id=f"c{idx}",
                    name_delta=f"tool_{idx}",
                )
            )

        for idx in range(3):
            asm.feed(
                RawToolDelta(
                    call_index=idx,
                    args_delta=json.dumps({"idx": idx}),
                )
            )

        all_calls: list[ToolCall] = []
        for idx in range(3):
            all_calls.extend(
                asm.feed(RawToolDelta(call_index=idx, done=True))
            )

        assert len(all_calls) == 3
        names = {tc.name for tc in all_calls}
        assert names == {"tool_0", "tool_1", "tool_2"}


class TestMalformedJSON:
    """Malformed argument strings should produce errors, not ToolCalls."""

    def test_invalid_json_on_done(self):
        asm = ToolCallAssembler()

        asm.feed(
            RawToolDelta(call_index=0, id="bad", name_delta="broken")
        )
        asm.feed(
            RawToolDelta(call_index=0, args_delta="NOT VALID JSON {{{")
        )
        result = asm.feed(RawToolDelta(call_index=0, done=True))

        assert result == []
        assert len(asm.errors) == 1
        assert "tool_call_json_parse_failed" in asm.errors[0]
        assert "idx=0" in asm.errors[0]

    def test_partial_json_on_done(self):
        asm = ToolCallAssembler()

        asm.feed(
            RawToolDelta(call_index=0, id="partial", name_delta="trunc")
        )
        asm.feed(
            RawToolDelta(call_index=0, args_delta='{"key": "val')
        )
        result = asm.feed(RawToolDelta(call_index=0, done=True))

        assert result == []
        assert len(asm.errors) == 1

    def test_malformed_does_not_block_valid_calls(self):
        """A malformed call should not prevent other calls from assembling."""
        asm = ToolCallAssembler()

        # Call 0: malformed.
        asm.feed(RawToolDelta(call_index=0, id="bad", name_delta="broken"))
        asm.feed(RawToolDelta(call_index=0, args_delta="{BAD"))
        r0 = asm.feed(RawToolDelta(call_index=0, done=True))
        assert r0 == []

        # Call 1: valid.
        asm.feed(RawToolDelta(call_index=1, id="good", name_delta="ok"))
        asm.feed(RawToolDelta(call_index=1, args_delta='{"a": 1}'))
        r1 = asm.feed(RawToolDelta(call_index=1, done=True))
        assert len(r1) == 1
        assert r1[0].name == "ok"


class TestFlush:
    """flush() should finalize remaining buffers."""

    def test_flush_completes_ready_buffer(self):
        asm = ToolCallAssembler()

        asm.feed(RawToolDelta(call_index=0, id="f0", name_delta="flush_me"))
        asm.feed(RawToolDelta(call_index=0, args_delta='{"done": true}'))

        # No done=True was sent -- flush should finalize.
        result = asm.flush()
        assert len(result) == 1
        assert result[0].name == "flush_me"
        assert result[0].arguments == {"done": True}

    def test_flush_with_bad_json(self):
        asm = ToolCallAssembler()

        asm.feed(RawToolDelta(call_index=0, id="f1", name_delta="bad"))
        asm.feed(RawToolDelta(call_index=0, args_delta="NOPE"))

        result = asm.flush()
        assert result == []
        assert len(asm.errors) == 1

    def test_flush_multiple_buffers(self):
        asm = ToolCallAssembler()

        # Two incomplete calls.
        asm.feed(RawToolDelta(call_index=0, id="a", name_delta="alpha"))
        asm.feed(RawToolDelta(call_index=0, args_delta='{"v": 1}'))

        asm.feed(RawToolDelta(call_index=1, id="b", name_delta="beta"))
        asm.feed(RawToolDelta(call_index=1, args_delta='{"v": 2}'))

        result = asm.flush()
        assert len(result) == 2
        names = {tc.name for tc in result}
        assert names == {"alpha", "beta"}

    def test_flush_on_empty_assembler(self):
        asm = ToolCallAssembler()
        assert asm.flush() == []


class TestEmptyArgs:
    """Empty or absent arguments should default to ``{}``."""

    def test_no_args_delta(self):
        asm = ToolCallAssembler()
        result = asm.feed(
            RawToolDelta(
                call_index=0,
                id="no_args",
                name_delta="simple",
                done=True,
            )
        )
        assert len(result) == 1
        assert result[0].arguments == {}

    def test_empty_string_args(self):
        asm = ToolCallAssembler()
        asm.feed(RawToolDelta(call_index=0, id="empty", name_delta="tool"))
        asm.feed(RawToolDelta(call_index=0, args_delta=""))
        result = asm.feed(RawToolDelta(call_index=0, done=True))
        assert len(result) == 1
        assert result[0].arguments == {}


class TestReset:
    """reset() should clear all state."""

    def test_reset_clears_buffers_and_errors(self):
        asm = ToolCallAssembler()

        asm.feed(RawToolDelta(call_index=0, id="x", name_delta="left_over"))
        asm.feed(RawToolDelta(call_index=1, id="y", name_delta="bad"))
        asm.feed(RawToolDelta(call_index=1, args_delta="INVALID"))
        asm.feed(RawToolDelta(call_index=1, done=True))

        assert len(asm.errors) == 1

        asm.reset()

        assert asm.errors == []
        assert asm.flush() == []


class TestIdFallback:
    """When no id is provided, a synthetic id should be generated."""

    def test_missing_id_uses_call_index(self):
        asm = ToolCallAssembler()
        result = asm.feed(
            RawToolDelta(
                call_index=7,
                name_delta="no_id",
                args_delta="{}",
                done=True,
            )
        )
        assert len(result) == 1
        assert result[0].id == "call_7"


class TestNameStripping:
    """Tool names should be stripped of leading/trailing whitespace."""

    def test_whitespace_in_name(self):
        asm = ToolCallAssembler()
        asm.feed(RawToolDelta(call_index=0, id="ws", name_delta="  spaced "))
        asm.feed(RawToolDelta(call_index=0, name_delta=" tool  "))
        result = asm.feed(RawToolDelta(call_index=0, args_delta="{}", done=True))
        assert len(result) == 1
        assert result[0].name == "spaced  tool"
