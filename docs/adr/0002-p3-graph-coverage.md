# ADR 0002：P3 图覆盖范围

P3 的图只声明当前 fixture 已实际生成的边类：`DECLARES`、`DEPLOYED_WITH`、`EXPOSES`、`SERIALIZES_TO`、`MAPS_TO`、`CALLS`、`PUBLISHES`、`CONSUMES`、`BELONGS_TO_CAPABILITY`、`OWNED_BY` 与可选的 `OBSERVED_CALL`。

其中 Java 类、字段、实体映射、Spring 注解和可解析对象调用由 AST 产生；反射映射与消息 topic 关系使用 `fixtures/java-microservices/bizguard-manual-edges.yaml` 中带 `source: manual` 的明确标注。枚举中其余边类是 schema 预留，不宣称 P3 已覆盖。

影响分析仅遍历快照中已有的边。标注 `dynamic=true` 且没有语义续边的节点产生 `UNKNOWN_BOUNDARY`，不会根据类名或路径文本猜测。
