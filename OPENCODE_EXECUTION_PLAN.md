# BizGuard 工程闭环与生产化收尾执行计划

> 执行对象：OpenCode 或其他编码 Agent  
> 执行方式：严格按步骤顺序实施，每步先补失败测试，再做最小实现，最后通过本步验收门。  
> 目标：将 BizGuard 从“fixture 级可演示原型”收尾为“功能闭环的开源 MVP”，但不虚假宣称已获得生产 Blocking 资格。

---

## 1. 执行规则

### 1.1 开始前必须执行

```bash
git status --short
git branch --show-current
python --version
python -m pytest -q
python -m ruff check src tests agents_mcp scripts
python -m ruff check --select D101,D103 src/bizguard agents_mcp
python -m mypy src tests agents_mcp
```

若当前环境尚未安装项目，且仓库内不存在需保留的 `.venv`，先执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

依赖安装需要访问网络而当前环境未授权时，停止并请求授权，不得换用不符合 Python 版本或依赖契约的其他解释器伪造通过。

基线成功标准：

- Python 版本为 3.12 或更高。
- 当前基线应为 `277 passed, 1 skipped`；若测试数因之前的合法提交而变化，记录实际值。
- Ruff 和 MyPy strict 全部退出码为 0。
- 若工作区已有与本计划无关的用户改动，保留并绕开，不得重置、覆盖或顺手格式化。

### 1.2 全程禁止事项

- 不得修改 Golden 或 benchmark truth 只为让实现通过。
- 不得根据 fixture 文件名、task ID 或预期结论写运行时分支。
- 不得在 CLI、MCP、Hook 和 CI 中复制 Policy 或决策逻辑。
- 不得将解析失败、版本未知、关键证据缺失降级为 `ALLOW`。
- 不得把 mock、scripted 或 recorded 轨迹标记为 `live`。
- 不得在日志或 transcript 中保存密钥、完整 Agent 对话、未脱敏文档或生产数据。

### 1.3 每步的固定执行模式

1. 阅读本步指定的现有文件和测试。
2. 先新增能复现缺口的失败测试。
3. 确认失败原因是待实现能力，不是测试本身错误。
4. 实现最小改动，不扩展无关功能。
5. 运行本步局部验收。
6. 运行全量 `pytest + ruff + mypy`。
7. 查看 `git diff --check` 和 `git status --short`。
8. 只有验收全绿才可进入下一步。

若同一失败连续三次没有新证据，停止机械尝试，记录已排除项、最小复现和所需用户决策。

---

## 2. 目标架构和唯一信任链

收尾后只允许一条权威链路：

```text
Git/Agent Diff
    ↓
Unified Diff Parser（多文件）
    ↓
ChangeEvaluator（唯一应用服务）
    ├─ Policy validators
    ├─ repository/revision-pinned impact graph
    ├─ required tests
    ├─ knowledge/context evidence
    └─ approval state
    ↓
Canonical ChangeDecision
    ↓
CLI / MCP / Hook / CI 薄适配器
```

CLI、MCP、Hook 和 CI 可以因显示需求增加外层字段，但以下核心字段必须一致：

- `decision`
- `rationale`
- `findings`
- `required_tests`
- `required_approvers`
- `evidence`
- `risk_score`
- `change_context_id`
- `policy_revision`
- `base_revisions_sha256`

---

## 3. 分步执行计划

## 步骤 0：冻结现状和收尾契约

### 目标

将本计划中需要修复的真实缺口固化为测试名称和完成清单，避免后续通过改文档口径宣布完成。

### 操作

- 在 `tests/` 中预留以下行为契约的测试文件，每个文件随对应步骤实现，不一次性提交全部红测试：
  - `tests/test_diff_multifile.py`
  - `tests/test_entrypoint_parity.py`
  - `tests/test_ci_gate.py`
  - `tests/test_approval_persistence.py`
  - `tests/test_mcp_resources_live.py`
  - `tests/test_agent_connectors.py`
  - `tests/test_observability_integration.py`
  - `tests/test_benchmark_metrics.py`
- 在本文档最后的完成清单中维护执行状态，不删除未完成项。
- 不修改当前 Golden 预期结论。

### 验证

