"""Regression checks for the frozen BizGuard sample corpus."""

from hashlib import sha256
from pathlib import Path
import re
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).parent.parent
DIFFS_DIR = PROJECT_ROOT / "sample" / "diffs"
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
FROZEN_BASE_HASHES = {
    "sample/coupon-service/redeem_service.py": "1e2723f5cdf3af1ad9919342f888916f59a9c03e7bdb97bdf2e7848cceb0e898",
    "sample/merchant-gateway/coupon_client.py": "2ca0c002c55491808fb901cf27ee3290382ff6d4156653b3319951020533e029",
}


def test_frozen_sample_bases_match_diff_corpus() -> None:
    """Guard the base files on which the frozen unified diffs depend."""
    for relative_path, expected_hash in FROZEN_BASE_HASHES.items():
        actual_hash = sha256((PROJECT_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash


def test_unified_diff_hunk_headers_match_their_bodies() -> None:
    """Ensure every frozen unified diff has trustworthy hunk line counts."""
    for path in sorted(DIFFS_DIR.glob("*.diff")):
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            header = HUNK_HEADER.match(lines[index])
            if header is None:
                index += 1
                continue

            expected_old = int(header.group(2) or 1)
            expected_new = int(header.group(4) or 1)
            actual_old = 0
            actual_new = 0
            index += 1
            while index < len(lines) and HUNK_HEADER.match(lines[index]) is None:
                line = lines[index]
                if line.startswith(("diff --git ", "--- ", "+++ ")):
                    break
                if line.startswith("-"):
                    actual_old += 1
                elif line.startswith("+"):
                    actual_new += 1
                elif line.startswith(" "):
                    actual_old += 1
                    actual_new += 1
                index += 1

            assert (actual_old, actual_new) == (expected_old, expected_new), path


def test_ground_truth_covers_incomplete_decisions() -> None:
    """Keep frozen examples for each decision state."""
    ground_truth = (PROJECT_ROOT / "tests" / "fixtures" / "ground_truth.yaml").read_text(
        encoding="utf-8"
    )
    assert ground_truth.count("expected_decision: CHECK_INCOMPLETE") == 2


def test_all_unified_diffs_apply_to_isolated_sample_bases(tmp_path: Path) -> None:
    """Every frozen .diff must actually apply, rather than merely parse as a diff."""
    for diff_path in sorted(DIFFS_DIR.glob("*.diff")):
        sandbox = tmp_path / diff_path.stem
        shutil.copytree(PROJECT_ROOT / "sample", sandbox / "sample")
        result = subprocess.run(
            ["patch", "--batch", "-p1", "-i", str(diff_path)],
            cwd=sandbox,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{diff_path}: {result.stdout}{result.stderr}"
