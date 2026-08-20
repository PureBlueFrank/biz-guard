"""Regression coverage for the reproducible milestone 6 demonstration."""

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).parent.parent


def test_demo_script_replays_all_deterministic_outcomes() -> None:
    """The demo includes the control group and every decision state it promises."""
    result = subprocess.run(
        ["sh", "scripts/demo.sh"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "原生 Coding Agent（模拟，确定性）：代码可编译、改动看似合理，放行。" in result.stdout
    assert result.stdout.count('"decision":"BLOCK"') == 1
    assert result.stdout.count('"decision":"ALLOW"') == 1
    assert result.stdout.count('"decision":"REQUIRE_APPROVAL"') == 1
    assert '"evidence":["fault:policy_uncovered"]' in result.stdout
