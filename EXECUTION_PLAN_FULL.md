# BizGuard：从 MVP 到完整版执行计划

> 执行依据：`BizGuard-设计方案v3.md` 与当前 `biz-guard/` MVP。本文只规划 MVP 尚未具备的能力；不重复建设既有确定性三态决策、单条幂等 AST 不变量、契约注册表、全文注入、embedding 隔离评测、CLI/FastMCP 双入口、14 个冻结 Diff 与 32 个测试。
>
> 估算口径：一名熟悉 Python、测试与 Java/Spring 的开发者，全职、脱敏样例仓库优先。总计 **50–65 个工作日**：各 Phase 实现 39–51 天（含 P0 的 1–2 天、P3 的 6–8 天、P5 的 12–15 天），六个场景演示脚本 2–3 天，以及跨模块级联重验、回归修复与完成验收 9–11 天；不包含接入真实企业事实源、生产权限/OAuth、真实团队试运行所需的外部协调时间。所有“性能阈值”和“误报阈值”先由基准测量确定，不虚构数字。

## 1. 北极星、范围和不可违背的执行规则

完成目标不是重造 Coding Agent，而是让现有 Agent 在一次变更中得到版本一致、可追溯的业务上下文；由确定性校验、测试证据和显式人工责任共同决定是否放行。

最终本地演示必须覆盖以下六种确定性可回放场景：历史字段删除、核销顺序重构、跨服务 DTO 变更、动态映射未知边界、低风险重构放行、候选规则从 shadow 升级至 blocking。团队模式只在本地接口和证据语义稳定后接入，不把 Neo4j、Kafka、OpenSearch 或真实 LLM 当作“完整版完成”的前置条件。

所有 Phase 均遵守：

1. **先黄金答案，后业务实现。** 新能力的 fixture、预期 JSON、证据 ID 与反例必须先合并；实现不得反向修改黄金答案来迁就代码。
2. **证据有等级且可定位。** 每条边、知识、Finding 都含 `source`、`confidence`、`revision`、`evidence_uri`；结论显示 `FACT`、`INFERENCE`、`POLICY` 或 `UNKNOWN`。
3. **未知不伪装成安全。** 索引落后、版本不一致、权限拒绝、动态映射或校验未运行，只能形成 `UNKNOWN_BOUNDARY`、`ALLOW_WITH_TESTS` 或 `REQUIRE_APPROVAL`，绝不能形成 `ALLOW`。
4. **稳定核心，薄适配器。** CLI、MCP、Hook、CI 共享领域模型与决策 API；适配层不得复制规则、风险或审批逻辑。
5. **旧基线不可改写。** `tests/fixtures/ground_truth.yaml` 的 14 条 MVP Diff 及其既有预期冻结；扩展样例置于新的 `bench/` 或 `tests/fixtures/v2/`，不改变历史语义。
6. **版本与时间可复现。** 分析器、JVM 工具链与 embedding 版本均锁定；stale、TTL、超时测试一律注入时钟，审批写入必须幂等。

## 2. 已验证的 MVP 基线

| 项目 | 当前事实 | 后续保护方式 |
| --- | --- | --- |
| 决策 | `ALLOW` / `BLOCK` / `CHECK_INCOMPLETE`，故障语义闭包 | 作为 `legacy_decision` 兼容层；新增四态决策时保留映射与回归测试 |
| 规则 | 1 条 Python AST 幂等不变量 | 保留 YAML 单一事实源和“校验失败不放行”原则，扩展为 Policy 生命周期与多验证器 |
| 影响 | 人工契约表的字段→服务→Policy 映射 | 以该表作为图谱/Provider 的第一份可验证事实源，而非直接丢弃 |
| 检索 | 匹配契约后的知识全文注入；embedding 仅评测 | 迁移为带 scope、版本、TTL、ACL 的 Hub，并新增独立混合检索评测 |
| 接入 | CLI + FastMCP；`prepare_change`、`validate_patch` 两工具 | 扩为 8 工具，所有工具调用同一核心服务 |
| 质量 | 14 冻结 Diff、32 测试；`ruff` 与 strict `mypy` 约定 | 每个 Phase 都要求这 32 个测试与 14 fixture 原样通过 |

基线核验命令（实施开始前及每次合并前执行）：

```bash
cd biz-guard && source ../.venv/bin/activate
python -m pytest --collect-only -q   # 输出必须含 “32 tests collected”
python -m pytest -q                  # 32 passed
python -m ruff check src tests agents_mcp
python -m mypy src tests agents_mcp   # strict，0 error
```

## 3. 差距分析与依赖裁决

