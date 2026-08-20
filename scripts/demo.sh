#!/usr/bin/env sh
# Reproducible, offline BizGuard milestone 6 demo.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${PYTHON:-python3}

if [ -f "$PROJECT_ROOT/../.venv/bin/activate" ]; then
    . "$PROJECT_ROOT/../.venv/bin/activate"
    PYTHON_BIN=python
fi

export PYTHONHASHSEED=0
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

run_check() {
    "$PYTHON_BIN" -m bizguard.cli check --diff "$1"
}

assert_decision() {
    payload=$1
    wanted_decision=$2
    "$PYTHON_BIN" - "$payload" "$wanted_decision" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
wanted = sys.argv[2]
actual = payload.get("decision")
if actual != wanted:
    raise SystemExit(f"expected decision {wanted}, got {actual}")
PY
}

expect_check() {
    wanted_exit=$1
    wanted_decision=$2
    diff_path=$3
    if output=$(run_check "$diff_path"); then
        actual=0
    else
        actual=$?
    fi
    if [ "$actual" -ne "$wanted_exit" ]; then
        printf '%s\n' "演示失败：期望退出码 $wanted_exit，实际为 $actual" >&2
        return 1
    fi
    assert_decision "$output" "$wanted_decision"
    printf '%s\n' "$output"
}

printf '%s\n' 'BizGuard 六场景确定性演示（固定 seed，离线可重放）'

printf '\n%s\n' '场景 1：启发式对照组（非真实 Agent）'
control_output=$("$PYTHON_BIN" - "$PROJECT_ROOT" <<'PY'
import json
from pathlib import Path
import sys

from bizguard.ci.check import evaluate
from scripts.run_benchmark import _naive_baseline

root = Path(sys.argv[1])
fixture = root / "bench/fixtures/phase5/cross-service-dto-breaking.diff"
diff_text = fixture.read_text(encoding="utf-8")
baseline = _naive_baseline(diff_text)
bizguard = evaluate(diff_text, {"revision": "phase3-fixture-v1"})["decision"]
if baseline != "allow" or bizguard != "REQUIRE_APPROVAL":
    raise SystemExit(
        f"control changed: expected heuristic allow/BizGuard REQUIRE_APPROVAL, got {baseline}/{bizguard}"
    )
print(
    json.dumps(
        {
            "baseline": "Naive Baseline (heuristic)",
            "bizguard_decision": bizguard,
            "decision": baseline.upper(),
            "fixture": fixture.name,
        },
        sort_keys=True,
    )
)
PY
)
printf '%s\n' "$control_output"

printf '\n%s\n' '场景 2：业务规则违规（预期 BLOCK，退出码 1）'
expect_check 1 BLOCK "$PROJECT_ROOT/sample/diffs/diff_violation_1.diff"

printf '\n%s\n' '场景 3：低风险正常变更（预期 ALLOW，退出码 0）'
expect_check 0 ALLOW "$PROJECT_ROOT/sample/diffs/diff_normal_1.diff"

printf '\n%s\n' '场景 4：非法输入（预期 CHECK_INCOMPLETE，退出码 2）'
missing_diff="$PROJECT_ROOT/sample/diffs/does-not-exist.diff"
[ ! -e "$missing_diff" ] || { printf '%s\n' "演示夹具意外存在：$missing_diff" >&2; exit 1; }
expect_check 2 CHECK_INCOMPLETE "$missing_diff"

printf '\n%s\n' '场景 5：动态映射未知边界（预期 REQUIRE_APPROVAL）'
dynamic_output=$(cd "$PROJECT_ROOT" && "$PYTHON_BIN" -m bizguard.ci.check \
    --diff bench/fixtures/phase5/dynamic-mapper.diff \
    --base-revisions bench/fixtures/phase3-revisions.yaml \
    --json)
"$PYTHON_BIN" - "$dynamic_output" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
expected = (
    "impact:DYNAMIC_BOUNDARY:repo://coupon-core/src/main/java/com/bizguard/coupon/"
    "persistence/DynamicCouponMapper.java#DynamicCouponMapper.map()"
)
if payload.get("decision") != "REQUIRE_APPROVAL":
    raise SystemExit(f"dynamic boundary was not routed to approval: {payload.get('decision')}")
if payload.get("evidence") != [expected]:
    raise SystemExit(f"dynamic boundary evidence mismatch: {payload.get('evidence')}")
if payload.get("required_approvers") != ["coupon_platform"]:
    raise SystemExit(f"dynamic boundary owner mismatch: {payload.get('required_approvers')}")
PY
printf '%s\n' "$dynamic_output"

printf '\n%s\n' '场景 6：跨服务影响路径'
impact_output=$(cd "$PROJECT_ROOT" && "$PYTHON_BIN" -m bizguard.impact analyze \
    --diff bench/fixtures/phase3/mq-status.diff \
    --repos fixtures/java-microservices \
    --revision-set bench/fixtures/phase3-revisions.yaml)
"$PYTHON_BIN" - "$impact_output" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
path = payload.get("path")
if not isinstance(path, list):
    raise SystemExit("cross-service impact has no path")
if not any("merchant-service" in str(node) for node in path):
    raise SystemExit(f"cross-service path lacks merchant-service: {path}")
if not any("coupon-core" in str(node) for node in path):
    raise SystemExit(f"cross-service path lacks coupon-core: {path}")
if not any(str(node).startswith("mq://") for node in path):
    raise SystemExit(f"cross-service path lacks messaging boundary: {path}")
if payload.get("unknown_boundary") is not False:
    raise SystemExit(f"cross-service path unexpectedly unknown: {payload.get('unknown_boundary')}")
PY
printf '%s\n' "$impact_output"
