"""Offline Java fixture build contracts."""

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parent.parent
FIXTURES = ("coupon-core", "merchant-service", "coupon-contract")


@pytest.mark.parametrize("fixture", FIXTURES)
def test_each_fixture_compiles_offline(fixture: str) -> None:
    project = ROOT / "fixtures" / "java-microservices" / fixture
    result = subprocess.run(
        ["./mvnw", "--offline", "test"], cwd=project, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert list((project / "target" / "classes").rglob("*.class"))


def test_verifier_builds_all_fixture_repositories() -> None:
    result = subprocess.run(
        ["./scripts/verify_java_fixtures.sh", "--offline"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "verified 3 Java fixtures offline" in result.stdout