| 差距能力 | MVP 现状与缺口 | 前置依赖 | 复杂度 | 面试价值 | 落点 |
| --- | --- | --- | --- | --- |
| 统一领域模型与四态决策 | 只有三态 `ChangeSafetyCard`，没有 `ChangeContext`、Evidence、风险、测试与审批语义 | 无；必须最先完成 | 中 | 高 | P1 |
| 三仓 fixture 与最小语义种子 | 无 Java/Spring 可离线构建样例，也无冻结的 Capability/Owner/测试事实 | 无；必须先于版本化任务 | 中 | 高 | P0 |
| 基准集与 Golden Context/Impact/Decision | 仅 14 个单仓 Python Diff；没有跨服务、未知边界、审批与版本场景 | P0 fixture/catalog、统一 schema | 中 | 高 | P1 |
| Knowledge Hub 元数据与治理 | 仅 Markdown frontmatter 与全文注入；缺 scope/revision/TTL/ACL、候选审核和 stale | P1 Evidence Contract | 中 | 高 | P2 |
| BM25 + 向量 + 重排 | 无词法/语义融合、版本/权限过滤及可测排序 | P1 基准；P2 知识 schema | 中 | 中 | P2 |
| 业务语义层与必需测试选择 | 没有 Domain/Capability/Entity/State/Owner 模型，测试靠人工 | P1；P2 的知识与契约事实 | 中 | 高 | P2 |
| Java/Spring 静态索引 | 仅 Python diff/AST 检查，无 Java 类、方法、字段、注解和 Maven/Gradle 分析 | P0 Canonical ID；冻结 catalog | 中 | 高 | P3 |
| 契约与字段级跨服务图谱 | 无 DTO/API/MQ/DB 解析、8 类节点/21 类边、五层证据路径和增量快照 | P3 符号索引；冻结 catalog | 高 | 极高 | P3 |
| 运行时 Trace 证据 | 未接入 OTel/AppMap；静态无法证明时没有明确补证据机制 | P3 图谱/Evidence Contract | 中 | 高 | P3 |
| `analyze_impact` / `explain_symbol` / `get_required_tests` / `search_team_knowledge` | MCP 只有两个工具 | P1–P3 对应核心服务 | 中 | 高 | P4 |
| Context Compiler 与 `prepare_change` | 当前工具只是复用 diff 校验，没有任务、commit、Context Pack、token 分层或 stale 检测 | P1、P2、P3 | 高 | 极高 | P4 |
| 扩展 Policy 与风险引擎 | 仅一条 blocking AST 规则，缺 API/DB/MQ/架构验证器、Policy 状态和测试证据 | P1 schema；P2 测试映射；P3 契约图 | 高 | 极高 | P5 |
| HITL、会签、豁免和审计 | 无 `REQUIRE_APPROVAL`、审批状态、路由、限时豁免、审计 | P5 风险/Owner；P1 变更状态 | 高 | 高 | P5 |
| Agent Hook、CI、安装诊断 | 当前只有 CLI/MCP server，无自动触发、CI 权威重检、`doctor` 与降级提示 | P4/P5 稳定核心 | 中 | 高 | P5 |
| benchmark、消融实验、可观测 | 有小型 embedding 评测，无 5 组对照、指标、Trace、可复现实验 | P1–P5 功能均可离线运行 | 高 | 极高 | P5 |

依赖图：

```text
P0 三仓 fixture + 最小 semantic catalog + 分析器裁决
 └─ P1 领域契约 + manifest/schema verifier
     ├─ P2 Knowledge Hub + 语义/测试映射
     └─ P3 Java/Spring + 契约/运行时图谱
            \             /
             └── P4 Impact + Context Compiler + 8 MCP Tool ── P5 Policy/Risk/HITL/Hook/CI/Benchmark
```

真实串行点是语义 catalog 冻结，已在 P0 解决；因此 P2 与 P3 可在 P1 的 schema 冻结后并行。P4 必须等待二者完成：Context Compiler 不能先定义一套与图谱或知识不兼容的证据格式。P5 最后执行，因为审批、CI 和 benchmark 都必须消费稳定的影响、测试和决策语义。

## 4. Phase 0：离线 Java fixture、语义种子与分析器裁决（1–2 天）

### 目标与交付物

- `fixtures/java-microservices/{coupon-core,merchant-service,coupon-contract}/`：三个最小、脱敏、可离线 Maven/Gradle 构建的仓库骨架，覆盖 Spring Controller/Service、DTO/Proto、JPA/MyBatis、MQ producer/consumer 与动态 Mapper。
- `fixtures/java-microservices/README.md`、锁定的 wrapper/依赖缓存说明与 `scripts/verify_java_fixtures.sh`：在无网络环境验证三个仓库。
- `src/bizguard/graph/ids.py`：冻结 `repo://`、`api://`、`proto://`、`db://`、`mq://` Canonical ID 生成规则。
- `docs/adr/` 中的分析器 spike 裁决：比较 JavaParser/JDT 与 tree-sitter，固定 tree-sitter + 契约解析 + 人工标注 Mapper 边的轻量路径，并 pin Python 包、grammar、JDK、Maven/Gradle 版本；不引入 JDT 全量类型绑定。
- `src/bizguard/semantic/catalog.yaml`：冻结最小 Capability、Owner、Entity、State、Invariant、Policy、RequiredTest 种子，供 P1 的 12 个任务和 P2/P3 共同消费。
- `tests/test_fixture_build.py`、`test_canonical_ids.py`、`test_semantic_seed.py`。

