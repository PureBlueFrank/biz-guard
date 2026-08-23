"""Production transport configuration fails closed and never exposes secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from bizguard.production import ProductionSettings
from bizguard.cli import _check_production_config


ROOT = Path(__file__).parents[1]


def _http_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repository = tmp_path / "repositories"
    state = tmp_path / "state"
    repository.mkdir()
    state.mkdir()
    monkeypatch.setenv("BIZGUARD_TRANSPORT", "streamable-http")
    monkeypatch.setenv("BIZGUARD_REPOSITORY_ROOT", str(repository))
    monkeypatch.setenv("BIZGUARD_APPROVAL_DB", str(state / "approvals.sqlite3"))
    monkeypatch.setenv("BIZGUARD_CONTEXT_DB", str(state / "contexts.sqlite3"))
    monkeypatch.setenv("BIZGUARD_CALLER_IDENTITY", "coupon-platform-ci")
    monkeypatch.setenv("BIZGUARD_CALLER_ROLES", "engineering,coupon_platform")
    monkeypatch.setenv("BIZGUARD_ALLOWED_HOSTS", "bizguard.internal.example")
    monkeypatch.setenv("BIZGUARD_AUTH_ISSUER_URL", "https://auth.internal.example")
    monkeypatch.setenv("BIZGUARD_RESOURCE_URL", "https://bizguard.internal.example/mcp")
    monkeypatch.setenv(
        "BIZGUARD_CATALOG_PATH", str(ROOT / "src/bizguard/semantic/catalog.yaml")
    )
    monkeypatch.setenv(
        "BIZGUARD_POLICY_REGISTRY_PATH", str(ROOT / "policy/phase5-registry.yaml")
    )
    monkeypatch.setenv(
        "BIZGUARD_CONTRACT_REGISTRY_PATH", str(ROOT / "registry/contracts.yaml")
    )
    monkeypatch.setenv("BIZGUARD_INVARIANTS_PATH", str(ROOT / "policy/invariants.yaml"))
    monkeypatch.setenv("BIZGUARD_KNOWLEDGE_ROOT", str(ROOT / "knowledge/published"))
    monkeypatch.setenv("BIZGUARD_INVARIANT_KNOWLEDGE_ROOT", str(ROOT / "knowledge"))


def test_http_mode_requires_a_strong_bearer_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _http_environment(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="API_TOKEN"):
        ProductionSettings.from_env()


def test_http_mode_has_persistent_ready_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _http_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("BIZGUARD_API_TOKEN", "a" * 32)
    settings = ProductionSettings.from_env()
    assert settings.transport == "streamable-http"
    assert settings.roles == ("coupon_platform", "engineering")
    assert all(settings.readiness().values())
    assert "a" * 32 not in repr(settings)
    assert _check_production_config() == "ok"


def test_http_mode_requires_dns_rebinding_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _http_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("BIZGUARD_API_TOKEN", "a" * 32)
    monkeypatch.delenv("BIZGUARD_ALLOWED_HOSTS")
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        ProductionSettings.from_env()


def test_http_mode_requires_explicit_governance_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _http_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("BIZGUARD_API_TOKEN", "a" * 32)
    monkeypatch.delenv("BIZGUARD_CATALOG_PATH")
    with pytest.raises(ValueError, match="explicit governance inputs"):
        ProductionSettings.from_env()
