# Hub API

## 目的
提供 `/v1` REST Query/Command API，并在生产同源提供 Decision Desk 静态资源。

## 不负责
不在 route 内实现领域判断；不得暴露 SQL、LangGraph checkpoint 或原始模型 response。

## 运行
`uv run uvicorn apps.hub_api.main:app --host 127.0.0.1 --port 8000`。

## 最近验证
R0 scaffold。