### 验收标准与验证

```bash
cd biz-guard && source ../.venv/bin/activate
./scripts/verify_java_fixtures.sh --offline
python -m pytest -q tests/test_fixture_build.py tests/test_canonical_ids.py tests/test_semantic_seed.py
# 断言：三个仓库离线可构建；至少 10 passed；catalog 种子、Canonical ID 和工具链版本均冻结。

python -m pytest -q
# 断言：至少 42 passed，且既有 32 项与 14 个 MVP fixture 全部通过。
```

### 防返工清单

- P1 不得新增或变更 fixture 业务语义来迁就黄金答案；fixture、catalog 与其 revision 先冻结。
- Java fixture 的 wrapper、依赖与构建命令必须可离线使用；构建失败不得被分析器静默忽略。
- 后续扩展 catalog 或分析器版本时，必须保留旧版本并级联重验所有下游 Golden suite。

## 5. Phase 1：领域契约、版本化证据与黄金基准（5–7 天）

### 目标与交付物

先建立后续全部模块的机器可读边界，而不是立即加功能。

- `src/bizguard/domain/models.py`：`ChangeContext`、`Evidence`、`ChangedArtifact`、`ImpactPath`、`RequiredTest`、`Finding`、`Decision`、`ApprovalRequest` 的 Pydantic schema。
- `src/bizguard/domain/enums.py`：四态 `ALLOW`、`ALLOW_WITH_TESTS`、`REQUIRE_APPROVAL`、`BLOCK`；证据等级、Policy mode、变更状态与未知原因枚举。
- `src/bizguard/domain/compat.py`：MVP 三态到新决策的**按 `FaultCode` 分流的显式只读映射表**；`CHECK_INCOMPLETE` 不映射为 `ALLOW`，垃圾输入不被过度升级为审批。
- `src/bizguard/domain/schema_v2.py`、`tests/fixtures/v2/invariants.yaml`：为 lifecycle 增加 `extra="forbid"` 的 v2 schema；MVP v1 blocking 规则的资格证据固定为 P0 冻结 fixture 集（grandfather），不回写 v1。
- `src/bizguard/evidence/contracts.py`：所有 Provider 的 Evidence Contract，强制 `id/source/confidence/revision/evidence_uri`。
- `src/bizguard/bench/verify.py`：唯一的 `python -m bizguard.bench.verify` verifier 入口，按 suite 只校验已有生产者负责的字段。
- `bench/fixtures/manifest.yaml`、`bench/golden/{context,impact,decision}/`：不污染既有 14 fixture 的新增基准。
- `tests/test_domain_models.py`、`tests/test_golden_manifest.py`、`tests/test_legacy_compat.py`、`tests/test_schema_v2.py`。

### 先冻结的 ground truth

先提交 **12 个版本化任务**（每类 2 个）及其完整 JSON 黄金答案：

| 场景 | 数量 | 必须冻结的答案 |
| --- | ---: | --- |
| 私有方法重命名 | 2 | 单服务、无强制 Policy、`ALLOW` |
| 幂等调用顺序破坏 | 2 | critical Policy、`BLOCK`、源码证据 |
| DTO 增加/删除字段 | 2 | DTO→API/消费者→服务→Capability 的预期路径 |
| 账本/状态一致性 | 2 | 必需单测与契约测试、违反时 `BLOCK` |
| 动态 Mapper | 2 | `UNKNOWN_BOUNDARY`、`REQUIRE_APPROVAL`，禁止直接 ALLOW |
| 过期知识/commit 不一致 | 2 | `stale` 证据和 `REQUIRE_APPROVAL` |

每个任务 manifest 固定：任务 ID、base revision、输入 Diff、可见知识 ID、Golden Context、Golden Impact、Golden Decision、预期必需测试、可人工复核的来源文件。黄金 JSON 按稳定 ID 排序；更新须写明变更原因，不能由测试的“自动更新”覆盖。

`compat.py` 将旧 `CHECK_INCOMPLETE` 按首个确定 `FaultCode` 映射；未知 FaultCode 默认 `BLOCK`，不得产生 `ALLOW`：

| FaultCode | 四态映射 | 原因 |
| --- | --- | --- |
| `INDEX_LAG`、`REVISION_MISMATCH`、`PERMISSION_DENIED`、`DYNAMIC_BOUNDARY` | `REQUIRE_APPROVAL` | 证据不足或边界未知，需要人工承担责任 |
| `TEST_EVIDENCE_MISSING` | `ALLOW_WITH_TESTS` | 仅缺少可执行的测试证据，且无更高优先级故障 |
| `MALFORMED_INPUT`、`UNSUPPORTED_DIFF` | `BLOCK` | 垃圾或不可解析输入不是审批请求 |
| 其他/缺失 FaultCode | `BLOCK` | 保守失败，防止兼容层扩散不确定性 |

### 验收标准与验证

单元 ground truth：新增 **至少 18 个**测试（schema 正反例 10、证据不可缺字段 3、决策优先级/兼容 3、manifest 完整性 2）。

