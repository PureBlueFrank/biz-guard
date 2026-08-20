# ADR 0001：Java 分析器采用 tree-sitter 轻量路径

状态：已接受（2026-08-20）

## 背景

BizGuard 需要从离线、脱敏 Java fixture 提取 Spring 符号和跨服务契约证据。JavaParser/JDT 能提供更丰富的语法或类型绑定；不过 JDT 的完整类型绑定要求准确 classpath、依赖解析和构建模型，离线、跨仓场景会把缺失依赖误报成确定事实。tree-sitter 不做全量类型推断，适合增量解析类、方法、字段和注解，但无法可靠推导动态 Mapper 与运行时反射边。

## 决策

固定采用 **tree-sitter + 契约解析 + 人工标注 Mapper 边**。tree-sitter 索引 Java/Spring 的语法事实；OpenAPI/Proto 解析器提供公开 API/RPC 的确定事实；MyBatis/dynamic Mapper 仅由显式人工 catalog 边补充，未标注边必须输出未知边界。**不引入 JDT 全量类型绑定。**

## 版本冻结

| 工具 | 固定版本 |
| --- | --- |
| Python | 3.12 |
| tree-sitter | 0.24.0 |
| tree-sitter-java grammar | 0.23.5 |
| JDK | 17.0.15 |
| Maven（有安装时） | 3.9.9 |
| Gradle（有安装时） | 8.10.2 |

离线 fixture 使用 JDK 17 的本地 `mvnw` 兼容入口；它不下载 wrapper、插件或依赖。升级上述版本须新建 ADR、保留旧 catalog revision，并重验下游 Golden suite。

## 后果

获得可重现的轻量离线索引，代价是不会虚构全局类型或动态映射事实；此类缺口由 `UNKNOWN_BOUNDARY` 和人工责任承担。
