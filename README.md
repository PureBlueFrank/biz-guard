# BizGuard

离线 benchmark 中的 “Naive Baseline” 是基于 diff 内容的启发式基线，并非真实 LLM agent；只有 `--live` 且配置真实 agent 命令时才会运行真实 agent。

BizGuard 是用于确定性业务变更安全检查的项目骨架。

## 离线安装与检索限制

离线验收前须在虚拟环境或内部包源预置构建依赖（当前为 `hatchling`），然后执行
`pip install --no-build-isolation -e .`；常规 `pip install -e .` 会创建隔离构建环境，可能尝试联网下载该依赖。

CLI 的 `knowledge search` 在本地使用词法向量适配器时会明确标记为降级结果。该模式适用于本地开发；CI 与生产验收必须配置真实 embedding，不能将降级检索当成等价结果。

## Diff 执行前提

BizGuard 必须在包含样例基座文件的仓库内运行。对受 Policy 保护的文件，检查器会把
unified diff 应用到仓库当前基座的内存副本，以重建**变更后完整文件文本**，随后再执行
AST 校验；它不会修改工作区。若 diff 不能应用到当前基座，结果为 `CHECK_INCOMPLETE`，
不会猜测或放行。
