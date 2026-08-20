"""P0 metadata guards for stale knowledge and the optional Java parser spike."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]


ROOT = Path(__file__).parent.parent


def test_knowledge_documents_have_revision_and_source_commit_stale_anchors() -> None:
    for document in (ROOT / "knowledge").glob("*.md"):
        _, frontmatter, _ = document.read_text(encoding="utf-8").split("---", 2)
        metadata = yaml.safe_load(frontmatter)
        assert metadata["revision"] == "semantic-seed-v1"
        assert len(metadata["source_commit"]) == 40


def test_tree_sitter_java_can_parse_every_fixture_source_without_error_nodes() -> None:
    tree_sitter = pytest.importorskip("tree_sitter")
    tree_sitter_java = pytest.importorskip("tree_sitter_java")
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_java.language()))
    for source in (ROOT / "fixtures" / "java-microservices").glob("*/src/main/java/**/*.java"):
        tree = parser.parse(source.read_bytes())
        assert not tree.root_node.has_error, source
