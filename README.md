# BizGuard：给 AI 编程助手加一道业务安全门禁

**BizGuard 是一个开源验证项目：它在 AI 编程助手改代码前，检查改动是否触犯了系统中容易被忽略的业务规则。**

它可以与 Claude Code、Codex 一类的编程助手配合：助手负责写代码，BizGuard 负责在关键规则被破坏时给出可追溯的拦截结论。它不是生产级安全产品，也不替代人工判断。

## 它解决什么问题？

AI 很会按需求改代码，却未必知道你的系统有哪些“不能碰”的规矩。更麻烦的是，这些规矩常常没有写在注释里：例如一次优惠券只能核销一次、账本状态必须一致、对外返回的数据字段不能随意删除。

举个例子：AI 修改优惠券核销逻辑时，为了“精简代码”删掉了幂等键检查。幂等键可以理解为“这次请求的唯一编号”；有它，重复点击或网络重试不会把同一张券核销两次。代码也许仍能编译、普通测试也可能通过，但用户重复提交后就可能发生重复核销。

传统的 LLM 代码审查更像是事后请另一位 AI 猜一猜“这里是否有风险”：有帮助，但结果是概率性的。BizGuard 则在变更进入下一步前，把明确的业务规则作为可执行的 Policy（策略），用语法树检查（AST，程序结构而非纯文本）和固定规则得出确定性结论：同一份输入可离线重放，结论不会靠模型临场发挥。

## 为什么做这个项目？

复杂业务系统里藏着许多业务不变量——也就是“无论怎么改，始终必须成立”的约束，例如：

- 幂等：重复请求不能重复扣款、核销或发货；
- 账本一致性：交易状态与账本记录不能相互矛盾；
- DTO 兼容性：DTO 是服务之间传递的数据结构，对外字段不能悄悄破坏调用方。

这些知识往往散落在历史事故、接口契约、团队文档和多个服务中。现有 CodeRabbit 等方案主要依赖事后 LLM review，适合发现线索，却不能保证每次都识别出隐藏的不变量。

BizGuard 的做法是把重要不变量整理成 Policy，以 AST 校验、影响分析和证据链支撑决策。它遵循三条底线：

- **确定性**：结论可离线重放；
- **证据链**：每条 `BLOCK` 都能追溯到规则、变更和相关证据；
- **未知不装作安全**：信息不完整时返回 `CHECK_INCOMPLETE` 或 `REQUIRE_APPROVAL`，而不是武断地 `ALLOW`。

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

脚本会演示一个“原生 Coding Agent 对照组”（离线、确定性的 scripted 模拟基线）认为改动看似合理而放行；随后 BizGuard 对同一份 diff 做检查并返回 `BLOCK`。这不是对真实 Claude Code 或 Codex 能力的测量；只有 benchmark 的 `--live` 模式并配置真实 Agent 命令时，才会运行真实 Agent。

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

# 运行当前工作区的全部测试（当前可收集 259 个）
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
5. `validate_patch`：确定性校验 unified diff；
6. `get_required_tests`：按 Policy 找出应运行的测试；
7. `request_approval`：目前仅提供审批 schema，不会创建审批记录；
8. `get_change_decision`：返回四态聚合决策、证据、测试与审批人。

### 安装闭环验证

```bash
./scripts/verify_install.sh --offline
```

该脚本会检查本地诊断和 CI 慢速复检，默认使用跨服务 DTO 变更 fixture。

## 诚实声明与限制

- BizGuard 是**开源验证项目**，不是已经验证可用于生产拦截的系统。
- Java 支持仅覆盖三个脱敏 fixture 仓库，不是完整的 Java 生态分析器。
- 离线 benchmark 的 Agent 轨道是 scripted/启发式基线；传入 `--live` 并配置真实 Agent 命令才会跑真实 Agent。
- 检索的目标 embedding 模型是智谱 `embedding-3`；离线时会降级为本地词法检索，并明确标识降级。该结果适合开发和演示，不能视作与真实 embedding 等价的生产验收。
- 受 Policy 保护的 diff 会在内存中应用到当前 fixture 基座后再做 AST 校验；工作区不会被修改。若 diff 无法应用或规则未覆盖，系统不会猜测安全。

## 参与和反馈

欢迎阅读 [贡献指南](CONTRIBUTING.md)，并通过 issue 或 PR 讨论新的业务不变量、fixture 与可复现案例。
