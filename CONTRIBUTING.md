# 贡献规范

## 命名与类型

- 函数、变量和模块使用 `snake_case`；类使用 `PascalCase`；机器可读 ID 使用小写 `kebab-case`。
- 所有公开函数必须显式声明返回类型。
- 使用 Python 3.12+，每行最多 100 个字符。

## 目录职责

- `src/bizguard/`：唯一的核心业务包与共享决策逻辑。
- `agents_mcp/`：MCP 协议适配层；不得承载 Policy 判断。
- `registry/`：字段影响契约注册表。
- `policy/`：机器可读不变量的唯一事实源。
- `knowledge/`：仅解释背景和证据，不定义规则。
- `sample/`：脱敏微服务样例。
- `tests/`：确定性自动化测试。
- `scripts/`：可重放的本地演示脚本。

## 核心铁律

- CLI 和 MCP 都只能调用 `evaluate_change(diff_text: str) -> ChangeSafetyCard`，不得复制决策逻辑。
- 确定性优先：Policy 使用 AST 或正则判断，禁止使用 LLM 判断。
- 未检查、解析失败、检索为空或 Policy 未覆盖时必须是 `CHECK_INCOMPLETE`，不得默认 `ALLOW`。

## 提交与测试

提交前必须同时满足：`pytest` 全绿、`ruff` 零警告、`mypy` strict 零错误。

```bash
ruff check .
mypy src tests
pytest -q
```
