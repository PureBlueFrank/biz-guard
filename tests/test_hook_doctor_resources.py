from pathlib import Path

from agents_mcp.resources import change_summary, policy_summary
from bizguard.hooks.agent import validate
from bizguard.hooks.install import install


ROOT = Path(__file__).parents[1]


def test_hook_delegates_to_recomputation() -> None:
    result = validate((ROOT / "bench/fixtures/phase5/cross-service-dto-breaking.diff").read_text(encoding="utf-8"))
    assert result.decision.value == "REQUIRE_APPROVAL"
    assert "RECONSTRUCTION_INCOMPLETE" in result.findings[0].id


def test_install_writes_local_manifest(tmp_path: Path) -> None:
    manifest = install(tmp_path)
    assert manifest.is_file()
    assert "bizguard.cli hook --repository ." in manifest.read_text(encoding="utf-8")


def test_resource_is_summary_with_evidence_link() -> None:
    resource = policy_summary(ROOT)
    assert resource["summary"] and resource["evidence_links"]


def test_change_resource_is_demand_driven() -> None:
    assert change_summary("c", ["e://1"])["evidence_links"] == ["e://1"]


def test_doctor_resources_exist() -> None:
    assert (ROOT / "src/bizguard/cli.py").is_file()
