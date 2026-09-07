# ADR-0021：DSH 官方 OpenTelemetry 可观测插件接入

日期：2026-09-02
状态：accepted（OBS-01 canary passed）

## 决策

Decision Hub 在 DSH profile 层以官方插件 seam 接入
`@loongsuite/dsh-plugin@0.1.2`，并锁定其 npm integrity、Apache-2.0 许可证、Node
要求与当前 DSH 上游版本。插件只负责 DSH 技术运行 Trace/Metric（入口、Agent、Step、LLM、
Tool、耗时、Token、错误和重试），输出到标准 OTLP/HTTP 后端；默认显式关闭正文采集
`captureContent=false`。

接入保持显式 opt-in：只有 `DSH_OBSERVABILITY_ENABLED=1` 时，
`infra/dsh/run-web.sh` 才通过 DSH 官方 `plugin --profile web add --save-exact` 安装该
包，并校验 profile manifest 与 pnpm lock 的 exact version/integrity。普通产品启动、Hub
Kernel、LangGraph、Domain Pack 和 DSH 上游源码不增加对该包的硬依赖。

## 背景

DSH Session JSONL/Trajectory 是完整的原生运行事实，Hub ResearchTraceEvent 是业务审计
事实，但二者都不是适合查看 LLM/Tool waterfall、TTFT、Token、重试和错误关系的技术 Trace。
用户提供的 LoongSuite 插件已经在 DSH 原生生命周期上实现了这层标准 OTel 投影，避免在
LangGraph 或 Hub 中重复实现 Span 协调器。

## 候选与否决项

- 采用独立 `@loongsuite/dsh-plugin`：兼容 DSH 官方 profile、后端可替换、无 SaaS 强绑定。
- 不把 LoongSuite Pilot 放进首个 DSH 运行时：Pilot 面向多 Agent 汇聚，当前只有 DSH 一个真实
  Agent，且与独立插件同时采集会造成重复 Trace。
- 不在 Hub SQLite 复制 OTel Span，也不让插件写 Evidence/Gate/Forecast/Outcome。
- 不把 Prompt、回复、工具参数/结果或 Provider key 发往远程 Trace；需要正文时另立 ADR。
- 不引入新的 Trace 业务表；第一版只通过 `dsh.session.id -> dsh_session_links -> run_id`
  做只读关联。

## 后果

- DSH 原生 JSONL、Hub 业务账本和 OTel 技术 Trace 三层所有权清晰，任一后端可替换。
- 本地 canary 不需要新的外部 Key；metadata-only OTLP 接收器和可选 Jaeger 均可在本机运行。
- 插件导出失败必须 fail-open；DSH、Hub Run、Gate、Evidence 和 Artifact 不得被遥测后端拖垮。
- Jaeger all-in-one 默认内存存储，只用于开发验证；长期个人资产需要另行选择持久化后端、
  保留期和访问控制。
- DSH 上游升级必须重新做 exact-version plugin canary；不能把安装成功等同于兼容通过。

## 受影响实现与测试

- `infra/dsh/run-web.sh`：显式 opt-in 安装与版本/integrity 校验。
- `infra/dsh/observability/plugin.lock.json`：外部包锁定元数据。
- `infra/dsh/observability/run-canary.sh`：replay、OTLP 捕获和 exporter-down 验收入口。
- `infra/dsh/observability/jaeger.compose.yaml`：本地 Jaeger 验证后端。
- `tools/otel_capture.py`：仅测试用的 OTLP/HTTP protobuf metadata 接收器。
- 退出证据：2026-09-02 partial-failure replay 产生 2 条 trace、17 个 Span，完整包含
  `enter_ai_application_system`、`invoke_agent`、`react step`、LLM、Tool 和
  `dsh.session.id`；OTLP endpoint 不可用时同一业务 Run 仍按 `rejected/insufficient` 完成。

OBS-02（TelemetryRef/Query View）和 OBS-03（Pilot 多 Agent 评估）保持独立后续任务，不能
因本 ADR 自动进入 active runtime 或改变金融 Gate。