```bash
python -m pytest -q
git diff --check
```

### 完成标准

- 现有基线继续全绿。
- 没有为未实现能力提交占位 `pass` 或无断言测试。

---

## 步骤 1：实现正确的多文件 Unified Diff 模型

### 目标

替换 CI 中“从文本找第一个 `+++ b/`”的处理方式，为后续统一评估链提供稳定输入。

### 修改范围

- `src/bizguard/diff_parser.py`
- 必要时新增 `src/bizguard/change/diff_models.py`
- `tests/test_diff_multifile.py`

### 实现要求

- 返回文件级结构：old path、new path、change type、hunks、added/removed lines。
- 支持 add、modify、delete、rename 和多文件 Diff。
- 二进制 Diff、截断 hunk、路径缺失或不能安全重建时返回明确 fault，不得当作无风险变更。
- 拒绝越过 repository root 的路径。
- 保留旧版单文件接口作为兼容 adapter，内部调用新 parser。

### 必测场景

- 两个 Java 文件同时变更。
- DTO 文件删字段，同时另一个文件是无关诱饵。
- add/delete/rename。
- malformed hunk、binary diff、`../` 路径。
- 文件顺序变化不改变聚合结论。

### 验证

```bash
python -m pytest -q tests/test_diff_multifile.py tests/test_decision.py tests/test_sample_fixtures.py
python -m ruff check src/bizguard/diff_parser.py src/bizguard/change tests/test_diff_multifile.py
python -m mypy src tests agents_mcp
python -m pytest -q
```

### 完成标准

- 所有文件都进入后续评估，不再只取第一个文件。
- 所有不完整输入明确进入保守决策。

---

## 步骤 2：建立唯一 `ChangeEvaluator` 和规范化决策 Schema

### 目标

消除 `evaluate_change`、`decide_diff` 和 `ci.check.evaluate` 三条实质不同的决策链。

### 修改范围

- 新增 `src/bizguard/change/evaluator.py`
- 新增或整理 `src/bizguard/change/models.py`
- 复用 `src/bizguard/decision/v2.py`
- 复用 Policy、Impact、Required Tests 和 Context 现有服务
- `tests/test_entrypoint_parity.py`

### 规范化输入

`EvaluationRequest` 至少包含：

- `diff_text`
- `repository_root`
- `base_revisions`
- `policy_revision`
- `principal`
- `tests_passed` 或结构化测试证据
- 可选 `change_context_id`

### 规范化输出

`ChangeDecision` 使用唯一 Pydantic schema，至少包含第 2 节列出的核心字段。`required_tests` 不得在一个入口中是 ID 列表，在另一个入口中又被覆盖成字典列表。

### 实现要求

- `ChangeEvaluator` 一次评估所有 changed files。
- Policy validator 根据 Policy Registry 和文件类型选择，不固定只运行 DTO Policy。
- ImpactService 必须使用 `repository_root` 和 base revision，不得默认指向 `fixtures/java-microservices`。
- 决策优先级保持：critical violation → unknown/version → missing tests → public/multi-owner → risk score。
- 旧三态 API 只保留兼容 adapter，`CHECK_INCOMPLETE` 映射为不自动放行。
- 核心服务不读取 CLI 参数、环境变量或 MCP 状态。

### 必测场景

- `BLOCK / ALLOW_WITH_TESTS / REQUIRE_APPROVAL / ALLOW` 四态各至少两个。
- 同一 Diff 调用核心服务两次的规范化输出一致。
- base revision 变化会改变 revision hash。
- 多文件中任意一个 critical 违规都导致整体 `BLOCK`。
- 未知边界保留 owner、required tests 和 evidence。

### 验证

```bash
python -m pytest -q tests/test_entrypoint_parity.py tests/test_risk_decision.py tests/test_ci_parity.py
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase5
python -m ruff check src tests agents_mcp
python -m mypy src tests agents_mcp
python -m pytest -q
```

### 完成标准

- 所有新代入口只依赖 `ChangeEvaluator`。
- 项目内不再存在第二套四态聚合逻辑。

---

## 步骤 3：将 CLI、MCP、Hook 和 CI 改为薄适配器

### 目标

