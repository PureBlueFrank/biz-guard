"""Live FastMCP resource discovery and read tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from mcp.server.fastmcp.exceptions import ResourceError

from agents_mcp.server import mcp


ROOT = Path(__file__).parents[1]


def _read(uri: str) -> dict[str, object]:
    contents = list(asyncio.run(mcp.read_resource(uri)))
    assert len(contents) == 1
    content = contents[0].content
    assert isinstance(content, str)
    payload = json.loads(content)
    assert isinstance(payload, dict)
    return payload


def test_all_five_resources_are_registered_as_templates() -> None:
    templates = asyncio.run(mcp.list_resource_templates())
    uris = {template.uriTemplate for template in templates}
    assert uris == {
        "bizguard://changes/{change_context_id}",
        "bizguard://symbols/{symbol_id}",
        "bizguard://capabilities/{capability_id}",
        "bizguard://policies/{policy_id}",
        "bizguard://evidence/{evidence_id}",
    }


def test_policy_resource_returns_summary_not_full_registry() -> None:
    payload = _read("bizguard://policies/published-dto-backward-compatible")
    assert payload["id"] == "published-dto-backward-compatible"
    assert payload["severity"] == "high"
    assert "summary" in payload


def test_capability_resource_returns_summary() -> None:
    payload = _read("bizguard://capabilities/dto_field_contract")
    assert payload["id"] == "dto_field_contract"
    assert payload["owner"] == "coupon_platform"
    assert "summary" in payload


def test_symbol_resource_returns_summary() -> None:
    symbol = "db://coupon-core/coupon_redemption#status"
    payload = _read(f"bizguard://symbols/{quote(symbol, safe='')}")
    assert payload["id"] == symbol
    assert payload["kind"] == "data"


def test_evidence_resource_reads_from_knowledge_store() -> None:
    payload = _read("bizguard://evidence/field-ledger-status")
    assert payload["id"] == "field-ledger-status"
    assert payload["evidence_links"]


def test_change_resource_reads_from_persisted_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents_mcp.server as server

    monkeypatch.setenv("BIZGUARD_APPROVAL_DB", str(tmp_path / "approvals.sqlite3"))
    monkeypatch.setattr(server, "_approval_store", None)
    asyncio.run(
        mcp.call_tool(
            "request_approval",
            {
                "change_context_id": "ctx-res",
                "policy_revision": "phase5",
                "approvers": ["coupon_platform"],
                "required_cosigns": 1,
            },
        )
    )
    payload = _read("bizguard://changes/ctx-res")
    assert payload["change_context_id"] == "ctx-res"
    assert payload["summary"] == "pending"


def test_missing_resource_returns_error_without_extra_information() -> None:
    with pytest.raises((ValueError, ResourceError), match="resource unavailable"):
        _read("bizguard://policies/does-not-exist")
