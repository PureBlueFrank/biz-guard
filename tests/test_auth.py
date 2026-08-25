"""OIDC token verification fails closed on claims, scope, and signature errors."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest

from agents_mcp.server import _OidcTokenVerifier
from bizguard.production import ProductionSettings


ROOT = Path(__file__).parents[1]


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ProductionSettings:
    repository = tmp_path / "repositories"
    repository.mkdir()
    monkeypatch.setenv("BIZGUARD_TRANSPORT", "streamable-http")
    monkeypatch.setenv("BIZGUARD_REPOSITORY_ROOT", str(repository))
    monkeypatch.setenv("BIZGUARD_DATABASE_URL", "postgresql://bizguard:test@postgres.test/bizguard")
    monkeypatch.setenv("BIZGUARD_CALLER_IDENTITY", "fallback-service")
    monkeypatch.setenv("BIZGUARD_CALLER_ROLES", "engineering")
    monkeypatch.setenv("BIZGUARD_AUTH_MODE", "oidc")
    monkeypatch.setenv("BIZGUARD_REQUIRED_SCOPES", "bizguard:use")
    monkeypatch.setenv("BIZGUARD_AUTH_ISSUER_URL", "https://auth.internal.example")
    monkeypatch.setenv("BIZGUARD_AUTH_JWKS_URL", "https://auth.internal.example/jwks.json")
    monkeypatch.setenv("BIZGUARD_AUTH_AUDIENCE", "https://bizguard.internal.example/mcp")
    monkeypatch.setenv("BIZGUARD_RESOURCE_URL", "https://bizguard.internal.example/mcp")
    monkeypatch.setenv("BIZGUARD_ALLOWED_HOSTS", "bizguard.internal.example")
    monkeypatch.setenv("BIZGUARD_EMBEDDING_PROVIDER", "zhipu")
    monkeypatch.setenv("BIZGUARD_ZHIPU_API_KEY", "test-zhipu-key")
    monkeypatch.setenv("BIZGUARD_CATALOG_PATH", str(ROOT / "src/bizguard/semantic/catalog.yaml"))
    monkeypatch.setenv("BIZGUARD_POLICY_REGISTRY_PATH", str(ROOT / "policy/phase5-registry.yaml"))
    monkeypatch.setenv("BIZGUARD_CONTRACT_REGISTRY_PATH", str(ROOT / "registry/contracts.yaml"))
    monkeypatch.setenv("BIZGUARD_INVARIANTS_PATH", str(ROOT / "policy/invariants.yaml"))
    monkeypatch.setenv("BIZGUARD_KNOWLEDGE_ROOT", str(ROOT / "knowledge/published"))
    monkeypatch.setenv("BIZGUARD_INVARIANT_KNOWLEDGE_ROOT", str(ROOT / "knowledge"))
    monkeypatch.setenv(
        "BIZGUARD_CALIBRATION_GATES_PATH", str(ROOT / "policy/calibration-gates.yaml")
    )
    monkeypatch.setenv(
        "BIZGUARD_CALIBRATION_PUBLIC_KEY_PATH",
        str(ROOT / "policy/calibration-public-key.pem"),
    )
    return ProductionSettings.from_env()


def test_oidc_verifier_uses_signed_claims_for_identity_and_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _OidcTokenVerifier(_settings(monkeypatch, tmp_path))
    monkeypatch.setattr(
        verifier._jwk_client,
        "get_signing_key_from_jwt",
        lambda _token: SimpleNamespace(key="public-key"),
    )

    def decode(*_args: object, **kwargs: Any) -> dict[str, object]:
        assert kwargs["audience"] == "https://bizguard.internal.example/mcp"
        assert kwargs["issuer"] == "https://auth.internal.example"
        assert kwargs["algorithms"] == ["RS256"]
        return {
            "sub": "alice",
            "azp": "coding-agent",
            "scope": "bizguard:use",
            "roles": ["engineering", "coupon_platform"],
            "iat": 1,
            "exp": 4_000_000_000,
        }

    monkeypatch.setattr("agents_mcp.server.jwt.decode", decode)
    token = asyncio.run(verifier.verify_token("signed-token"))
    assert token is not None
    assert token.subject == "alice"
    assert token.client_id == "coding-agent"
    assert set(token.scopes) == {"bizguard:use", "engineering", "coupon_platform"}


def test_oidc_verifier_rejects_missing_scope_and_invalid_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _OidcTokenVerifier(_settings(monkeypatch, tmp_path))
    monkeypatch.setattr(
        verifier._jwk_client,
        "get_signing_key_from_jwt",
        lambda _token: SimpleNamespace(key="public-key"),
    )
    monkeypatch.setattr(
        "agents_mcp.server.jwt.decode",
        lambda *_args, **_kwargs: {"sub": "alice", "scope": "other", "iat": 1, "exp": 2},
    )
    assert asyncio.run(verifier.verify_token("missing-scope")) is None

    monkeypatch.setattr(
        "agents_mcp.server.jwt.decode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(jwt.InvalidTokenError()),
    )
    assert asyncio.run(verifier.verify_token("invalid")) is None
