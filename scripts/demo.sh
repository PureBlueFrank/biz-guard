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
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

run_check() {
    "$PYTHON_BIN" -m bizguard.cli check --diff "$1"
}

expect_exit() {
    expected=$1
    diff_path=$2
    if output=$(run_check "$diff_path"); then
        actual=0
    else
        actual=$?
    fi
    if [ "$actual" -ne "$expected" ]; then
        printf '%s\n' "演示失败：期望退出码 $expected，实际为 $actual" >&2
        return 1
    fi
    printf '%s\n' "$output"
}

printf '%s\n' 'BizGuard 对照组与确定性检查演示（固定 seed，离线可重放）'
printf '\n%s\n' '步骤 1：违规 diff 的原生 Coding Agent 对照组'
printf '%s\n' '原生 Coding Agent（模拟，确定性）：代码可编译、改动看似合理，放行。'
printf '%s\n' '对照组结论：未使用 BizGuard 时，该业务约束违规会被漏掉。'
printf '%s\n' 'BizGuard：同一 diff 的确定性检查（预期 BLOCK，退出码 1）'
expect_exit 1 "$PROJECT_ROOT/sample/diffs/diff_violation_1.diff"

printf '\n%s\n' '步骤 2：正常 diff（预期 ALLOW，退出码 0）'
expect_exit 0 "$PROJECT_ROOT/sample/diffs/diff_normal_1.diff"

printf '\n%s\n' '步骤 3：未覆盖 Policy 的 diff（预期 CHECK_INCOMPLETE + fault，退出码 4）'
expect_exit 4 "$PROJECT_ROOT/sample/diffs/diff_incomplete_policy_uncovered.diff"
