# BizGuard：给 AI 编程助手加一道业务安全门禁

**BizGuard 是面向复杂 Java 业务系统的开源验证项目：它为 Claude Code、Codex、Cursor 等 Coding Agent 补充业务上下文、跨服务影响证据和确定性变更门禁。**

现有 Agent 继续负责代码搜索、生成、编辑和测试，BizGuard 专注回答“改之前必须知道什么、这次变更可能影响谁、满足什么条件才允许继续”。它不是生产级安全产品，也不替代代码评审和领域专家判断。

## 项目背景

Claude Code、Codex、Cursor 等 Coding Agent 已经能够完成多文件修改、命令执行、测试修复和 Diff 审查。但在优惠券、交易、履约、供应链等复杂业务系统中，**代码只是业务语义的不完整投影：代码改得出来，不等于业务改得安全。**

一个看似冗余的判断，可能承担旧链路兼容职责；一个当前仓库里没有引用的字段，可能仍通过 DTO、RPC、MQ 或离线任务被下游消费；一次状态更新的顺序，也可能承载幂等、一致性或资损防护语义。以优惠券核销为例，Agent 为了“精简代码”删除幂等检查后，代码仍可能编译并通过普通测试，却会在重复点击或网络重试时造成重复核销。

真正决定“这段代码能不能改”的知识，往往分散在接口契约、技术方案、故障复盘、运行时链路和团队经验中。通用 Agent 能读懂当前代码，却无法凭空知道未被记录的团队知识，也难以仅靠单仓库的局部信息，稳定回答三个问题：

1. 修改前必须遵守哪些业务不变量和历史约束？
2. 这次变更会影响哪些字段、接口、服务和下游调用方？
3. 需要补充哪些检查、测试或人工审批，变更才可以继续？

项目指令、RAG 和 LLM Review 都能提供线索，但还不足以单独成为业务安全边界：知识可能过期，Prompt 可能被弱化，跨服务影响可能没有查全，概率性判断也难以作为可重复、可审计的强制门禁。尤其当证据不足时，“没有发现风险”不应被等同于“已经证明安全”。

BizGuard 因此被设计为一层独立于模型和 Agent 品牌的**业务感知变更安全控制面**。它不重新实现通用 Coding Agent，而是将分散的业务知识转化为有来源的上下文、可执行的 Policy 和可追溯的影响证据；能确定性检查的规则交给 AST 与固定校验器，证据不足的边界则明确标记为未知并转人工，最终收敛为 `ALLOW`、`ALLOW_WITH_TESTS`、`REQUIRE_APPROVAL` 或 `BLOCK`。

BizGuard 的目标不是让 Agent 生成更多代码，而是让 Agent 面对企业隐性业务规则时少猜测、尽早暴露关键影响与未知边界，并让每一次放行、阻断和人工接管都有证据可查。

## 技术架构

项目按 P0 到 P5 逐步构建；这里的“P”是阶段编号，不是要求你按顺序手工操作。

```mermaid
flowchart LR
    P0[P0：3 个 Java 脱敏 fixture 仓库\n语义 catalog] --> P1[P1：领域契约\n黄金基准]
    P1 --> P2[P2：知识 Hub\n混合检索]
    P2 --> P3[P3：跨服务影响图谱\n8 类节点 · 真 BFS]
    P3 --> P4[P4：Context Compiler\n8 个 MCP Tool]
    P4 --> P5[P5：四态决策 · 审批 · CI\n5 组消融]
    P5 --> D[带证据的安全结论]
```

- **P0**：提供 `coupon-core`、`coupon-contract`、`merchant-service` 三个脱敏 Java fixture 仓库，以及描述业务能力、规则和责任人的语义 catalog。
- **P1**：把领域契约固定成可验证的黄金基准，防止规则随实现漂移。
- **P2**：知识 Hub 汇集受治理的团队知识；混合检索结合语义向量与关键词结果。冻结评测集的 `Recall@5=1.0`，仅表示该固定小集合中前 5 条结果覆盖了目标，不代表生产环境的通用召回率。
- **P3**：建立跨服务影响图谱，包含组织、部署、代码、接口、数据、消息、运行时、业务共 **8 类节点**；使用真正的 BFS（广度优先搜索）找最短影响路径，并随路径返回证据。动态边界无法确认时显式标为未知。
- **P4**：Context Compiler 将任务、仓库、基线版本、规则、影响和必测项编译为只读上下文包；通过 **8 个 MCP Tool** 供 Agent 调用。
- **P5**：聚合为四态决策，接入审批工作流与 CI 复检，并提供 **5 组**可离线重放的消融对照：Naive Baseline、Rules Only、RAG Only、Context、Full。

四态的含义很直白：`ALLOW`（可继续）、`ALLOW_WITH_TESTS`（补齐指定测试后可继续）、`REQUIRE_APPROVAL`（需要人确认）和 `BLOCK`（发现关键违规，阻断）。旧检查管线出现无法检查的情况会明确给出 `CHECK_INCOMPLETE`，再映射到“不自动放行”的结果。

## 项目结构

```text
biz-guard/
├── src/bizguard/        # 核心：规则、决策、图谱、检索、CLI 与 CI
├── agents_mcp/          # MCP 协议适配层，供 AI 编程助手调用
├── fixtures/            # 三个脱敏 Java 微服务 fixture 与辅助编译脚本
├── sample/              # Python 示例代码与可复现的 diff
├── policy/              # 业务不变量与策略注册表
├── registry/            # 领域契约登记数据
├── knowledge/           # 已发布知识、ADR 与检索素材
├── bench/               # 黄金基准、决策 fixture、五组消融任务
├── tests/               # 自动化测试
├── scripts/             # Demo、安装验证和 benchmark 脚本
└── docs/                # 架构决策记录
```

