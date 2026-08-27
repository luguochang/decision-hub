# Hub API

## 目的
提供 `/v1` REST Query/Command API，并在生产同源提供 Decision Desk 静态资源。R1 额外公开只读来源/产品健康和单 owner `/v1/pilot/readiness`，以及显式启用时的受限手动 poll 入口。

## 不负责
不在 route 内实现领域判断；不得暴露 SQL、LangGraph checkpoint、secret 或原始模型 response。Readiness 只调用 `pilot_runtime` 的脱敏报告。

## 运行
`uv run uvicorn apps.hub_api.main:app --host 127.0.0.1 --port 8000`。`/v1/sources` 和 `/v1/health` 可读取 source health；`POST /v1/sources/{source_id}/poll` 只有 `DECISION_HUB_SOURCES_ENABLED=1` 才可用，并仍通过 Kernel admission/R0 graph/Gate 执行。

## 最近验证
`tests/e2e` 和 `tests/pilot` 覆盖文本 admission、PIT、Graph、Gate、Artifact、Outcome/Evaluation、Inspector、Provider 失败安全、source poll 和 readiness；生产启动使用 Alembic `upgrade head`，测试注入的 Database 才使用 `create_all`。来源 adapter 的原始 HTTP/Provider JSON 不会作为 API DTO 返回。
