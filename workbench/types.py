from dataclasses import dataclass, field


@dataclass
class ArtifactPayload:
    content: bytes
    original_name: str = ""
    media_type: str = "text/plain"
    description: str = ""


@dataclass
class ArtifactRef:
    sha256: str
    stored_path: str
    original_name: str = ""
    media_type: str = "text/plain"
    description: str = ""
    size_bytes: int = 0


@dataclass
class ToolResult:
    success: bool
    content: str
    data: dict | list | None = None
    artifact_payloads: list[ArtifactPayload] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    metadata: dict = field(default_factory=dict)


class ErrorCode:
    VALIDATION_ERROR = "validation_error"
    POLICY_BLOCK = "policy_block"
    TIMEOUT = "timeout"
    TOOL_EXCEPTION = "tool_exception"
    UNKNOWN_TOOL = "unknown_tool"
    CANCELLED = "cancelled"
    BACKEND_ERROR = "backend_error"
    LLM_PROTOCOL_ERROR = "llm_protocol_error"


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    requires_confirmation: bool = False


@dataclass
class ContextPackReport:
    max_context_tokens: int
    max_output_tokens: int
    reserve_tokens: int
    tool_schema_tokens: int
    system_prompt_tokens: int
    message_tokens: int
    kept_messages: int
    dropped_messages: int
