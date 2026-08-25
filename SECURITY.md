# 安全政策

## 报告漏洞

请不要在公开 Issue 中提交可利用细节、密钥或受限业务知识。应通过仓库所在平台的私密安全报告渠道提交，并附上受影响版本、最小复现和预期影响。

## 支持范围

仅最新的 `main` 和最新发布版本接受安全修复。生产部署必须使用 OIDC/JWKS 验证的 Bearer JWT、服务端绑定的身份与角色、只读仓库挂载、PostgreSQL 共享存储和真实 Embedding Provider。

## 安全默认值

- HTTP 模式缺少 HTTPS issuer/JWKS、audience、scope、身份、角色、PostgreSQL、Embedding 凭据、组织治理制品或 Host 白名单时拒绝启动。
- 知识 ACL 只使用服务端认证角色，不信任 Tool 参数。
- CI 不接受“全部测试已完成”布尔断言，只接受按 Test ID 与 revision 绑定的执行证据。
- Policy 升级只接受组织 Ed25519 公钥验证的真实样本、Owner 批准和回退演练；示例公钥不具备生产资格。
- 解析、能力推断或影响分析不完整时保持非放行。