```bash
cd biz-guard && source ../.venv/bin/activate
python -m pytest -q tests/test_domain_models.py tests/test_golden_manifest.py tests/test_legacy_compat.py tests/test_schema_v2.py
# 断言：至少 18 passed；12 个任务的 manifest/schema 均能加载；所有 Evidence 均有 5 个必填字段；
# FaultCode 映射覆盖版本不一致、权限/索引故障与垃圾输入，且 CHECK_INCOMPLETE 不会成为新 ALLOW。

python -m pytest -q
# 断言：至少 60 passed，且既有 32 项与 14 个 MVP fixture 全部通过。

python -m ruff check src tests agents_mcp && python -m mypy src tests agents_mcp
# 断言：两命令退出码 0；mypy 输出 “Success: no issues found”。
```

端到端闭环命令：

```bash
cd biz-guard && source ../.venv/bin/activate
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase1
# 断言：12/12 manifest、schema 与 Golden 引用加载成功；无缺 revision 或 evidence_uri。
# Context、Impact、Decision 的内容匹配分别由 P4、P3、P5 的生产者实现后再变绿。
```

### 防返工清单

- 不在 `decision.py` 原地把旧枚举悄悄改为四态；先建 adapter，保留两套输出版本并给迁移期限。
- 不允许用文件名或裸方法名作跨仓 ID；从第一天使用 `repo://`、`api://`、`proto://`、`db://`、`mq://` 规范。
- Golden 不存“人工印象”；每条结论必须可链接到脱敏源码、schema、Trace 或知识文档。
- 真实指标尚未测量前，文档和 CI 只写“门槛待基准确定”，不能写虚假的延迟或准确率目标。
- 任一 Golden、schema 或 fixture 改动都先运行本 Phase verifier，再级联运行全部已落地的下游 suite；不可只更新局部快照。

## 6. Phase 2：Knowledge Hub、业务语义与混合检索（7–9 天）

### 目标与交付物

- `src/bizguard/knowledge/models.py`、`repository.py`、`ingest.py`：知识 scope、owner、source revision、confidence、TTL、security label 与状态管理。
- `src/bizguard/knowledge/search.py`、`rerank.py`：SQLite FTS/BM25、本地向量 adapter、时间/版本/权限过滤和可解释加权重排。
- `src/bizguard/semantic/models.py`、`required_tests.py`：消费 P0 冻结的 `catalog.yaml`，提供 Domain、Capability、Entity、State、Invariant、Owner 与测试映射；修改 catalog 必须走新版本与级联重验。
- `knowledge/candidates/`：候选知识只能处于 draft；`knowledge/published/` 仅收 Owner 已确认内容。
- `tests/test_knowledge_ingest.py`、`test_knowledge_search.py`、`test_semantic_catalog.py`、`test_required_tests.py`；`bench/golden/retrieval/`。

### 先冻结的 ground truth

新增 **15 条检索任务**，每条指定 caller 权限、repo revision、查询、候选集、top-5 的有序 `knowledge_id`、必需 Policy 以及禁止返回的过期/越权文档：

- 5 条精确字段/枚举查询：BM25 必须命中对应字段卡；
- 4 条语义相近的故障/ADR 查询：向量通道 Gold 文档须进入 top-5，并同时满足硬过滤；
- 3 条 scope 或 revision 不匹配查询：Gold 文档必须被过滤；
- 2 条 ACL 查询：无权限文档不得出现在任何结果或摘要；
- 1 条 critical Policy scope 命中查询：即使普通排序靠后也必须注入 Mandatory。

另冻结 6 个业务语义任务：DTO 字段、账本、状态枚举、幂等键、公开 API、低风险私有方法各一个，固定 Capability、Owner、Mandatory Policy 和 RequiredTest ID；每项至少含一个诱饵测试，防止“返回全部测试”作弊。

### 验收标准与验证

单元 ground truth：新增 **至少 24 个**测试（摄取/元数据 6、过滤 6、混合召回与排序 7、语义映射 3、必需测试选择及诱饵排除 2）。

```bash
cd biz-guard && source ../.venv/bin/activate
python -m pytest -q tests/test_knowledge_ingest.py tests/test_knowledge_search.py tests/test_semantic_catalog.py tests/test_required_tests.py
# 断言：至少 24 passed；5 条 BM25 精确查询的 top-5 与 Golden 完全一致；4 条向量查询 Gold 入 top-5 且硬过滤正确；
# 3 条 stale、2 条 ACL 文档零泄漏；唯一 critical Policy 1/1 scope 命中即注入。

python -m bizguard.eval.retrieval --dataset bench/golden/retrieval/phase2.yaml --offline
# 断言：输出 JSON 含 query_count=15、mandatory_policy_recall=1.0、
# stale_knowledge_rate=0.0、acl_leak_count=0；并逐条写出 expected/actual ID。

python -m pytest -q
# 断言：至少 84 passed；既有 32 项和 MVP 14 fixture 全部仍通过。
```

端到端闭环命令：