对同一个 `EvaluationRequest`，四个入口的核心结果完全一致。

### 修改范围

- `src/bizguard/cli.py`
- `agents_mcp/server.py`
- `src/bizguard/hooks/agent.py`
- `src/bizguard/ci/check.py`
- `tests/test_entrypoint_parity.py`
- 更新 `bench/golden/phase4-mcp-io.yaml` 前必须证明是公开 schema 升级，不得改变原 Golden 决策。

### 实现要求

- CLI 和 CI 支持传入真实 repository root 和 base revisions。
- MCP 不再将主链固定到 fixture 目录；repository root 必须由安全配置或请求上下文提供。
- Hook 只收集 Agent 事件并构造请求，不进行额外决策。
- 为输出定义 `normalize_core_result()` 测试 helper，只去掉时间戳、随机 audit ID 等非确定性字段。
- 不允许为通过 parity 测试而忽略 required tests、approvers 或 evidence。

### 验证

```bash
python -m pytest -q tests/test_entrypoint_parity.py tests/test_mcp.py tests/test_mcp_read_tools.py tests/test_ci_parity.py tests/test_hook_doctor_resources.py
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase4
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase5
python -m pytest -q
```

### 完成标准

- 至少对 `cross-service-dto-breaking.diff`、`dynamic-mapper.diff`、一个 `BLOCK` 和一个 `ALLOW` fixture 进行四入口 parity 验证。
- 上述每个 fixture 的核心字段全部相等。

---

## 步骤 4：实现真正可执行的 PR/CI 门禁

### 目标

让 GitHub PR Check 真正重算当前 PR Diff，并在未满足放行条件时阻止合并。

### 修改范围

- `src/bizguard/ci/check.py`
- `.github/workflows/bizguard.yml`
- `tests/test_ci_gate.py`
- `scripts/verify_install.sh`

### 退出码契约

| 结果 | 退出码 | 含义 |
| --- | ---: | --- |
| `ALLOW` | 0 | 可自动放行 |
| `ALLOW_WITH_TESTS` 且证据完整 | 0 | 已满足测试要求 |
| `ALLOW_WITH_TESTS` 且证据缺失 | 1 | 需要先补测试 |
| `REQUIRE_APPROVAL` 且有有效完整审批 | 0 | 已完成指定人工审批 |
| `REQUIRE_APPROVAL` 且未审批 | 1 | 禁止自动合并 |
| `BLOCK` | 1 | 确定违规 |
| 输入/配置错误 | 2 | 门禁未完成，不得当作通过 |

### 实现要求

- Workflow 获取 PR base SHA 和 head SHA，生成完整 unified diff。
- 浅克隆下显式 fetch 评估所需的 base commit。
- CI 使用当前 checkout 作为 repository root，不使用演示 fixture。
- 为 fork PR 保持最小权限，不向不可信代码暴露 secret。
- 在 job summary 中输出决策、修复建议、必测项、审批人和证据 ID，不输出敏感全文。
- 保留 lint/type/test job，另增真正的 BizGuard gate step。

### 必测场景

- 四态及缺测试/缺审批的退出码。
- 多文件 Diff 中第二个文件违规时 job 失败。
- base revision 不存在或 Diff 无法生成时 job 不得通过。
- 子进程验证真实退出码，不只测试 Python 函数返回值。

### 验证

```bash
python -m pytest -q tests/test_ci_gate.py tests/test_ci_parity.py
python - <<'PY'
import subprocess
import sys

completed = subprocess.run(
    [
        sys.executable,
        "-m",
        "bizguard.ci.check",
        "--diff",
        "bench/fixtures/phase5/cross-service-dto-breaking.diff",
        "--base-revisions",
        "bench/fixtures/phase3-revisions.yaml",
        "--repository-root",
        "fixtures/java-microservices",
        "--json",
    ],
    check=False,
)
assert completed.returncode == 1, completed.returncode
PY
./scripts/verify_install.sh --offline
python -m pytest -q
```

> 注意：上述命令使用未审批的 `REQUIRE_APPROVAL` fixture，所以 CI 子进程应返回非 0。`verify_install.sh` 需显式将“预期的非 0 门禁结果”视为安装验证成功，不得通过强制改成 `ALLOW` 规避。

