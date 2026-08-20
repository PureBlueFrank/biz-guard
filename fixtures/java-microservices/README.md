# 离线 Java fixtures

三个脱敏的 Java 17 微服务骨架：`coupon-core` 覆盖 Controller、Service、DTO、JPA 实体与 MQ producer；`merchant-service` 覆盖 RPC 客户端和消费者 DTO；`coupon-contract` 提供 Proto 与 OpenAPI 契约。

本机没有 Maven 或 Gradle，因此每个仓库包含一个最小 `mvnw` 兼容入口。它在 `--offline` 下调用 JDK 17 的 `javac`，对仓内源码做真实编译，并且不下载任何依赖；Spring/JPA 注解以脱敏的本地编译桩表示。各仓库仍保留零依赖 `pom.xml`，在具备 Maven 的环境可作为标准 Maven 项目导入。运行 `../../scripts/verify_java_fixtures.sh --offline` 验证全部仓库。
