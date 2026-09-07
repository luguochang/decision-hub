# DSH Observability Plugin

本目录是 `OBS-01` 的隔离接入层，使用 DSH 官方 `dsh plugin` 命令安装
`@loongsuite/dsh-plugin@0.1.2`，把 DSH 原生 Session/Agent/Step/LLM/Tool 生命周期以
OpenTelemetry OTLP/HTTP 输出到可替换的本地后端。插件不写 Decision Hub 账本、不拥有
Evidence/Gate/Forecast/Outcome，也不替换现有 DSH Web 或 Hub 业务 Trace。

## 当前锁定

| 项目 | 值 |
| --- | --- |
| DSH | `0.1.2-alpha.2` / `0a53fb55bea101816fa226bb964ae2bed71c343b` |
| 插件 | `@loongsuite/dsh-plugin@0.1.2` |
| integrity | 见 `plugin.lock.json` |
| License | Apache-2.0 |
| 正文采集 | 强制保持 `captureContent=false` |
| 采集器 | 独立插件；不同时启用 LoongSuite Pilot |

插件通过官方 profile seam 安装到 `DSH_HOME/profiles/web`，不会复制到业务源码；上游
源码仍由 `infra/dsh/upstream.lock.json` 固定。只有显式设置 `DSH_OBSERVABILITY_ENABLED=1`
时 `run-web.sh` 才执行安装，普通 `run-product.sh` 不会增加该依赖。

## 无 Key 的可重复 canary

```bash
./infra/dsh/observability/run-canary.sh --scenario=partial_failure
```

该命令使用上游 `llm-replay` 作为离线输入，并启动本地 metadata-only OTLP 接收器；它
验证插件真实挂载、请求导出、失败场景不中断 Hub、以及默认不发送正文。运行产物写入
`tmp/dsh-observability-canary/`，不会进入 Git。

本地 Jaeger UI 是可选的（需要 Docker 已能拉取镜像）：

```bash
./infra/dsh/observability/run-canary.sh --jaeger --scenario=success
```

Jaeger UI 在 `http://127.0.0.1:16686`，OTLP/HTTP 在 `http://127.0.0.1:4318`。Jaeger
all-in-one 默认内存存储，只用于本地验证；长期 Trace 资产需另行选择持久化后端并新增
ADR，不把 Jaeger 数据目录提交到仓库。

## 运行时环境变量

插件沿用官方 OpenTelemetry 变量：`OTEL_SERVICE_NAME`、
`OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_HEADERS` 等。生产或远程后端的 header
只能来自 gitignored 环境变量/Secret Manager，禁止进入 profile、日志和业务数据库。

## 退出门

- 每个 DSH turn 形成可查询 Trace，至少包含 `ENTRY -> AGENT -> STEP` 及其 `LLM/TOOL` 子级；
- Tool failure、LLM retry、Subagent 和 shutdown flush 不改变 DSH/Hub 执行结果；
- exporter 不可用时 DSH、Hub Run、Gate/Evidence/Artifact 仍按原路径结束，只有 telemetry
  diagnostics 记录导出失败；
- `captureContent=false` 时 OTLP 后端没有 Prompt、回复、工具参数或工具结果正文；
- `dsh.session.id` 可与现有 `dsh_session_links` 只读关联；Trace 不成为金融 Evidence。

OBS-02（TelemetryRef/Query View）和 OBS-03（LoongSuite Pilot 多 Agent 采集）不在本目录
实现；只有 OBS-01 通过并出现第二个真实 Agent 后才分别立项。
