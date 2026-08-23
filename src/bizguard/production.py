"""Validated process settings for a production BizGuard MCP deployment."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class GovernancePaths:
    """Resolve every organization-owned governance input from the environment."""

    catalog: Path
    policy_registry: Path
    contract_registry: Path
    invariants: Path
    knowledge: Path
    invariant_knowledge: Path

    @classmethod
    def from_env(cls) -> "GovernancePaths":
        return cls(
            catalog=Path(
                os.environ.get(
                    "BIZGUARD_CATALOG_PATH",
                    _PROJECT_ROOT / "src/bizguard/semantic/catalog.yaml",
                )
            ).resolve(),
            policy_registry=Path(
                os.environ.get(
                    "BIZGUARD_POLICY_REGISTRY_PATH",
                    _PROJECT_ROOT / "policy/phase5-registry.yaml",
                )
            ).resolve(),
            contract_registry=Path(
                os.environ.get(
                    "BIZGUARD_CONTRACT_REGISTRY_PATH",
                    _PROJECT_ROOT / "registry/contracts.yaml",
                )
            ).resolve(),
            invariants=Path(
                os.environ.get(
                    "BIZGUARD_INVARIANTS_PATH",
                    _PROJECT_ROOT / "policy/invariants.yaml",
                )
            ).resolve(),
            knowledge=Path(
                os.environ.get(
                    "BIZGUARD_KNOWLEDGE_ROOT",
                    _PROJECT_ROOT / "knowledge/published",
                )
            ).resolve(),
            invariant_knowledge=Path(
                os.environ.get(
                    "BIZGUARD_INVARIANT_KNOWLEDGE_ROOT",
                    _PROJECT_ROOT / "knowledge",
                )
            ).resolve(),
        )

    def readiness(self) -> dict[str, bool]:
        """Return only path availability; file contents are validated by their loaders."""
        return {
            "catalog": self.catalog.is_file(),
            "policy_registry": self.policy_registry.is_file(),
            "contract_registry": self.contract_registry.is_file(),
            "invariants": self.invariants.is_file(),
            "knowledge": self.knowledge.is_dir(),
            "invariant_knowledge": self.invariant_knowledge.is_dir(),
        }


@dataclass(frozen=True)
class ProductionSettings:
    """Hold validated transport, identity, network, and persistence settings."""

    transport: str
    host: str
    port: int
    repository_root: Path
    approval_db: Path | None
    context_db: Path | None
    identity: str
    roles: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    issuer_url: str | None
    resource_url: str | None
    governance: GovernancePaths
    api_token: str | None = field(repr=False)

    @classmethod
    def from_env(cls) -> "ProductionSettings":
        transport = os.environ.get("BIZGUARD_TRANSPORT", "stdio").strip()
        if transport not in {"stdio", "streamable-http"}:
            raise ValueError("BIZGUARD_TRANSPORT must be stdio or streamable-http")
        host = os.environ.get("BIZGUARD_HOST", "127.0.0.1").strip()
        try:
            port = int(os.environ.get("BIZGUARD_PORT", "8000"))
        except ValueError as exc:
            raise ValueError("BIZGUARD_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("BIZGUARD_PORT must be between 1 and 65535")
        repository_root = Path(
            os.environ.get("BIZGUARD_REPOSITORY_ROOT", "fixtures/java-microservices")
        ).resolve()
        approval_raw = os.environ.get("BIZGUARD_APPROVAL_DB")
        context_raw = os.environ.get("BIZGUARD_CONTEXT_DB")
        approval_db = Path(approval_raw).resolve() if approval_raw else None
        context_db = Path(context_raw).resolve() if context_raw else None
        identity = os.environ.get("BIZGUARD_CALLER_IDENTITY", "engineering").strip()
        roles = tuple(
            sorted(
                role.strip()
                for role in os.environ.get("BIZGUARD_CALLER_ROLES", "engineering").split(",")
                if role.strip()
            )
        )
        allowed_hosts = tuple(
            item.strip()
            for item in os.environ.get("BIZGUARD_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        allowed_origins = tuple(
            item.strip()
            for item in os.environ.get("BIZGUARD_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        )
        api_token = os.environ.get("BIZGUARD_API_TOKEN") or None
        issuer_url = os.environ.get("BIZGUARD_AUTH_ISSUER_URL") or None
        resource_url = os.environ.get("BIZGUARD_RESOURCE_URL") or None
        governance = GovernancePaths.from_env()
        settings = cls(
            transport=transport,
            host=host,
            port=port,
            repository_root=repository_root,
            approval_db=approval_db,
            context_db=context_db,
            identity=identity,
            roles=roles,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            issuer_url=issuer_url,
            resource_url=resource_url,
            governance=governance,
            api_token=api_token,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Reject unsafe or incomplete HTTP production configuration."""
        if not self.repository_root.is_dir():
            raise ValueError("BIZGUARD_REPOSITORY_ROOT must be an existing directory")
        if not self.identity or not self.roles:
            raise ValueError("authenticated identity and at least one role are required")
        unavailable = [name for name, ready in self.governance.readiness().items() if not ready]
        if unavailable:
            raise ValueError(f"governance inputs are unavailable: {', '.join(unavailable)}")
        if self.transport == "stdio":
            return
        required_governance_variables = (
            "BIZGUARD_CATALOG_PATH",
            "BIZGUARD_POLICY_REGISTRY_PATH",
            "BIZGUARD_CONTRACT_REGISTRY_PATH",
            "BIZGUARD_INVARIANTS_PATH",
            "BIZGUARD_KNOWLEDGE_ROOT",
            "BIZGUARD_INVARIANT_KNOWLEDGE_ROOT",
        )
        missing = [name for name in required_governance_variables if not os.environ.get(name)]
        if missing:
            raise ValueError(
                "HTTP production mode requires explicit governance inputs: " + ", ".join(missing)
            )
        if self.api_token is None or len(self.api_token) < 32:
            raise ValueError("BIZGUARD_API_TOKEN must contain at least 32 characters")
        if self.approval_db is None or self.context_db is None:
            raise ValueError("HTTP production mode requires persistent approval and context databases")
        if not self.allowed_hosts:
            raise ValueError("BIZGUARD_ALLOWED_HOSTS is required for HTTP production mode")
        if not self.issuer_url or not self.resource_url:
            raise ValueError("HTTP production mode requires issuer and resource URLs")
        if not self.issuer_url.startswith(("http://", "https://")) or not self.resource_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("issuer and resource URLs must use HTTP or HTTPS")

    def readiness(self) -> dict[str, bool]:
        """Return non-secret readiness facts for health checks and diagnostics."""
        approval_parent = self.approval_db.parent if self.approval_db else None
        context_parent = self.context_db.parent if self.context_db else None
        return {
            "repository": self.repository_root.is_dir()
            and os.access(self.repository_root, os.R_OK | os.X_OK),
            "approval_store": approval_parent is None
            or (approval_parent.is_dir() and os.access(approval_parent, os.W_OK | os.X_OK)),
            "context_store": context_parent is None
            or (context_parent.is_dir() and os.access(context_parent, os.W_OK | os.X_OK)),
            "authenticated": self.transport == "stdio" or self.api_token is not None,
        } | self.governance.readiness()
