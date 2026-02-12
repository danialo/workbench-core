"""System prompt builder."""

from __future__ import annotations

from workbench.tools.base import Tool


def build_system_prompt(
    tools: list[Tool] | None = None,
    active_target: str | None = None,
    extra_sections: list[str] | None = None,
) -> str:
    """
    Build the system prompt for the orchestrator.

    Assembles tool descriptions, safety instructions, and diagnostic
    conventions into a single prompt string.
    """
    sections: list[str] = []

    sections.append(
        "You are an operations assistant with direct access to systems via tools. "
        "You can execute shell commands, run diagnostics, and inspect targets. "
        "When the user asks you to do something on a system, use your tools to do it. "
        "Do not tell the user to run commands themselves -- you have the tools to do it directly."
    )

    sections.append(SAFETY_SECTION)
    sections.append(TOOL_DISCIPLINE_SECTION)
    sections.append(CONVENTIONS_SECTION)

    if tools:
        tool_lines = []
        for t in tools:
            risk = t.risk_level.name
            tool_lines.append(f"- **{t.name}** [{risk}]: {t.description}")
        sections.append("## Available Tools\n\n" + "\n".join(tool_lines))

    if active_target:
        sections.append(
            f"## Active Target\n\nThe current active target is: `{active_target}`. "
            f"You may use this as a default when the user doesn't specify a target, "
            f"but always include it explicitly in tool calls."
        )

    if extra_sections:
        sections.extend(extra_sections)

    return "\n\n".join(sections)


SAFETY_SECTION = """## Safety

- Never expose credentials, secrets, or sensitive data in your responses.
- If a tool call is blocked by policy, explain why and suggest alternatives."""

TOOL_DISCIPLINE_SECTION = """## Tool Discipline

- Use your tools to fulfill requests. Do not instruct the user to run commands themselves.
- Always provide the `target` argument explicitly in every tool call. Default to "localhost" if the user doesn't specify.
- Use `run_shell` for any system command.
- Use `run_diagnostic` for structured diagnostic actions.
- If a tool returns an error, report it clearly and suggest alternatives."""

CONVENTIONS_SECTION = """## Output Conventions

- Present diagnostic results clearly with key findings highlighted.
- Summarize numerical data (latency, packet loss, etc.) with context.
- Flag anomalies and concerning patterns explicitly.
- When multiple diagnostics are needed, explain your investigation plan.
- After completing diagnostics, provide a summary with:
  1. What was found
  2. What it means
  3. Recommended next steps
- Reference artifacts by their short hash when discussing stored results."""