### 完成标准

- GitHub Actions 中真实出现安全门禁步骤。
- `BLOCK`、未完成测试和未完成审批不再返回成功状态。

---

## 步骤 5：持久化 HITL 审批并接通决策闭环

### 目标

将已有 ApprovalService 从内存测试对象升级为可恢复、可并发、可审计的本地持久化工作流。

### 修改范围

- 新增 `src/bizguard/workflow/store.py`
- 修改 `src/bizguard/workflow/approval.py`
- 必要时对 `src/bizguard/change/store.py` 做最小扩展
- 修改 `agents_mcp/server.py`
- `tests/test_approval_persistence.py`
- 保留 `tests/test_approval_workflow.py`

### 数据和幂等契约

- 幂等键保持 `(change_context_id, policy_revision, sorted approver_set)`。
- 审批记录至少持久化：state、approvers、required cosigns、approvals、delegates、waiver、evidence refs、created/updated time。
- 审计事件追加写，不原地覆盖历史。
- SQLite 作为开源 MVP 默认存储；业务层依赖 store protocol，不直接依赖全局连接。
- 状态更新使用 transaction，重试不能生成重复审批。

### MCP 闭环

- `request_approval` 改为显式写工具，Tool description 标明副作用。
- 只有客户端明确批准该写工具时才执行。
- 输入不再只有 reason；需包含 policy revision、approver set、required cosigns 和 evidence refs。
- `get_change_decision` 支持传入 `change_context_id`，读取测试证据、审批和豁免后重算。
- 过期豁免、不足会签、非法代理、审批服务不可用都不得自动 `ALLOW`。

### 验证

```bash
python -m pytest -q tests/test_approval_persistence.py tests/test_approval_workflow.py tests/test_risk_decision.py
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase5
python -m pytest -q
```

### 完成标准

- 关闭并重新打开 store 后，审批状态和审计链可恢复。
- 重复请求只有一个审批项。
- 审批前 CI 不放行，合法审批后同一 Change Context 可按 Policy 进入下一状态。

---

## 步骤 6：实现真正的 MCP Resources

### 目标

让 Agent 客户端能通过 FastMCP 发现和读取设计文档中的资源 URI，而不只是调用两个未注册函数。

### 修改范围

- `agents_mcp/resources.py`
- `agents_mcp/server.py`
- `tests/test_mcp_resources_live.py`
- `tests/test_mcp_read_tools.py`

### 必须注册的资源

- `bizguard://changes/{change_context_id}`
- `bizguard://symbols/{canonical_symbol_id}`
- `bizguard://capabilities/{business_capability_id}`
- `bizguard://policies/{policy_id}`
- `bizguard://evidence/{evidence_id}`

### 实现要求

- 使用 FastMCP 真实 Resource 注册机制。
- 资源只返回摘要、revision、freshness、confidence 和 evidence links，不倾倒整库文档或整张图。
- change/evidence 资源从持久化 store 读取，不伪造占位数据。
- 不存在和无权限资源返回明确错误，且不泄露资源是否存在的额外信息。

### 验证

```bash
python -m pytest -q tests/test_mcp_resources_live.py tests/test_mcp_read_tools.py tests/test_mcp.py
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase4
python -m pytest -q
```

### 完成标准

- 测试通过真实 FastMCP session 列出和读取资源。
- 不再只断言 Python helper 返回一个字典。

---

## 步骤 7：实现 Agent 连接器、Hook 和安装诊断

### 目标

将当前不可直接执行的通用 Hook manifest 改为可预览、可安装、可幂等、可验证的薄连接器。

### 修改范围

- `src/bizguard/hooks/install.py`
- `src/bizguard/hooks/agent.py`
- `src/bizguard/cli.py`
- 新增 `src/bizguard/connectors/`
- `tests/test_agent_connectors.py`
- `tests/test_hook_doctor_resources.py`
- `scripts/verify_install.sh`

### 命令契约

```text
bizguard init --repository ROOT --dry-run
bizguard connect claude-code --repository ROOT --dry-run
bizguard connect codex --repository ROOT --dry-run
bizguard doctor --repository ROOT --json
bizguard verify-install --repository ROOT --offline
```

