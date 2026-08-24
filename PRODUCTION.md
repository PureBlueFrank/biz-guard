# BizGuard 生产运行手册

BizGuard 可以作为单实例、持久化的 Streamable HTTP MCP 服务运行。新团队首次接入必须使用 `shadow` 模式；只有经过真实样本校准、误报审核和回退演练的 critical Policy 才能进入 blocking。

## 一、部署前提

- Python 3.12；如果 CI 需要执行 Java catalog 测试，同时安装 JDK 17。
- 将受管仓库以只读方式挂载到容器。
- 将经过 Owner 审核的组织治理目录以只读方式挂载；生产模式不会回退到镜像内的 demo 配置。
- 为审批和 Context Pack 提供持久卷，并纳入备份。
- 通过反向代理提供 TLS；容器默认只发布到主机 `127.0.0.1:8000`。
- 为每个服务实例配置唯一 Bearer Token、身份和最小角色集。当前内置认证模型适合“每个主体一个实例”；多租户部署应在反向代理或组织 OAuth 层完成令牌签发和实例隔离。

## 二、必需配置

| 变量 | 用途 |
| --- | --- |
| `BIZGUARD_API_TOKEN` | 至少 32 字符的 Bearer Token |
| `BIZGUARD_CALLER_IDENTITY` | 服务端认证主体，用于审批记录 |
| `BIZGUARD_CALLER_ROLES` | 逗号分隔的 ACL 角色，必须最小化 |
| `BIZGUARD_REPOSITORY_PATH` | Compose 宿主机上的受管仓库目录 |
| `BIZGUARD_GOVERNANCE_PATH` | Compose 宿主机上的组织治理目录 |
| `BIZGUARD_ALLOWED_HOSTS` | MCP 服务 Host 白名单 |
| `BIZGUARD_ALLOWED_ORIGINS` | 需要浏览器访问时的 Origin 白名单，否则留空 |
| `BIZGUARD_AUTH_ISSUER_URL` | 认证发行方 URL |
| `BIZGUARD_RESOURCE_URL` | 对外 MCP resource URL，例如 `https://bizguard.example.com/mcp` |

Token 应由密钥管理系统注入，不写入仓库、Compose 文件或日志。

治理目录必须包含以下结构，内容需要替换为受管业务的真实仓库、Owner、Policy、测试命令和知识资料：

```text
governance/
├── catalog.yaml
├── policy-registry.yaml
├── contracts.yaml
├── invariants.yaml
└── knowledge/
    ├── published/
    └── invariants/
```

HTTP 模式会检查这 6 项是否被显式配置且可读，缺失时进程直接启动失败。`catalog.yaml` 中的 required test 命令会被 CI runner 实际执行，因此治理目录必须与门禁使用同一版本，并通过只读制品或受保护配置仓库分发。

## 三、容器启动

```bash
docker compose build
docker compose up -d
docker compose ps
```

`compose.yaml` 会强制提供上述变量，以非 root 用户运行，移除 Linux capabilities，使用只读根文件系统，并将审批/上下文写入 `bizguard-state` 持久卷。

验证：

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

MCP 请求必须携带 `Authorization: Bearer <token>`。未携带或错误 Token 必须返回 HTTP 401。

## 四、CI 真实测试证据

生产门禁使用可信 runner，不得使用布尔参数声称“全部测试完成”：

```bash
python -m bizguard.ci.runner \
  --diff /ci/pr.diff \
  --base-revisions /ci/base-revisions.yaml \
  --repository-root /workspace/repos \
  --test-root /workspace/repos \
  --test-evidence-out /ci/test-evidence.json \
  --audit-log /ci/audit.jsonl \
  --json
```

Runner 先计算 `required_tests`，再在 catalog 指定的仓库中不经 shell 执行命令，为每个 Test ID 生成与 baseline revision 绑定的哈希证据，最后重算决策。任一测试未执行、失败、超时或仓库路径无效都不会形成 `ALLOW`。

CI Runner 还必须设置 `BIZGUARD_CATALOG_PATH`、`BIZGUARD_POLICY_REGISTRY_PATH`、`BIZGUARD_CONTRACT_REGISTRY_PATH`、`BIZGUARD_INVARIANTS_PATH`、`BIZGUARD_KNOWLEDGE_ROOT` 和 `BIZGUARD_INVARIANT_KNOWLEDGE_ROOT`，指向与服务端相同版本的治理制品。不要从待审 PR 分支执行可被 PR 自身修改的 BizGuard 引擎或治理文件；应使用固定版本、校验摘要的镜像/制品，并用分支保护把 gate 设为必需检查。

CI 需要消费审批时，将与 MCP 服务共享的状态卷挂载给自托管 Runner，并配置：

```text
BIZGUARD_APPROVAL_DB=/var/lib/bizguard/approvals.sqlite3
BIZGUARD_CHANGE_CONTEXT_ID=<prepare_change 返回的 context id>
```

SQLite 存储使用 WAL、FULL synchronous、30 秒 busy timeout 和进程内并发锁。当前只支持单服务实例；不得让多个 Pod 同时写同一 SQLite 卷。

## 五、上线门禁与回退

1. 在真实但非核心仓库使用 shadow，记录命中、误报、未知边界、测试耗时和审批负担。
2. 由 Policy Owner 复核 scope、证据和修复指引后进入 warning。
3. 只将具有确定性 Validator、误报达标、已演练豁免和回退的 critical Policy 升级为 blocking。
4. 出现索引落后、Provider 故障、审批存储异常或误拦时，立即将 Policy 回退到 warning，保留审计记录。

每次决策都会输出实际违规或关键未知的 `shadow_findings`；已通过的 shadow 检查不计为命中。指标聚合的 `shadow_hit_count` 是 finding 总数，`shadow_hit_rate` 是至少有一个命中的决策样本占比。这些命中只用于观察与误报校准，不改变决策；warning 命中转为审批，只有 blocking 的 critical 违规才会直接阻断。

## 六、生产自检

```bash
bizguard doctor --production --json
python -m ruff check src tests agents_mcp scripts
python -m mypy src tests agents_mcp
python -m pytest -q
```

`doctor --production` 必须返回 `production_config=ok`。新环境在完成 shadow 数据采集前，不得把“服务已启动”等同于“生产 blocking 已批准”。