```bash
cd biz-guard && source ../.venv/bin/activate
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase2 --offline
# 断言：6/6 语义任务输出固定 capability、owner、mandatory_policy、required_test；
# 返回的每份知识具有 revision、freshness 与 evidence_uri。
```

### 防返工清单

- BM25、向量、过滤和重排必须保留各通道分数与淘汰原因；不能只保存最终文本。
- ACL 和 revision 过滤发生在排序及摘要前，不能“先检索后隐藏”。
- 向量 provider 不可用时仅降级语义通道并写明 `UNKNOWN`；不可把缓存过期当命中。
- LLM 生成内容只能写入 candidate，不可自动提升为 Policy 或 published knowledge。
- 摄入阶段扫描 prompt-injection 指令并隔离/拒绝命中项；脚本纳入 `ruff`/`mypy` 检查，无法纳入的第三方脚本须在配置中逐项明示豁免。
- embedding 模型、索引参数和语义分析器版本写入证据；版本变更必须重建并级联重验 retrieval 与下游 suite。

P2 简历检查点：可独立演示并讲清混合检索、治理评测与 `mandatory_policy_recall=1.0`；该数值只表示本冻结集的 1/1 critical Policy，不外推为生产召回率。

## 7. Phase 3：Java/Spring、契约与跨服务证据图谱（6–8 天）

### 目标与交付物

- `src/bizguard/graph/{models,store,ids,indexer,incremental,build}.py`：嵌入式图、commit 快照、Canonical ID、8 类节点与 **21 类边**；`build.py` 是 `python -m bizguard.graph.build` 入口。
- `src/bizguard/analyzers/{java_spring,openapi_proto,persistence,messaging}.py`：采用 P0 裁决的 tree-sitter + 契约解析；动态 Mapper 边由人工标注，不追求 JDT 全量类型绑定。
- `src/bizguard/providers/{code_context,contract,service_catalog,runtime_evidence}.py`：本地 fixture Provider 和统一 Evidence Contract。
- `fixtures/traces/`、`src/bizguard/graph/runtime.py`：导入脱敏 OTel/AppMap JSON，生成 `OBSERVED_CALL`，保留 `first_seen/last_seen`。
- `src/bizguard/impact/analyzer.py`：L1–L5 影响路径与 `UNKNOWN_BOUNDARY`。

### 先冻结的 ground truth

冻结 **10 个图谱快照任务**，每个给定三个仓库的 base commit、变更 Diff、预期节点/边集合、最短影响路径和未知边界：

| 场景 | 数量 | 预期五层路径 / 关键断言 |
| --- | ---: | --- |
| DTO `status` 字段修改 | 2 | Field→DTO/API→Mapper/Consumer→Service→coupon redemption Capability |
| Proto/MQ 枚举兼容性 | 2 | Event Field→Topic→Consumer→merchant service→Capability |
| DB 列/Mapper 读写 | 2 | Column→SQL/ORM→Service→Entity→Invariant |
| Spring API 调用 | 2 | Endpoint→Feign/RPC→upstream/downstream Service→Owner |
| 自定义反射 Mapper | 2 | 到 Mapper 停止，必有 `UNKNOWN_BOUNDARY`，不得伪造静态边 |

并冻结 4 份 runtime Trace：一份静态/Trace 都有、一份仅静态、一份仅 Trace、一份都没有。各自的 confidence 与证据来源必须固定；“都没有”不能得出无影响。

### 验收标准与验证

单元 ground truth：新增 **至少 30 个**测试（Canonical ID 3、Java/Spring 6、契约/MQ/DB 7、图存储及快照 4、增量索引 2、Trace 合并 3、影响路径/未知边界 5）。

```bash
cd biz-guard && source ../.venv/bin/activate
python -m pytest -q tests/test_graph_ids.py tests/test_java_spring_indexer.py tests/test_contract_indexer.py tests/test_graph_snapshot.py tests/test_runtime_evidence.py tests/test_impact_analyzer.py
# 断言：至少 30 passed；10/10 任务的节点、21 类边、最短路径与 Golden JSON 完全一致；
# 两个动态 Mapper 都返回 UNKNOWN_BOUNDARY；四类静态/Trace 合并结论均匹配 Golden。

python -m bizguard.graph.build --repos fixtures/java-microservices --revision-set bench/fixtures/phase3-revisions.yaml --out .artifacts/phase3-graph.json
python -m bizguard.eval.impact --dataset bench/golden/impact/phase3.yaml --graph .artifacts/phase3-graph.json
# 断言：输出 task_count=10、path_evidence_completeness=1.0、unknown_boundary_recall=1.0；
# 每条路径有 source、confidence、revision、evidence_uri。

python -m pytest -q
# 断言：至少 114 passed；旧 32 项与 14 fixture 全部通过。
```

端到端闭环命令：

```bash
cd biz-guard && source ../.venv/bin/activate
python -m bizguard.impact analyze --diff bench/fixtures/phase3/dto-status.diff --repos fixtures/java-microservices --revision-set bench/fixtures/phase3-revisions.yaml --format json
# 断言：返回 L1–L5 五层路径、merchant-service、coupon-redemption capability、
# 至少一个可追溯证据 ID；输出与 dto-status.impact.json 字节规范化后一致。
```

