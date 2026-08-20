"""FastMCP protocol adapter that delegates all decisions to the shared core."""

from mcp.server.fastmcp import FastMCP

from bizguard.decision import ChangeSafetyCard, evaluate_change


mcp = FastMCP("bizguard")


@mcp.tool(
    description=(
        "只读分析 unified diff 的变更风险；不写入文件、不调用外部服务，且没有副作用。"
    )
)
def validate_patch(diff_text: str) -> ChangeSafetyCard:
    """Validate a patch through the shared, deterministic decision pipeline."""
    return evaluate_change(diff_text)


@mcp.tool(
    description=(
        "只读分析 unified diff 的变更准备状态；不写入文件、不调用外部服务，且没有副作用。"
    )
)
def prepare_change(diff_text: str) -> ChangeSafetyCard:
    """Prepare a change through the shared, deterministic decision pipeline."""
    return evaluate_change(diff_text)


if __name__ == "__main__":
    mcp.run()