### 实现要求

- `init` 只检测语言、构建工具、契约、CODEOWNERS 和 Agent 配置；默认先输出候选改动。
- `connect` 必须先显示目标文件和将写入的内容；`--dry-run` 绝不写文件。
- Claude Code 适配器使用真实支持的 Hook/MCP 配置形式；Codex 只生成 MCP/指令/CLI wrapper 所需配置，不假设它具有与 Claude Code 完全相同的 Hook。
- 安装幂等，不重复追加配置，不覆盖用户既有文件。
- Hook 必须能获取实际 Diff、repository root 和 base revision；不再生成缺少必需参数的命令。
- `doctor` 检查 Python、Policy、MCP schema、store、图谱 revision、库路径、CI workflow 和 Agent 配置，并区分 ok/degraded/failed。

### 必测场景

- dry-run 无文件变更。
- 连续安装两次的内容不变。
- 已有用户配置被保留。
- MCP 不可用和 store 不可写时 doctor 明确失败。
- 生成的 Hook/wrapper 在临时 Git 仓库内可真实执行。

### 验证

```bash
python -m pytest -q tests/test_agent_connectors.py tests/test_hook_doctor_resources.py
bizguard init --repository . --dry-run
bizguard connect codex --repository . --dry-run
bizguard doctor --repository . --json
./scripts/verify_install.sh --offline
python -m pytest -q
```

### 完成标准

- OpenCode/Claude Code/Codex 适配不复制业务逻辑。
- 安装和诊断测试验证真实产物和执行效果，不再只验证文件存在。

---

## 步骤 8：接通审计、Trace 和指标

### 目标

让每个 Change Context 能从 prepare、validate、impact、decision、approval 追溯到 CI 最终结果。

### 修改范围

- `src/bizguard/observability.py`
- `src/bizguard/change/evaluator.py`
- `src/bizguard/workflow/approval.py`
- `src/bizguard/ci/check.py`
- `tests/test_observability_integration.py`

### 实现要求

- 全链路传递 `change_context_id`、`trace_id`、policy revision、graph revision 和 knowledge revision。
- 审计事件使用明确 schema，事件顺序可重放。
- 增加 count、decision distribution、unknown rate、approval latency、evaluation P50/P95 和样本数。
- P95 使用测量样本计算；样本太少时明确标识，不宣传性能阈值。
- 脱敏覆盖字段名敏感和值内嵌敏感两种情况。
- 不记录完整 Diff、完整文档或 Agent conversation。

### 验证

```bash
python -m pytest -q tests/test_observability_integration.py tests/test_approval_workflow.py
python -m pytest -q
python -m ruff check src tests agents_mcp
python -m mypy src tests agents_mcp
```

### 完成标准

- 一次 E2E 检查可以按 `change_context_id` 重建完整事件顺序。
- canary 密钥、密码、token 和 conversation 不出现在任何持久化日志中。

---

## 步骤 9：重构 benchmark 指标和 holdout

### 目标

将“Full 在 12 个已知任务上 12/12”升级为更难被过拟合和过度拦截欺骗的评测。

### 修改范围

- `scripts/run_benchmark.py`
- `bench/ablations/`
- 新增 `bench/holdout/`
- `tests/test_benchmark_metrics.py`
- `tests/test_benchmark.py`

### 数据集要求

- 保留当前12任务作为 development/replay suite。
- 新增至少20个 holdout Diff，覆盖四态、多文件、诱饵、rename/delete、unknown boundary 和无风险变更。
- holdout truth 由独立 YAML/JSON 给出，运行时组件不能读取 truth。
- 根据可用条件，holdout 尽量来至第四个脱敏仓库或不同包结构，不只复制当前三仓路径。

### 指标要求

- Critical Violation Recall
- Unsafe Allow Rate
- False Block Rate
- 四态 confusion matrix 和 Macro-F1
- Approval Precision/Recall
- Required Test Recall
- Impact Recall
- 平均/P50/P95 延迟
- 实际 MCP/模型调用次数
- 如果有真实 Agent，记录 input/output token 和可核对成本；离线组不得用 Diff 字符数冒充 token 成本。

### 测量要求

