# BizGuard

BizGuard 是用于确定性业务变更安全检查的项目骨架。

## Diff 执行前提

BizGuard 必须在包含样例基座文件的仓库内运行。对受 Policy 保护的文件，检查器会把
unified diff 应用到仓库当前基座的内存副本，以重建**变更后完整文件文本**，随后再执行
AST 校验；它不会修改工作区。若 diff 不能应用到当前基座，结果为 `CHECK_INCOMPLETE`，
不会猜测或放行。
