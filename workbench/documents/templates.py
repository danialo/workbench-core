"""
Deterministic narrative templates for M3.

Pure functions — no LLM dependency.

Templates:
  default_internal_v1  — structured Markdown for analysts
  default_customer_v1  — plain summary for external reporting
"""

from __future__ import annotations

import hashlib
import json


# ---------------------------------------------------------------------------
# Input hashing
# ---------------------------------------------------------------------------

def generation_inputs_hash(
    audience: str,
    template_id: str,
    source_assertion_ids: list[str],
    approved_assertions: list[dict],
) -> str:
    """
    Stable SHA-256 of narrative generation inputs.

    Sorted so the hash is deterministic regardless of dict key ordering or
    the order assertions were added to the document.
    """
    inputs = {
        "audience": audience,
        "template_id": template_id,
        "assertion_ids": sorted(source_assertion_ids),
        "assertions": [
            {
                "id": a.get("id", ""),
                "claim": a.get("claim", ""),
                "evidence": sorted(
                    [
                        {
                            "artifact_ref": e.get("artifact_ref", ""),
                            "byte_start": e.get("byte_start", 0),
                            "byte_end": e.get("byte_end", 0),
                        }
                        for e in a.get("evidence", [])
                    ],
                    key=lambda e: (e["artifact_ref"], e["byte_start"]),
                ),
            }
            for a in sorted(approved_assertions, key=lambda a: a.get("id", ""))
        ],
    }
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Internal template (v1)
# ---------------------------------------------------------------------------

def render_internal(
    *,
    investigation_id: str,
    document_id: str,
    source_revision: int,
    generated_at: str,
    approved_assertions: list[dict],
    rejected_assertions: list[dict],
) -> str:
    """
    Internal narrative — structured Markdown for analyst review.

    Sections:
      # Internal Investigation Narrative
      [header metadata]
      ## Approved Assertions
        ### 1. <claim>
           - Evidence: artifact, byte range, line range
      ## Rejected Assertions (optional)
        - <claim>
    """
    lines: list[str] = [
        "# Internal Investigation Narrative",
        "",
        f"**Investigation:** {investigation_id}  ",
        f"**Document:** {document_id}  ",
        f"**Source Revision:** {source_revision}  ",
        f"**Generated:** {generated_at}",
        "",
        "---",
        "",
    ]

    if approved_assertions:
        lines.append("## Approved Assertions")
        lines.append("")
        for i, a in enumerate(approved_assertions, 1):
            lines.append(f"### {i}. {a.get('claim', '(no claim)')}")
            lines.append("")
            evidence = a.get("evidence", [])
            if evidence:
                lines.append("**Evidence:**")
                lines.append("")
                for ev in evidence:
                    art = ev.get("artifact_ref", "")[:12]
                    bs = ev.get("byte_start", 0)
                    be = ev.get("byte_end", 0)
                    ls = ev.get("line_start", 0)
                    le = ev.get("line_end", 0)
                    note = ev.get("note", "")
                    span = f"artifact `{art}…` bytes {bs}–{be} (lines {ls + 1}–{le + 1})"
                    if note:
                        span += f" — {note}"
                    lines.append(f"- {span}")
                lines.append("")
    else:
        lines += ["## Approved Assertions", "", "_None_", ""]

    if rejected_assertions:
        lines.append("## Rejected Assertions")
        lines.append("")
        for a in rejected_assertions:
            lines.append(f"- {a.get('claim', '(no claim)')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Customer template (v1)
# ---------------------------------------------------------------------------

def render_customer(
    *,
    approved_assertions: list[dict],
    **_kwargs,  # absorb unused keyword args for uniform call signature
) -> str:
    """
    Customer-facing narrative — concise Markdown for external distribution.

    Sections:
      ## Summary
        <one-paragraph summary from approved claims>
      ## What We Observed
        - <claim>
        - <claim>
    """
    if not approved_assertions:
        return "_No findings have been approved for customer reporting._"

    claims = [a.get("claim", "") for a in approved_assertions]

    # Single-sentence summary: join claims, each ending with a period
    summary = " ".join(
        (c.rstrip(" .") + ".") for c in claims if c
    )

    lines: list[str] = [
        "## Summary",
        "",
        summary,
        "",
        "## What We Observed",
        "",
    ]
    for claim in claims:
        lines.append(f"- {claim}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, str] = {
    "internal": "default_internal_v1",
    "customer": "default_customer_v1",
}


def build_narrative(
    *,
    audience: str,
    template_id: str = "",
    investigation_id: str,
    document_id: str,
    source_revision: int,
    generated_at: str,
    approved_assertions: list[dict],
    rejected_assertions: list[dict],
) -> str:
    """
    Render a narrative using the appropriate template.

    template_id defaults to the standard template for the given audience.
    """
    if not template_id:
        template_id = TEMPLATES.get(audience, "default_internal_v1")

    kwargs = dict(
        investigation_id=investigation_id,
        document_id=document_id,
        source_revision=source_revision,
        generated_at=generated_at,
        approved_assertions=approved_assertions,
        rejected_assertions=rejected_assertions,
    )

    if template_id in ("default_internal_v1", "internal"):
        return render_internal(**kwargs)
    if template_id in ("default_customer_v1", "customer"):
        return render_customer(**kwargs)

    # Unknown template — fall back to internal
    return render_internal(**kwargs)