- 每个非 Agent baseline 至少重复5次，报告样本数和 P50/P95。
- RAG Only 全部返回 Block 时，False Block Rate 必须暴露该问题。
- `Naive Baseline` 保持这一名称，不改成 `Agent Only`。
- recorded 轨迹与 live 轨迹分开输出。

### 验证

```bash
python -m pytest -q tests/test_benchmark_metrics.py tests/test_benchmark.py
python scripts/run_benchmark.py \
  --dataset bench/ablations/tasks.yaml \
  --offline \
  --out /tmp/bizguard-offline-benchmark.json
python scripts/run_benchmark.py \
  --dataset bench/holdout/tasks.yaml \
  --offline \
  --out /tmp/bizguard-holdout-benchmark.json
python -m pytest -q
```

### 完成标准

- 结果文件同时包含安全召回、过度拦截、四态准确性和延迟。
- 任何 baseline 不能通过“全部 Block”获得看似完美的综合结果。

---

## 步骤 10：运行并保存真实 Codex MCP 轨迹

### 目标

证明不只是 harness 和 mock 测试可运行，而是真实 Coding Agent 能在只读边界内调用 BizGuard MCP 并产生可重放结果。

### 执行前置条件

- 步骤 1–9 已全部通过。
- 用户已明确允许发起可能产生外部模型调用或费用的 live 运行。
- `BIZGUARD_CODEX_MODEL` 是当前账号真实可用模型。
- 传入的只是脱敏 fixture Diff。

### 执行

```bash
export BIZGUARD_CODEX_MODEL="<current-enabled-model>"
BIZGUARD_LIVE_AGENT_COMMAND="python scripts/codex_agent.py" \
BIZGUARD_LIVE_TASK_ID="critical-ledger-1" \
python scripts/run_benchmark.py \
  --dataset bench/ablations/tasks.yaml \
  --live \
  --out bench/ablations/live_results.json \
  --transcript-out bench/ablations/codex_agent_transcript.json
```

### 验证：安全和真实性

```bash
python -m pytest -q tests/test_codex_agent.py tests/test_benchmark.py
python - <<'PY'
import json
from pathlib import Path

transcript = json.loads(Path("bench/ablations/codex_agent_transcript.json").read_text())
assert transcript["track"] == "live"
assert not str(transcript["model"]).startswith("recorded-")
assert transcript["tool_calls"]
assert transcript["diff_sha256"]
print("live transcript metadata verified")
PY
```

还必须人工查看产物，确认：

- Agent 只调用允许的只读 BizGuard Tool。
- 输入 Diff hash 与冻结 fixture 相同。
- transcript 中的 Tool output 能被当前 FastMCP 实现重放。
- 不包含账号 token、本地绝对敏感路径或非 fixture 内容。

### 完成标准

- 仓库中存在一条真实、脱敏、可重放、`track=live` 的 Codex MCP 产物。
- 若因账号、模型或网络权限未运行，该步保持未完成，不得用 mock 替代。

---

## 步骤 11：文档校准和最终回归

### 目标

让 README、项目概览、设计方案和实际代码状态一致。

### 修改范围

- `README.md`
- `CONTRIBUTING.md`
- `项目资料/项目概览.md`
- 必要时对 `项目资料/设计方案v3.md` 增加 implementation status，不改写原设计目标。
- 本执行计划的完成清单。

### 校准内容

- 使用当前 `pytest --collect-only` 结果，不手写过时测试数。
- 修正本地仓库路径或改为不绑定机器的相对说明。
- 将“Agent Only”与“Naive Baseline / recorded heuristic”正确区分。
- 将六场景 Demo 标记为已完成。
- 仅在步骤 10 完成后才将真实 Agent MCP 轨迹标记为已完成。
- 继续保留“开源验证项目，未获得生产 Blocking 批准”的声明。

### 验证：最终全量回归