## Demo：同一改动，两种结果

在项目根目录运行：

```bash
./scripts/demo.sh
```

脚本会依次实际运行 6 个可自校验场景：启发式对照组、违规 `BLOCK`、正常
`ALLOW`、非法输入 `CHECK_INCOMPLETE`、动态边界 `REQUIRE_APPROVAL`，以及跨服务影响路径。
其中对照组是离线启发式，不代表真实 Claude Code 或 Codex；只有 benchmark 的 `--live`
模式并配置 Agent 命令时，才会运行真实 Agent。

也可以直接查看违规样例：

```bash
bizguard check --diff sample/diffs/diff_violation_1.diff
```

该 diff 删除了 `IdempotencyStore.check(idempotency_key)`。BizGuard 会输出 `BLOCK`，并把“被删掉的幂等检查”作为 finding/evidence 返回；因此你可以追溯“为什么被拦截”，而不只是得到一个黑盒的“不通过”。

## 快速启动

### 环境要求

- Python 3.12+
- Java 17（用于 Java fixture 的编译/校验）

```bash
git clone https://github.com/PureBlueFrank/biz-guard.git
cd biz-guard

# 常规安装
pip install -e .

# 运行当前工作区的全部测试
pytest
```

离线环境请先在虚拟环境或内部包源准备好构建依赖 `hatchling`，然后使用：

```bash
pip install --no-build-isolation -e .
```

`pip install -e .` 默认会创建隔离构建环境，离线时可能尝试下载 `hatchling`。

### 常用 CLI

以下命令假设当前目录是项目根目录。`prepare` 需要给出任务、涉及仓库和基线版本；`impact` 根据真实 fixture 图谱给出路径与证据。

```bash
# 编译 Agent 可读的上下文包
bizguard prepare --task "检查优惠券状态字段变更" \
  --repos coupon-core coupon-contract \
  --base-revisions bench/fixtures/phase3-revisions.yaml --json

# 检查 unified diff 是否违反 Policy
bizguard check --diff sample/diffs/diff_violation_1.diff

# 将 BizGuard MCP 注册到 Codex 官方配置（先预览，再执行）
bizguard connect codex --repository . --dry-run
bizguard connect codex --repository . \
  --identity coupon_platform --roles engineering

# 分析跨服务影响
bizguard impact analyze \
  --diff bench/fixtures/phase3/dto-status.diff \
  --repos fixtures/java-microservices \
  --revision-set bench/fixtures/phase3-revisions.yaml --format json

# 搜索受治理的团队知识
bizguard knowledge search --query "优惠券核销必须使用幂等键" \
  --scope coupon_redemption --revision semantic-seed-v1 \
  --roles engineering --json
```

### 8 个 MCP Tool

MCP（Model Context Protocol）是让 AI 助手调用外部能力的标准接口。BizGuard 提供以下 8 个工具：

1. `prepare_change`：编译只读 Context Pack；
2. `search_team_knowledge`：检索有权限的团队知识；
3. `explain_symbol`：解释已索引符号及其图谱证据；
4. `analyze_impact`：分析影响路径、未知边界和必测项；
5. `validate_patch`：兼容旧客户端的确定性 unified diff 校验；
6. `get_required_tests`：按 Policy 找出应运行的测试；
7. `request_approval`：写入或推进审批请求；审批绑定决策指纹，执行人取自服务端认证身份；
8. `get_change_decision`：统一返回四态聚合决策、证据、必测项和审批状态；MCP 调用方不能自行宣称测试已通过。

### 安装闭环验证

```bash
./scripts/verify_install.sh --offline
```

该脚本会检查 8 个 MCP 工具的真实调用、本地诊断和 CI 慢速复检，默认使用
`bench/fixtures/phase5/dynamic-mapper.diff` 验证动态边界、证据与审批责任人。

### 真实 Codex benchmark 轨道

先选择当前 Codex 账号可用的模型，再让 `run_benchmark.py --live` 调用只读的 Codex CLI
适配器：

```bash
export BIZGUARD_CODEX_MODEL="<已启用的 Codex 模型>"
BIZGUARD_LIVE_AGENT_COMMAND="python scripts/codex_agent.py" \
BIZGUARD_LIVE_TASK_ID="critical-ledger-1" \
python scripts/run_benchmark.py \
  --dataset bench/ablations/tasks.yaml \
  --live \
  --out bench/ablations/live_results.json \
  --transcript-out bench/ablations/codex_agent_transcript.json
```

适配器通过 stdio MCP 只暴露统一主链的 `get_change_decision`，要求 Codex 原样提交冻结 diff，并解析
`codex exec --json` 的真实事件。benchmark 会用当前 FastMCP schema 再执行同一调用；只有
输入、完整输出和最终决策全部一致时才写出 transcript。

## 诚实声明与限制

- BizGuard 是**开源验证项目**，不是已经验证可用于生产拦截的系统。
- Java 支持仅覆盖三个脱敏 fixture 仓库，不是完整的 Java 生态分析器。
- 离线 benchmark 的 Agent 轨道是 scripted/启发式基线；传入 `--live` 并配置真实 Agent 命令才会跑真实 Agent。
- 检索的目标 embedding 模型是智谱 `embedding-3`；离线时会降级为本地词法检索，并明确标识降级。该结果适合开发和演示，不能视作与真实 embedding 等价的生产验收。
- 受 Policy 保护的 diff 会在内存中应用到当前 fixture 基座后再做 AST 校验；工作区不会被修改。若 diff 无法应用或规则未覆盖，系统不会猜测安全。

## 参与和反馈

欢迎阅读 [贡献指南](CONTRIBUTING.md)，并通过 issue 或 PR 讨论新的业务不变量、fixture 与可复现案例。
