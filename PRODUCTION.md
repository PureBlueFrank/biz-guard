# BizGuard 生产运行手册

BizGuard 的生产服务基线是：Streamable HTTP MCP、OIDC/JWT、PostgreSQL 共享状态、真实 Embedding、组织治理制品和可重放的 CI 证据。仓库内的 Demo 可用于展示，但内置的业务策略、Owner、阈值和公钥都不是任何组织的生产批准。

## 1. 生产边界

代码已提供以下生产能力：

- OIDC JWKS 签名、issuer、audience、时间和 scope 校验；
- PostgreSQL 审批与 Context Pack 存储，审批状态在事务内加锁并写入审计事件；
- 智谱 `embedding-3` 批量调用、重试、结果校验和原子缓存；
- 签名真实样本、Owner 批准和回退演练驱动的 Policy 升级门禁；
- 支持多实例的存活/就绪检查与可重放 CI 决策证据。

实际上线仍必须由组织提供真实仓库、治理制品、OIDC 租户、PostgreSQL、Embedding 凭据、签名校准样本和值班/备份制度。不得用 Demo 证据替代这些输入。

## 2. 部署前提

- Python 3.12；CI 执行 Java catalog 测试时安装 JDK 17。
- 受管仓库和治理目录以只读方式挂载。
- PostgreSQL 17 或组织支持的兼容版本，开启备份、恢复演练和容量监控。
- OIDC 发行方为 HTTPS，可提供 JWKS，为 BizGuard 分配独立 audience 和最小 scope。
- 智谱 API Key 由密钥管理系统注入，不出现在仓库、镜像、日志或进程参数中。
- 公网入口在反向代理或网关终止 TLS；容器参考配置仅绑定 `127.0.0.1:8000`。

## 3. 组织治理制品

生产 HTTP 模式不回退到镜像内的 Demo 数据。挂载目录必须至少包含：

```text
governance/
├── catalog.yaml
├── policy-registry.yaml
├── contracts.yaml
├── invariants.yaml
├── calibration-gates.yaml
├── calibration-public-key.pem
└── knowledge/
    ├── published/
    └── invariants/
```

`catalog.yaml` 中的仓库、能力、Owner 和 required test 命令必须映射到真实项目。`policy-registry.yaml` 的 `file_patterns` 决定 Validator 作用范围。治理目录应由受保护的配置仓库或校验摘要制品发布，并与 CI 使用同一版本。

仓库内 `policy/calibration-public-key.pem` 只是格式样例，必须替换。生产私钥在仓库外生成并交给组织签名服务：

```bash
openssl genpkey -algorithm ED25519 -out calibration-private-key.pem
openssl pkey -in calibration-private-key.pem -pubout -out calibration-public-key.pem
chmod 600 calibration-private-key.pem
```

私钥不得复制到 BizGuard 代码库或运行容器。

## 4. 必需配置

| 变量 | 用途 |
| --- | --- |
| `BIZGUARD_REPOSITORY_PATH` | Compose 宿主机上的受管仓库根目录 |
| `BIZGUARD_GOVERNANCE_PATH` | Compose 宿主机上的组织治理目录 |
| `BIZGUARD_DATABASE_URL_FILE` | 仅含 PostgreSQL DSN 的文件 |
| `BIZGUARD_DATABASE_PASSWORD_FILE` | Compose 内置 PostgreSQL 密码文件 |
| `BIZGUARD_ZHIPU_API_KEY_FILE` | 仅含智谱 API Key 的文件 |
| `BIZGUARD_AUTH_ISSUER_URL` | HTTPS OIDC issuer |
| `BIZGUARD_AUTH_JWKS_URL` | HTTPS JWKS endpoint |
| `BIZGUARD_AUTH_AUDIENCE` | BizGuard 专用 audience |
| `BIZGUARD_REQUIRED_SCOPES` | 逗号分隔的必需 scope，默认 `bizguard:use` |
| `BIZGUARD_RESOURCE_URL` | 对外 MCP URL，例如 `https://bizguard.example.com/mcp` |
| `BIZGUARD_ALLOWED_HOSTS` | HTTP Host 白名单 |
| `BIZGUARD_ALLOWED_ORIGINS` | 需要浏览器访问时的 Origin 白名单，否则留空 |
| `BIZGUARD_CALLER_IDENTITY` | 非 HTTP/内部任务的默认审计主体 |
| `BIZGUARD_CALLER_ROLES` | 非 HTTP/内部任务的最小 ACL 角色集 |

HTTP 请求的身份、角色和 scope 从已验证 JWT 中提取。静态 Token 仅保留给显式开启 `BIZGUARD_ALLOW_STATIC_AUTH=true` 的本地 Demo，不属于生产基线。

参考密钥文件：

```text
secrets/database-password.txt     # 随机高强度密码
secrets/database-url.txt          # postgresql://bizguard:<URL 编码密码>@postgres:5432/bizguard
secrets/zhipu-api-key.txt         # 真实 API Key
```

