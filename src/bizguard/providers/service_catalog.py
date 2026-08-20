"""Catalog evidence read from the selected catalog revision."""

from pathlib import Path
import yaml  # type: ignore[import-untyped]

from bizguard.domain.models import Evidence


class ServiceCatalogProvider:
    def __init__(self, catalog: Path, revision: str) -> None:
        self.catalog = catalog
        self.revision = revision

    def collect(self) -> list[Evidence]:
        raw = yaml.safe_load(self.catalog.read_text(encoding="utf-8")) or {}
        return [
            Evidence(
                id=f"catalog:{item['id']}", source="catalog", confidence=1.0,
                revision=self.revision, evidence_uri=f"catalog://{self.catalog.name}#{item['id']}",
            )
            for item in raw.get("capabilities", [])
        ]
