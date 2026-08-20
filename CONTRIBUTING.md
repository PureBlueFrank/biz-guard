# 贡献规范

BizGuard 是业务感知的变更安全控制面，不是通用 Coding Agent，也不以 LLM 判断替代
Policy、影响图谱、CI 或人工审批。贡献必须保持结论可重放、证据可追溯，并在信息不足时
保守处理。

## 项目边界与目录职责

- `src/bizguard/`：核心领域模型、Context Compiler、影响分析、Policy、决策与 CI 逻辑。
- `agents_mcp/`：MCP 协议适配层；只调用共享核心服务，不复制 Policy 或决策逻辑。
- `policy/`：机器可读业务不变量；`registry/`：字段与跨服务契约。
- `knowledge/`：有版本和权限边界的解释性知识，不作为硬规则的唯一事实源。
- `bench/`、`fixtures/`、`sample/`：冻结基准、脱敏仓库和可重放 diff。
- `tests/`：确定性自动化测试；`scripts/`：演示、安装验证和 benchmark 入口。

只实现当前问题所需的最小改动。不要在 MCP、CLI、CI 中分别实现同一判断，也不要把
fixture 文件名、预期结论或 benchmark truth 当成运行时决策依据。

## 决策契约

聚合决策只有四态，优先级由共享决策引擎确定：

1. `BLOCK`：关键 Policy 已被确定违反；
2. `REQUIRE_APPROVAL`：关键边界、版本或责任归属未知，或公共契约需要人工确认；
3. `ALLOW_WITH_TESTS`：规则未被违反，但缺少明确要求的测试证据；
4. `ALLOW`：硬条件、版本和测试证据均满足。

旧版校验适配器仍可能返回 `CHECK_INCOMPLETE`。它只表示“无法完成检查”，必须保守映射
为不自动放行，不能当成第五种聚合决策，更不能降级成 `ALLOW`。

## Context Compiler

Context Pack 按 `Mandatory`、`Structural`、`Rationale`、`Expandable` 四层编译，并绑定任务、
仓库基线、图谱摘要、知识版本和调用者身份。预算收缩从 `Expandable` 和 `Rationale` 开始，
随后只移除证据与结构的冗长副本；Mandatory Policy 及其 evidence ID 永不裁剪。若这些
Mandatory 内容本身超过 800、1200、2000 或 4000 的所选预算，编译器会显式抛出
`ValueError`，不会悄悄丢规则，也不会伪造“预算内”结果。

修改编译、检索、图谱或 token 估算时，必须更新相应冻结摘要，并证明 mandatory recall
保持为 `1.0`。

## MCP 工具面

`agents_mcp.server` 暴露 8 个工具：

1. `prepare_change`：编译只读 Context Pack；
2. `search_team_knowledge`：按 ACL、scope 和版本检索知识；
3. `explain_symbol`：返回符号及图谱证据；
4. `analyze_impact`：返回影响路径、未知边界和必测项；
5. `validate_patch`：确定性校验 unified diff；
6. `get_required_tests`：从语义 catalog 选择必测项；
7. `request_approval`：当前仅暴露 schema，并明确拒绝创建审批记录；
8. `get_change_decision`：返回四态聚合决策及证据。

读工具不得写工作区或调用外部服务。新增或修改工具时，应同时测试输入 schema、真实
FastMCP 调用以及与 CLI/核心服务的结果一致性。

## 影响图谱与 CI 重检

影响分析必须基于索引快照和真实最短路径返回证据。动态调用、反射或无法解析的跨服务
边界应标记 `unknown_boundary`，给出原因与责任人，并进入 `REQUIRE_APPROVAL`。

Agent 输出和缓存不是 CI 的信任根。CI 必须从提交 diff、仓库基线与版本锁重新构建检查
输入并执行共享决策逻辑；任何解析失败、版本漂移或关键证据缺失都不得自动放行。

## 代码与文档约定

- Python 3.12+，4 空格缩进，行宽 100；模块、函数和变量使用 `snake_case`，类使用
  `PascalCase`，机器可读 ID 使用小写 `kebab-case`。
- 公开类和函数必须有类型注解及简短 docstring。
- 测试必须离线、确定性且可重放；模拟只用于隔离边界，不能冒充真实 Agent 轨道。
- 修改 fixture 后应运行对应 verifier；修改 demo 后应执行完整脚本，而不只断言输出文字。

## 提交前验证

在仓库根目录执行：

```bash
source ../.venv/bin/activate
python -m ruff check src tests agents_mcp
python -m ruff check --select D101,D103 src/bizguard agents_mcp
python -m mypy src tests agents_mcp
python -m pytest -q
./scripts/demo.sh
./scripts/verify_install.sh --offline
```

`ruff`、`mypy --strict` 和 `pytest` 都必须通过。若改动涉及真实 Agent benchmark，还需保存
真实 CLI/MCP 事件，并确认 transcript 中的完整 diff 输入与工具输出能通过当前 FastMCP
schema 和实现重放；scripted 或 mock 轨道不得标记为 `live`。