### 防返工清单

- “8 类节点/21 类边”是图 schema 覆盖目标，不是让每个 fixture 人为塞满所有边；只索引可证实事实。
- 图查询必须按 repo+commit 快照执行；`INDEX_LAG` 或 revision 混用直接升级为未知，不借用最新版补洞。
- 不用字符串搜索假装 Java 分析；字段、调用、注解、API 与 schema 的证据必须带精确位置。
- Trace 只增加运行时证据，不覆盖静态边；Trace 缺失不能删静态边。
- 外部 Provider 先用 fixture adapter 合约测试，真实 Backstage/OTel 接入留到接口已稳定后。
- 分析器版本、grammar 与 JVM 工具链版本锁定在 snapshot 元数据；更新时重建图并级联重验 Impact、Context 与 Decision suite。

## 8. Phase 4：Impact API、Context Compiler 与完整 MCP 读工具（8–10 天）

### 目标与交付物

- `src/bizguard/context/{compiler,cache,staleness}.py`：任务到 Context Pack 的编译、Mandatory/Structural/Rationale/Expandable 四层、token budget、base commit 校验和 stale 处理。
- `src/bizguard/impact/service.py`、`src/bizguard/symbols/service.py`：供 CLI/MCP 共同调用的 Impact、符号解释和测试选择服务。
- `src/bizguard/change/store.py`：本地 SQLite change context 存储；只写本地运行目录，保存 immutable context/evidence ID。
- `agents_mcp/server.py` 扩展为 8 Tool：`prepare_change`、`search_team_knowledge`、`explain_symbol`、`analyze_impact`、`validate_patch`、`get_required_tests`、`request_approval`、`get_change_decision`。其中审批工具仅注册 schema，在 P5 才接真实写入。
- `agents_mcp/schema.py`：唯一的 MCP Schema 导出入口；不创建 `bizguard.mcp` 平行模块。
- `src/bizguard/impact/__main__.py`：`python -m bizguard.impact analyze` 的唯一 Impact 命令入口；与既有 `graph.build`、`bench.verify` 同样只委派给核心服务。
- `src/bizguard/cli.py` 新增 `prepare`、`impact`、`knowledge search`、`symbol explain`、`tests required`；输出同一 schema。
- `tests/test_context_compiler.py`、`test_mcp_read_tools.py`、`test_cli_context.py`、`test_context_staleness.py`。

### 先冻结的 ground truth

使用 P1–P3 的 12 个任务，冻结 **12 份 Context Pack**：每份锁定 `change_context_id` 生成算法、代码 revisions、Mandatory Policy、候选符号、上下游路径、相关知识、required tests、required approvers、unknowns 与 evidence。额外固定 4 个 token budget（800/1,200/2,000/4,000）：Mandatory 必须 100% 保留，其他层只能截断可展开内容，不可截断 evidence ID。

再冻结 8 个 MCP JSON Schema 快照和 16 个请求/响应样例：每工具 1 正例、1 无效/无权限/stale 例；所有返回都必须区分证据等级，不能倾倒整图或全文知识。

### 验收标准与验证

单元 ground truth：新增 **至少 30 个**测试（Context 编译 10、预算/分层 4、stale/缓存 4、CLI 4、8 Tool schema 4、读工具与核心一致性 4）。

```bash
cd biz-guard && source ../.venv/bin/activate
python -m pytest -q tests/test_context_compiler.py tests/test_context_staleness.py tests/test_mcp_read_tools.py tests/test_cli_context.py
# 断言：至少 30 passed；12/12 Context Pack 与 Golden 一致；4 种预算下 mandatory_policy_recall=1.0；
# base commit 改变后 context 被标为 stale，不能复用为 fresh。

python -m agents_mcp.schema --out .artifacts/mcp-tools.json
python -m bizguard.bench.verify --manifest bench/fixtures/manifest.yaml --suite phase4 --offline
# 断言：schema 恰有 8 个指定 Tool；16/16 I/O 样例通过；12/12 Context Pack、Impact 与 Required Tests 匹配 Golden。

python -m pytest -q
# 断言：至少 144 passed；旧 32 项与 14 fixture 全部通过。
```

端到端闭环命令：

```bash
cd biz-guard && source ../.venv/bin/activate
python -m bizguard.cli prepare --task '修改 CouponDTO.status 枚举' --repos coupon-core merchant-service --base-revisions bench/fixtures/phase3-revisions.yaml --json > .artifacts/context.json
python -m bizguard.cli impact --change-context .artifacts/context.json --json
# 断言：第一条命令签发 context ID；第二条输出与 dto-status Context/Impact Golden 一致，
# 含 required_tests、required_approvers、unknowns 与 evidence。
```

### 防返工清单