```bash
python -m ruff check src tests agents_mcp scripts
python -m ruff check --select D101,D103 src/bizguard agents_mcp
python -m mypy src tests agents_mcp
python -m pytest -q

python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase1
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase2
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase4
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase5

python -m bizguard.eval.retrieval \
  --dataset bench/golden/retrieval/phase2.yaml \
  --offline

python -m bizguard.graph.build \
  --repos fixtures/java-microservices \
  --revision-set bench/fixtures/phase3-revisions.yaml \
  --out /tmp/bizguard-phase3-graph.json
python -m bizguard.eval.impact \
  --dataset bench/golden/impact/phase3.yaml \
  --graph /tmp/bizguard-phase3-graph.json

./scripts/verify_install.sh --offline
./scripts/demo.sh

python scripts/run_benchmark.py \
  --dataset bench/ablations/tasks.yaml \
  --offline \
  --out /tmp/bizguard-final-benchmark.json

git diff --check
git status --short
```

### 最终完成标准

- 上述静态检查、全量测试、Golden verifier、检索评测、影响评测、安装验证、Demo 和离线 benchmark 全部通过。
- CLI、MCP、Hook 和 CI 对同一输入产生一致核心结果。
- CI 真正检查 PR Diff，非放行状态不再返回成功。
- 审批和审计可持久化、可恢复、可幂等。
- MCP Resources 能通过真实 FastMCP session 发现和读取。
- Agent 连接器可 dry-run、可幂等安装、不覆盖用户配置。
- benchmark 报告 False Block Rate、四态混淆矩阵、P50/P95 和真实成本边界。
- 存在一条可审计的真实 Codex MCP 轨迹；否则只能声明代码轨道已就绪。
- README 的限制说明与实际状态一致。

---

## 4. 建议提交边界

每个提交只包含一个可独立验收的完整改动：

1. `test+feat: support safe multi-file unified diffs`
2. `refactor: establish canonical change evaluator`
3. `refactor: route cli mcp hook and ci through shared evaluator`
4. `feat: enforce pull request decisions in ci`
5. `feat: persist approval workflow and audit state`
6. `feat: register governed mcp resources`
7. `feat: add idempotent agent connectors and diagnostics`
8. `feat: connect change traces audit and metrics`
9. `eval: add holdout and balanced benchmark metrics`
10. `eval: record validated live codex mcp trajectory`
11. `docs: align project status and verification evidence`

提交前必须确认：

- 无与本步无关的重构或格式化。
- 无缓存、虚拟环境、临时数据库、密钥或本机绝对路径。
- 提交说明中记录实际执行的验证命令和结果，不只写“tests passed”。

---

## 5. OpenCode 终止和汇报格式

完成每一步后，输出：

```text
步骤：<编号和名称>
状态：完成 / 阻塞
改动文件：<精确列表>
新增契约：<schema/行为变化>
实际验证：<命令 + passed/failed 数>
兼容性：<旧接口和 Golden 是否保持>
剩余风险：<必须如实说明>
下一步：<仅当本步全绿时继续>
```

以下任一条发生时必须停止，不得自行扩大权限：

- 需要改变已冻结的业务 Golden 结论。
- 需要访问未脱敏仓库、生产数据或外部审批系统。
- 需要发起产生费用的真实 Agent 调用，但用户未明确批准。
- 需要覆盖用户现有 Agent/CI 配置。
- 发现设计要求与已使用的对外 schema 存在无法兼容的冲突。

---

## 6. 完成状态清单

OpenCode 执行时只能在对应步骤的全部验收完成后勾选：

- [x] 步骤 0：冻结现状和契约
- [x] 步骤 1：多文件 Unified Diff
- [x] 步骤 2：唯一 ChangeEvaluator
- [x] 步骤 3：四入口 parity
- [x] 步骤 4：真实 PR/CI 门禁
- [x] 步骤 5：持久化审批闭环
- [x] 步骤 6：真实 MCP Resources
- [x] 步骤 7：Agent 连接器和诊断
- [x] 步骤 8：审计、Trace 和指标
- [x] 步骤 9：holdout 和平衡评测
- [x] 步骤 10：真实 Codex MCP 轨迹（gpt-5.6-terra，track=live）
- [x] 步骤 11：文档校准和最终回归

所有项完成后，BizGuard 可以宣称“开源 MVP 功能闭环完成”。是否进入生产 Blocking，仍需真实业务样本下的 `shadow → warning → blocking` 验证，不属于本执行计划自动授权范围。
