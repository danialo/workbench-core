import asyncio
import json
import re
from pathlib import Path
from datetime import datetime, timezone

from workbench.tools.base import Tool, ToolRisk, PrivacyScope
from workbench.types import ToolResult, PolicyDecision


class PolicyEngine:
    def __init__(
        self,
        *,
        max_risk: ToolRisk = ToolRisk.READ_ONLY,
        confirm_destructive: bool = True,
        confirm_shell: bool = True,
        confirm_write: bool = False,
        blocked_patterns: list[str] | None = None,
        redaction_patterns: list[str] | None = None,
        audit_log_path: str,
        audit_max_size_mb: int = 10,
        audit_keep_files: int = 5,
    ):
        self.max_risk = max_risk
        self.confirm_destructive = confirm_destructive
        self.confirm_shell = confirm_shell
        self.confirm_write = confirm_write
        self.blocked_patterns = blocked_patterns or []
        self._redaction_patterns = [re.compile(p) for p in (redaction_patterns or [])]
        self.audit_path = Path(audit_log_path).expanduser()
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_max_bytes = audit_max_size_mb * 1024 * 1024
        self.audit_keep_files = audit_keep_files
        self._audit_lock = asyncio.Lock()

    def check(self, tool: Tool, kwargs: dict) -> PolicyDecision:
        if tool.risk_level > self.max_risk:
            return PolicyDecision(
                False,
                f"risk_too_high:{tool.risk_level.name}>{self.max_risk.name}",
            )

        needs_confirm = False
        if tool.risk_level >= ToolRisk.SHELL and self.confirm_shell:
            needs_confirm = True
        elif tool.risk_level >= ToolRisk.DESTRUCTIVE and self.confirm_destructive:
            needs_confirm = True
        elif tool.risk_level >= ToolRisk.WRITE and self.confirm_write:
            needs_confirm = True

        if self.blocked_patterns:
            blob = json.dumps(kwargs, sort_keys=True, default=str)
            for pat in self.blocked_patterns:
                if re.search(pat, blob):
                    return PolicyDecision(False, "blocked_pattern")

        if needs_confirm:
            return PolicyDecision(True, "requires_confirmation", requires_confirmation=True)

        return PolicyDecision(True, "ok")

    def redact_args_for_audit(self, tool: Tool, kwargs: dict) -> dict:
        redacted = dict(kwargs)
        for f in tool.secret_fields:
            if f in redacted:
                redacted[f] = "***REDACTED***"
        for k, v in list(redacted.items()):
            if isinstance(v, str):
                redacted[k] = self._apply_pattern_redaction(v)
        return redacted

    def redact_output_for_audit(self, text: str) -> str:
        return self._apply_pattern_redaction(text)

    def _apply_pattern_redaction(self, s: str) -> str:
        out = s
        for rx in self._redaction_patterns:
            out = rx.sub("***REDACTED***", out)
        return out

    async def audit_log(
        self,
        *,
        session_id: str,
        event_id: str,
        tool: Tool,
        args: dict,
        result: ToolResult,
        duration_ms: int,
        tool_call_id: str,
    ) -> None:
        async with self._audit_lock:
            await self._rotate_if_needed()

            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "event_id": event_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool.name,
                "risk": tool.risk_level.name,
                "privacy": tool.privacy_scope.value,
                "duration_ms": duration_ms,
                "success": result.success,
                "error_code": result.error_code,
                "metadata": result.metadata or {},
            }

            if tool.privacy_scope == PrivacyScope.PUBLIC:
                record["args"] = self.redact_args_for_audit(tool, args)
                record["output"] = self.redact_output_for_audit(result.content[:2000])
            elif tool.privacy_scope == PrivacyScope.SENSITIVE:
                record["args"] = "***REDACTED***"
                record["output"] = self.redact_output_for_audit(result.content[:500])
            else:
                record["args"] = "***REDACTED***"
                record["output"] = "***REDACTED***"

            line = json.dumps(record, sort_keys=True) + "\n"
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()

    async def _rotate_if_needed(self) -> None:
        if self.audit_path.exists() and self.audit_path.stat().st_size < self.audit_max_bytes:
            return

        for i in range(self.audit_keep_files - 1, 0, -1):
            src = self.audit_path.with_suffix(self.audit_path.suffix + f".{i}")
            dst = self.audit_path.with_suffix(self.audit_path.suffix + f".{i + 1}")
            if src.exists():
                src.replace(dst)

        if self.audit_path.exists():
            self.audit_path.replace(
                self.audit_path.with_suffix(self.audit_path.suffix + ".1")
            )