- `prepare_change` 不再以 diff 代替任务输入；保持兼容入口，但明确标注 legacy，不能混淆两种语义。
- Context Pack 只存摘要和证据链接；全文和图细节通过资源/查询按需展开。
- cache key 必须包含 task、repos、base revision、权限主体和相关索引版本；少任一项均可能越权或返回旧结论。
- Fast Check 明确归属 `context/cache` 模块：只复用同 revision 的 immutable Context Pack；CI Slow Check 仍从 base revision 重算。
- `request_approval` 在此阶段不得创建外部任务或绕过显式授权，避免读工具被悄悄变写工具。
- Golden 或 cache schema 改动先重验 Context suite，再级联运行 Policy/CI suite；时钟由测试注入，禁止 sleep 等待 TTL。

P4 简历检查点：可独立演示 Context Pack、8 Tool 与 token 四层裁剪；Mandatory 层在四种预算下均完整保留。

## 9. Phase 5：Policy/Risk/HITL、Hook、CI 与可复现评测（12–15 天）

### 目标与交付物

- `src/bizguard/policy/{lifecycle,registry,validators}.py`：扩展 AST、ArchUnit/OpenRewrite 适配、OpenAPI/Proto、DB migration、MQ schema、config validator；保留现有 YAML 不变量兼容。
- `src/bizguard/risk/engine.py`、`src/bizguard/decision/v2.py`：硬条件优先的四态决策；所有 Finding 有 severity、effect、remediation、required approver、confidence。
- `src/bizguard/workflow/{state_machine,approval,audit}.py`：状态机、会签、代理、补证据、拒绝、限时豁免、超时升级与审计链。
- `src/bizguard/ci/check.py`、`.github/workflows/bizguard.yml`、`scripts/verify_install.sh`：PR/CI 权威重检与安装闭环。
- `src/bizguard/hooks/{agent,install}.py`、`agents_mcp/resources.py`、`src/bizguard/cli.py doctor`：Agent Hook、`doctor/connect/init` 诊断和 MCP Resources；均只调用既有核心服务，资源按需返回摘要与证据链接。
- `src/bizguard/observability.py`：change trace、JSON audit、指标导出；不记录密钥、完整敏感文档或 Agent 对话。
- `bench/ablations/`、`scripts/run_benchmark.py`：Agent Only、Rules Only、RAG Only、Context、Full 五组离线可回放消融；另设至少一条 Claude Code 或 Codex 经 MCP 操作本地 fixture 的真实 Agent 实弹轨道。
- `tests/test_policy_lifecycle.py`、`test_risk_decision.py`、`test_approval_workflow.py`、`test_ci_parity.py`、`test_hook_doctor_resources.py`、`test_benchmark.py`。

### 先冻结的 ground truth

在既有 1 条幂等规则之外，新增 **3 条规则**，每条先冻结 **9 个 Diff**（3 违规、3 正常、3 诱饵），合计 27 个；并定义 validator、scope、severity、owner、修复指引和测试 ID：

1. `redeem-ledger-consistency`：状态成功与账本写入顺序/失败语义一致；
2. `published-dto-backward-compatible`：已发布 DTO/Proto 不得删除必填字段或不兼容枚举；
3. `coupon-write-consumes-idempotency-key`：关键写入口必须消费幂等键。

另冻结 12 个决策任务：4 个 `BLOCK`、3 个 `ALLOW_WITH_TESTS`、3 个 `REQUIRE_APPROVAL`（动态边界、stale、跨 Owner）、2 个 `ALLOW`。每条含预期测试运行状态、审批人集合、会签数、审计事件序列与是否允许豁免。审批 fixture 至少覆盖：批准、拒绝、补证据、两人会签、过期豁免、审批服务不可用。

Policy 生命周期 fixture 固定一条候选规则从 `draft → shadow → warning → blocking`，以及因误报回退 `blocking → warning`。晋级门槛读取 fixture 配置，不把未经试运行的虚构比率硬编码到产品策略。

### 验收标准与验证

单元 ground truth：新增 **至少 51 个**测试（3×9 Diff 规则集 27、生命周期/回退 5、风险优先级 5、审批/豁免/会签 6、CI 一致性 2、Hook/doctor/MCP Resources 5、审计脱敏 canary 1）。

```bash
cd biz-guard && source ../.venv/bin/activate
python -m pytest -q tests/test_policy_lifecycle.py tests/test_risk_decision.py tests/test_approval_workflow.py tests/test_ci_parity.py tests/test_hook_doctor_resources.py tests/test_benchmark.py
# 断言：至少 51 passed；27/27 新规则 Diff、12/12 决策任务与 Golden 一致；
# critical 违规全为 BLOCK；缺测试全为 ALLOW_WITH_TESTS；关键未知全为 REQUIRE_APPROVAL；
# 过期豁免无效，审批不可用保持 pending，绝不自动 ALLOW。

python -m bizguard.ci.check --diff bench/fixtures/phase5/cross-service-dto-breaking.diff --base-revisions bench/fixtures/phase3-revisions.yaml --json
# 断言：decision=REQUIRE_APPROVAL（该 fixture 的唯一 Golden 决策）；输出 required_tests、required_approvers、
# evidence 与 audit_event_id；与本地 validate 输出结构化规范化后相同。

python scripts/run_benchmark.py --dataset bench/ablations/tasks.yaml --offline --out .artifacts/benchmark.json
# 断言：5 个 baselines 均运行，且至少一条真实 Agent 经 MCP 的本地 fixture 轨道完成；每条结果保存 agent/model/prompt/BizGuard/revision 版本；
# JSON 包含任务数、Critical Violation Recall、Unsafe Allow Rate、Impact Recall、成本和耗时，
# 缺任一版本字段或任一 baseline 即退出非 0。

python -m pytest -q
# 断言：至少 195 passed；旧 32 项和 14 fixture 全部通过。
```

