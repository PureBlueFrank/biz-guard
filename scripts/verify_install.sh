#!/usr/bin/env sh
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

offline=false
fixture="bench/fixtures/phase5/dynamic-mapper.diff"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --offline) offline=true ;;
    --fixture)
      [ "$#" -ge 2 ] || { echo "--fixture requires a path" >&2; exit 2; }
      fixture=$2
      shift
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ "$offline" = true ] || { echo "verify_install.sh requires --offline" >&2; exit 2; }

case "$fixture" in
  /*) ;;
  *) fixture="$PROJECT_ROOT/$fixture" ;;
esac
[ -f "$fixture" ] || { echo "fixture does not exist: $fixture" >&2; exit 2; }

cd "$PROJECT_ROOT"

doctor_output=$("$PYTHON_BIN" -m bizguard.cli doctor --json)
"$PYTHON_BIN" - "$doctor_output" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("ok") is not True:
    raise SystemExit(f"doctor failed: {payload}")
PY
printf '%s\n' "$doctor_output"

"$PYTHON_BIN" - "$fixture" <<'PY'
import asyncio
import json
from pathlib import Path
import sys

from agents_mcp.server import mcp

DYNAMIC_SYMBOL = (
    "repo://coupon-core/src/main/java/com/bizguard/coupon/persistence/"
    "DynamicCouponMapper.java#DynamicCouponMapper.map()"
)


def structured(result: object) -> dict[str, object]:
    if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[1], dict):
        raise RuntimeError("FastMCP call did not return structured output")
    return result[1]


async def verify() -> dict[str, object]:
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    required = {"prepare_change", "validate_patch", "analyze_impact", "get_change_decision"}
    if len(tools) != 8 or not required.issubset(names):
        raise RuntimeError(f"unexpected MCP tool surface: {sorted(names)}")
    if any(not tool.inputSchema.get("properties") for tool in tools):
        raise RuntimeError("an MCP tool has an empty input schema")

    prepared = structured(
        await mcp.call_tool(
            "prepare_change",
            {
                "task": "inspect dynamic coupon mapper boundary",
                "repos": ["coupon-core"],
                "base_revisions": {
                    "coupon-core": "fixture-coupon-core-base",
                    "__index__": "phase3-fixture-v1",
                },
            },
        )
    )
    if prepared.get("task") != "inspect dynamic coupon mapper boundary":
        raise RuntimeError("prepare_change did not compile the requested task")

    diff_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    fast = structured(await mcp.call_tool("validate_patch", {"diff_text": diff_text}))
    aggregate = structured(await mcp.call_tool("get_change_decision", {"diff_text": diff_text}))
    impact = structured(
        await mcp.call_tool(
            "analyze_impact",
            {"changed_symbol": DYNAMIC_SYMBOL, "revision": "phase3-fixture-v1"},
        )
    )
    if aggregate.get("decision") != "REQUIRE_APPROVAL":
        raise RuntimeError(f"FastMCP aggregate decision mismatch: {aggregate.get('decision')}")
    if impact.get("unknown_reason") != "DYNAMIC_BOUNDARY":
        raise RuntimeError(f"FastMCP impact reason mismatch: {impact.get('unknown_reason')}")
    if impact.get("required_approvers") != ["coupon_platform"]:
        raise RuntimeError(f"FastMCP impact owner mismatch: {impact.get('required_approvers')}")
    return {
        "fast_decision": fast.get("decision"),
        "mcp_ok": True,
        "prepared_context": prepared.get("change_context_id"),
        "tool_count": len(tools),
        "unknown_reason": impact.get("unknown_reason"),
    }


print(json.dumps(asyncio.run(verify()), sort_keys=True))
PY

set +e
ci_output=$("$PYTHON_BIN" -m bizguard.ci.check \
  --diff "$fixture" \
  --base-revisions "$PROJECT_ROOT/bench/fixtures/phase3-revisions.yaml" \
  --json)
ci_code=$?
set -e
if [ "$ci_code" -ne 1 ]; then
  echo "CI gate returned exit code $ci_code (expected 1 for unapproved REQUIRE_APPROVAL)" >&2
  exit 1
fi
"$PYTHON_BIN" - "$ci_output" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
expected = (
    "impact:DYNAMIC_BOUNDARY:repo://coupon-core/src/main/java/com/bizguard/coupon/"
    "persistence/DynamicCouponMapper.java#DynamicCouponMapper.map()"
)
if payload.get("decision") != "REQUIRE_APPROVAL":
    raise SystemExit(f"CI decision mismatch: {payload.get('decision')}")
if payload.get("evidence") != [expected]:
    raise SystemExit(f"CI dynamic-boundary evidence mismatch: {payload.get('evidence')}")
if payload.get("required_approvers") != ["coupon_platform"]:
    raise SystemExit(f"CI approver mismatch: {payload.get('required_approvers')}")
if payload.get("audit_event_id") != "ci-recomputed":
    raise SystemExit(f"CI audit event mismatch: {payload.get('audit_event_id')}")
required_tests = payload.get("required_tests")
if not isinstance(required_tests, list) or not required_tests:
    raise SystemExit("CI result has no required tests")
PY
printf '%s\n' "$ci_output"