三个文件设为 `0600`；DSN 与 PostgreSQL 密码必须一致。生产更推荐使用托管 PostgreSQL 和编排平台的 secret provider，Compose 是可重现参考而非 HA 方案。

## 5. 启动与就绪验证

```bash
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

`/healthz` 只证明进程存活。`/readyz` 会同时探测审批存储和 Context Pack 存储，任一 PostgreSQL 检查失败都应让实例退出流量。MCP 请求必须携带适用于 issuer/audience 的 Bearer JWT；签名、scope 或时间声明不合格必须返回 401/403。

启动前从与生产相同的密钥和网络环境运行：

```bash
bizguard doctor --production --json
```

该命令会校验配置、治理文件、仓库、PostgreSQL、OIDC JWKS 可达性和一次真实 Embedding 调用。涉及外部服务的检查不得用 mock 结果代替上线验收。

## 6. Policy 校准与升级

新 Policy 只能从 `draft` 或 `shadow` 起步。每次只允许提升一阶：`draft → shadow → warning → blocking`。更改 Validator、scope、severity、Owner、required tests、file patterns 或 precision 后，Policy 必须回到 shadow。

每个校准 observation、Owner approval 和 blocking 所需的 rollback drill 都使用组织 Ed25519 私钥分别签名。证据中的 revision、decision fingerprint、人工标签和审计 URI 必须来自真实受管变更。默认阈值至少要求 30 个样本、10 个正样本和 10 个负样本；组织可在 `calibration-gates.yaml` 中收紧，不应在待审 PR 中放宽。

本地预验证：

```bash
bizguard policy calibration verify \
  --bundle policy/calibration/<policy-id>.json \
  --registry policy/phase5-registry.yaml \
  --gates policy/calibration-gates.yaml \
  --public-key policy/calibration-public-key.pem \
  --json
```

CI 会使用可信基线中的校验器、阈值和公钥审查 registry diff，缺少或篡改证据都会关闭放行。只有 high-precision 的确定性 Validator 才能进入 blocking，且必须有当前窗口内签名、成功的回退演练。

## 7. CI 真实证据

生产门禁使用可信 runner，不接受调用方声称 `tests_passed=true`：

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

Runner 先计算 `required_tests`，再不经 shell 执行 catalog 命令，为每个 Test ID 生成绑定 baseline revision 的哈希证据，最后重算决策。未执行、失败、超时、路径越界或 revision 不一致都不能形成 `ALLOW`。

GitHub 仓库应将 `verify` 和 `gate` 设为 `main` 的必需检查，禁止绕过。工作流需在 pull request、main push 和手动重放三种事件上成功。发布前保留 `/tmp/bizguard-result.json`、审计日志和测试证据制品。

仓库自身的参考工作流只将 `fixtures/java-microservices` 和 `sample` 视为受 Demo catalog 管理的业务路径；平台源码由 `verify` 和 Policy registry 签名治理门禁检查。接入组织仓库时必须把该路径列表替换为 catalog 中真实受管仓库，不得因沿用 Demo 路径而跳过业务变更。

## 8. 扩容、备份与回退

- 所有实例指向同一 PostgreSQL 主库；不挂载共享 SQLite 卷。
- 优雅停机时先从负载均衡移除实例，再等待在途请求完成。
- 每日备份 PostgreSQL，按组织 RPO 配置 WAL/PITR；在独立环境定期演练恢复并记录 RTO。
- Embedding 缓存可重建，不代替治理原文备份。Provider 不可用时生产检索关闭放行，不静默切换成本地词法向量。
- 紧急回退优先将相关 Policy 降为 `warning`，保留 registry diff、原审批和审计记录；不删除已注册 Policy 来规避门禁。

## 9. 上线验收清单

以下项目全部满足后才可宣布“生产启用”：

- [ ] 真实仓库只读挂载，catalog/contract/invariant 覆盖抽样验收通过。
- [ ] 真实 OIDC JWT 正常通过，错误 issuer/audience/scope、过期 token 和未知 `kid` 均被拒绝。
- [ ] 至少两个服务实例并发审批验证通过，不丢失会签、状态或审计事件。
- [ ] PostgreSQL 备份、PITR/恢复演练、连接池与容量告警通过。
- [ ] `doctor --production` 在上线网络中全部为 `ok`，真实 Embedding 召回集达到组织阈值。
- [ ] 每个启用 Policy 都有真实签名样本和 Owner 批准；blocking Policy 另有回退演练。
- [ ] GitHub `verify`/`gate` 已设为必需检查，PR、main push 和手动重放均留存证据。
- [ ] 监控、告警、值班、密钥轮换、故障降级和回退手册已由负责人签字。

项目自检命令：

```bash
python -m ruff check src tests agents_mcp scripts
python -m ruff check --select D101,D103 src/bizguard agents_mcp
python -m mypy src tests agents_mcp
python -m pytest -q
./scripts/verify_install.sh --offline
```
