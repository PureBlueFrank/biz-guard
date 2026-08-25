"""Validated process settings for a production BizGuard MCP deployment."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bizguard.change.store import ContextStore
    from bizguard.knowledge.search import VectorAdapter
    from bizguard.workflow.store import ApprovalStore


_PROJECT_ROOT = Path(__file__).parents[2]


def _secret(name: str) -> str | None:
    """Read one secret from an environment value or an explicitly mounted file."""
    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if direct and file_name:
        raise ValueError(f"{name} and {name}_FILE are mutually exclusive")
    if file_name:
        try:
            value = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"unable to read {name}_FILE") from exc
        return value or None
    return direct or None


@dataclass(frozen=True)
class GovernancePaths:
    """Resolve every organization-owned governance input from the environment."""

    catalog: Path
    policy_registry: Path
    contract_registry: Path
    invariants: Path
    knowledge: Path
    invariant_knowledge: Path
    calibration_gates: Path
    calibration_public_key: Path

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
            calibration_gates=Path(
                os.environ.get(
                    "BIZGUARD_CALIBRATION_GATES_PATH",
                    _PROJECT_ROOT / "policy/calibration-gates.yaml",
                )
            ).resolve(),
            calibration_public_key=Path(
                os.environ.get(
                    "BIZGUARD_CALIBRATION_PUBLIC_KEY_PATH",
                    _PROJECT_ROOT / "policy/calibration-public-key.pem",
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
            "calibration_gates": self.calibration_gates.is_file(),
            "calibration_public_key": self.calibration_public_key.is_file(),
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
    database_url: str | None = field(repr=False)
    database_pool_min_size: int
    database_pool_max_size: int
    identity: str
    roles: tuple[str, ...]
    auth_mode: str
    required_scopes: tuple[str, ...]
    auth_jwks_url: str | None
    auth_audience: str | None
    auth_algorithms: tuple[str, ...]
    allow_static_auth: bool
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    issuer_url: str | None
    resource_url: str | None
    governance: GovernancePaths
    embedding_provider: str
    embedding_cache_dir: Path
    embedding_dimensions: int
    embedding_timeout_seconds: float
    embedding_api_key: str | None = field(repr=False)
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
        database_url = _secret("BIZGUARD_DATABASE_URL")
        try:
            database_pool_min_size = int(os.environ.get("BIZGUARD_DATABASE_POOL_MIN_SIZE", "1"))
            database_pool_max_size = int(os.environ.get("BIZGUARD_DATABASE_POOL_MAX_SIZE", "10"))
        except ValueError as exc:
            raise ValueError("database pool sizes must be integers") from exc
        identity = os.environ.get("BIZGUARD_CALLER_IDENTITY", "engineering").strip()
        roles = tuple(
            sorted(
                role.strip()
                for role in os.environ.get("BIZGUARD_CALLER_ROLES", "engineering").split(",")
                if role.strip()
            )
        )
        auth_mode = os.environ.get(
            "BIZGUARD_AUTH_MODE", "oidc" if transport == "streamable-http" else "static"
        ).strip()
        required_scopes = tuple(
            sorted(
                scope.strip()
                for scope in os.environ.get("BIZGUARD_REQUIRED_SCOPES", "bizguard:use").split(",")
                if scope.strip()
            )
        )
        auth_jwks_url = os.environ.get("BIZGUARD_AUTH_JWKS_URL") or None
        auth_audience = os.environ.get("BIZGUARD_AUTH_AUDIENCE") or None
        auth_algorithms = tuple(
            algorithm.strip()
            for algorithm in os.environ.get("BIZGUARD_AUTH_ALGORITHMS", "RS256").split(",")
            if algorithm.strip()
        )
        allow_static_auth = os.environ.get("BIZGUARD_ALLOW_STATIC_AUTH", "false").lower() in {
            "1",
            "true",
            "yes",
        }
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
        api_token = _secret("BIZGUARD_API_TOKEN")
        issuer_url = os.environ.get("BIZGUARD_AUTH_ISSUER_URL") or None
        resource_url = os.environ.get("BIZGUARD_RESOURCE_URL") or None
        embedding_provider = os.environ.get(
            "BIZGUARD_EMBEDDING_PROVIDER",
            "zhipu" if transport == "streamable-http" else "local",
        ).strip()
        embedding_api_key = _secret("BIZGUARD_ZHIPU_API_KEY")
        embedding_cache_dir = Path(
            os.environ.get(
                "BIZGUARD_EMBEDDING_CACHE_DIR",
                "/var/lib/bizguard/embeddings"
                if transport == "streamable-http"
                else _PROJECT_ROOT / ".cache/embeddings",
            )
        ).resolve()
        try:
            embedding_dimensions = int(os.environ.get("BIZGUARD_EMBEDDING_DIMENSIONS", "1024"))
            embedding_timeout_seconds = float(
                os.environ.get("BIZGUARD_EMBEDDING_TIMEOUT_SECONDS", "20")
            )
        except ValueError as exc:
            raise ValueError("embedding dimensions and timeout must be numeric") from exc
        governance = GovernancePaths.from_env()
        settings = cls(
            transport=transport,
            host=host,
            port=port,
            repository_root=repository_root,
            approval_db=approval_db,
            context_db=context_db,
            database_url=database_url,
            database_pool_min_size=database_pool_min_size,
            database_pool_max_size=database_pool_max_size,
            identity=identity,
            roles=roles,
            auth_mode=auth_mode,
            required_scopes=required_scopes,
            auth_jwks_url=auth_jwks_url,
            auth_audience=auth_audience,
            auth_algorithms=auth_algorithms,
            allow_static_auth=allow_static_auth,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            issuer_url=issuer_url,
            resource_url=resource_url,
            governance=governance,
            embedding_provider=embedding_provider,
            embedding_cache_dir=embedding_cache_dir,
            embedding_dimensions=embedding_dimensions,
            embedding_timeout_seconds=embedding_timeout_seconds,
            embedding_api_key=embedding_api_key,
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
        if self.auth_mode not in {"static", "oidc"}:
            raise ValueError("BIZGUARD_AUTH_MODE must be static or oidc")
        if not self.required_scopes:
            raise ValueError("BIZGUARD_REQUIRED_SCOPES must not be empty")
        if not self.auth_algorithms or not set(self.auth_algorithms) <= {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
            raise ValueError("BIZGUARD_AUTH_ALGORITHMS contains an unsafe or unsupported algorithm")
        if self.database_pool_min_size < 0 or self.database_pool_max_size < max(
            1, self.database_pool_min_size
        ):
            raise ValueError("invalid database pool size")
        if self.database_url is not None and not self.database_url.startswith(
            ("postgresql://", "postgres://")
        ):
            raise ValueError("BIZGUARD_DATABASE_URL must use PostgreSQL")
        if self.embedding_provider not in {"local", "zhipu"}:
            raise ValueError("BIZGUARD_EMBEDDING_PROVIDER must be local or zhipu")
        if not 256 <= self.embedding_dimensions <= 2048:
            raise ValueError("BIZGUARD_EMBEDDING_DIMENSIONS must be between 256 and 2048")
        if self.embedding_timeout_seconds <= 0:
            raise ValueError("BIZGUARD_EMBEDDING_TIMEOUT_SECONDS must be positive")
        unavailable = [name for name, ready in self.governance.readiness().items() if not ready]
        if unavailable:
            raise ValueError(f"governance inputs are unavailable: {', '.join(unavailable)}")
        if self.transport == "stdio":
            return
        if self.embedding_provider != "zhipu" or self.embedding_api_key is None:
            raise ValueError("HTTP production mode requires Zhipu embedding credentials")
        required_governance_variables = (
            "BIZGUARD_REPOSITORY_ROOT",
            "BIZGUARD_CATALOG_PATH",
            "BIZGUARD_POLICY_REGISTRY_PATH",
            "BIZGUARD_CONTRACT_REGISTRY_PATH",
            "BIZGUARD_INVARIANTS_PATH",
            "BIZGUARD_KNOWLEDGE_ROOT",
            "BIZGUARD_INVARIANT_KNOWLEDGE_ROOT",
            "BIZGUARD_CALIBRATION_GATES_PATH",
            "BIZGUARD_CALIBRATION_PUBLIC_KEY_PATH",
        )
        missing = [name for name in required_governance_variables if not os.environ.get(name)]
        if missing:
            raise ValueError(
                "HTTP production mode requires explicit governance inputs: " + ", ".join(missing)
            )
        if self.database_url is None:
            raise ValueError("HTTP production mode requires BIZGUARD_DATABASE_URL")
        if not self.allowed_hosts:
            raise ValueError("BIZGUARD_ALLOWED_HOSTS is required for HTTP production mode")
        if not self.issuer_url or not self.resource_url:
            raise ValueError("HTTP production mode requires issuer and resource URLs")
        if not self.issuer_url.startswith(("http://", "https://")) or not self.resource_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("issuer and resource URLs must use HTTP or HTTPS")
        if self.auth_mode == "static":
            if not self.allow_static_auth:
                raise ValueError("HTTP static authentication requires explicit demo-only opt-in")
            if self.api_token is None or len(self.api_token) < 32:
                raise ValueError("BIZGUARD_API_TOKEN must contain at least 32 characters")
        else:
            if not self.auth_jwks_url or not self.auth_audience:
                raise ValueError("OIDC mode requires JWKS URL and audience")
            if not self.issuer_url.startswith("https://") or not self.auth_jwks_url.startswith(
                "https://"
            ):
                raise ValueError("OIDC issuer and JWKS URLs must use HTTPS")

    def readiness(self) -> dict[str, bool]:
        """Return non-secret readiness facts for health checks and diagnostics."""
        approval_parent = self.approval_db.parent if self.approval_db else None
        context_parent = self.context_db.parent if self.context_db else None
        return {
            "repository": self.repository_root.is_dir()
            and os.access(self.repository_root, os.R_OK | os.X_OK),
            "approval_store": self.database_url is not None
            or approval_parent is None
            or (approval_parent.is_dir() and os.access(approval_parent, os.W_OK | os.X_OK)),
            "context_store": self.database_url is not None
            or context_parent is None
            or (context_parent.is_dir() and os.access(context_parent, os.W_OK | os.X_OK)),
            "authenticated": self.transport == "stdio"
            or (self.auth_mode == "static" and self.api_token is not None)
            or (
                self.auth_mode == "oidc"
                and self.auth_jwks_url is not None
                and self.auth_audience is not None
            ),
            "embedding": self.embedding_provider == "local" or self.embedding_api_key is not None,
        } | self.governance.readiness()

    def approval_store(self) -> "ApprovalStore":
        """Create the configured approval store."""
        from bizguard.workflow.store import PostgresApprovalStore, SqliteApprovalStore

        if self.database_url is not None:
            return PostgresApprovalStore(
                self.database_url,
                min_pool_size=self.database_pool_min_size,
                max_pool_size=self.database_pool_max_size,
            )
        if self.approval_db is None:
            raise ValueError("approval store is not configured")
        return SqliteApprovalStore(self.approval_db)

    def context_store(self) -> "ContextStore":
        """Create the configured Context Pack store."""
        from bizguard.change.store import ChangeContextStore, PostgresChangeContextStore

        if self.database_url is not None:
            return PostgresChangeContextStore(
                self.database_url,
                min_pool_size=self.database_pool_min_size,
                max_pool_size=self.database_pool_max_size,
            )
        if self.context_db is None:
            raise ValueError("context store is not configured")
        return ChangeContextStore(self.context_db)

    def vector_adapter(self) -> "VectorAdapter":
        """Build the configured semantic scorer without exposing provider credentials."""
        from bizguard.knowledge.search import EmbeddingVectorAdapter, LocalVectorAdapter
        from bizguard.rag.embedding import ZhipuEmbeddingClient

        if self.embedding_provider == "local":
            return LocalVectorAdapter()
        if self.embedding_api_key is None:  # validated above; defensive for direct construction
            raise ValueError("Zhipu embedding credentials are unavailable")
        return EmbeddingVectorAdapter(
            ZhipuEmbeddingClient(
                self.embedding_api_key,
                self.embedding_cache_dir,
                timeout_seconds=self.embedding_timeout_seconds,
                dimensions=self.embedding_dimensions,
            )
        )