端到端闭环命令：

```bash
cd biz-guard && source ../.venv/bin/activate
./scripts/verify_install.sh --offline --fixture bench/fixtures/phase5/dynamic-mapper.diff
# 断言：依次验证 MCP 连通、prepare、Fast Check、CI Slow Check；最终为 REQUIRE_APPROVAL，
# 一个聚合审批项路由至 Golden Owner；审计输出不含密钥、全文敏感知识或预置 canary 字符串。
```

### 防返工清单

- Policy 只有在 deterministic validator、Owner、修复路径、shadow/warning 样本、回退操作均具备时才可 blocking；功能写完不代表可启用阻断。
- 风险分只排序，不能覆盖硬规则和未知边界。决策顺序固定为 critical block → 关键未知/版本问题 → 测试证据 → 公共契约/多 Owner → 风险分。
- 豁免必须 scoped、限时、有理由和补偿控制；不修改 Policy，不迁移到下一次变更。
- CI 用独立进程从 base revision 重算；不得信任本地、Agent 或 MCP 声称的“已通过”。
- benchmark 的 Agent Only/RAG Only 等组必须在相同任务、模型、Prompt、commit 和运行环境下执行；否则只称“示例”，不可比较。
- 审批创建、重试与审计关联以 `(change_context_id, policy_revision, approver_set)` 幂等；重复 Hook/CI 触发不得产生重复审批项。
- `doctor/connect/init`、Hook 与 MCP Resources 的安装、无连接和降级路径都需 Golden 验证；审计测试注入 canary，命中即失败。

P5 简历检查点：可独立展示五组消融、真实 Agent MCP 轨道与 Unsafe Allow Rate，并说明所有指标对齐 v3 §18；若实现时裁剪指标，须在 benchmark README 逐项声明原因与不可比较范围。

## 10. 全程回归、提交策略与完成定义

每个 Phase 采用三段式提交顺序：`fixtures + Golden + verifier` → `最小实现` → `入口/文档`。第一段没有通过时，不进入第二段；每次合并请求都附带基线命令、新增 suite 命令及 E2E 命令的原始输出摘要。

| 守护项 | 强制规则 |
| --- | --- |
| MVP 回归 | 32 个既有测试与 14 个既有 fixture 的名称、输入与结果不变；若必须调整，另起 ADR 并获人工批准 |
| 静态质量 | `ruff check src tests agents_mcp` 与 `mypy src tests agents_mcp` 均为退出码 0 |
| 离线可复现 | 所有 Golden、impact、决策和 benchmark 主路径不依赖网络或真实模型；联网 embedding 仍是隔离可选测试 |
| Schema 稳定 | 对 CLI/MCP/CI 的 JSON 建立快照与兼容版本；破坏性改动必须提供兼容 adapter |
| 安全 | fixture、日志、审计和 Context Pack 禁止包含 `.env`、密钥、生产数据导出或未经授权的知识 |
| 性能 | 每 Phase 只采集 P50/P95 与样本数；达到足够样本前不设置/宣传阈值，Fast/Slow 预算分开报告 |

测试数链条按新增 suite 的最低数计算，且每一节点都包含此前所有测试：**32（MVP）→ 42（P0，+10）→ 60（P1，+18）→ 84（P2，+24）→ 114（P3，+30）→ 144（P4，+30）→ 195（P5，+51）**。测试数是最低通过数，不以删减旧测试或合并用例抵消新增覆盖。

收尾的 9–11 天只用于六场景演示脚本、Golden 级联重验、跨入口一致性、离线复现和回归修复；不引入 v3 之外的新能力。各 Phase 的防返工条目触发时，必须从受影响 Golden 的生产阶段起，重跑其全部下游 verifier、E2E、全量测试与静态检查。

完整版的“开发完成”判定为：P0–P5 的所有 Golden verifier、各自 E2E、全量 pytest、ruff、mypy 均满足本计划的明确断言；8 个 MCP Tool、MCP Resources、Agent Hook/doctor 与 CLI/CI 对同一变更产生一致的结构化核心结论；离线 benchmark 保存五组完整可重放结果和一条真实 Agent MCP 轨道；并能演示六个北极星场景。指标按 v3 §18 记录，若有裁剪必须声明。**这不等于生产 Blocking 已获批准。** 团队启用仍须按 v3 的 `shadow → warning → blocking`，基于真实样本确认 False Block Rate、修复时长、审批负担、索引新鲜度和回退演练结果。
