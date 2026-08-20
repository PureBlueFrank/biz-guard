"""On-demand MCP resource summaries; content stays in the core evidence stores."""

from pathlib import Path


def policy_summary(root: Path) -> dict[str, object]:
    """Describe the policy registry as an on-demand MCP resource."""
    policy = root / "policy" / "invariants.yaml"
    return {"uri": f"file://{policy}", "summary": "Policy YAML invariant registry", "evidence_links": [str(policy)]}


def change_summary(change_context_id: str, evidence_links: list[str]) -> dict[str, object]:
    """Summarize the evidence links associated with a change context."""
    return {"change_context_id": change_context_id, "summary": "evidence available on demand", "evidence_links": evidence_links}
