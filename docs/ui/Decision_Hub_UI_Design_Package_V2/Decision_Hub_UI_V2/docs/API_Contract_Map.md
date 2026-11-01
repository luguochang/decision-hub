# UI 与 Hub API 对照：已知合同与待确认项

**依据仅为 S1 第 12–13 页。** 本轮没有访问当前后端，也没有推断出它的完整响应 schema。下表中的路径是原交接稿已列出的接口，不表示本原型已经接入、接口已联调成功，或任何新增功能已得到后端支持。

| 页面动作 | 原文列出的接口 | UI 需要的信息；实际字段待核对 |
|---|---|---|
| 首页快照 | `GET /v1/decision-desk/summary` | 选中 Run、摘要、更新时间与资源局部状态 |
| 健康状态 | `GET /v1/health` | Worker、mode、heartbeat、unknown / degraded |
| 研究列表 | `GET /v1/research/runs` | ID、状态、触发方式、覆盖、来源、父 Run |
| 提交研究 | `POST /v1/research/observations` | 空输入补默认意图；幂等性、返回 Run 身份 |
| 宏观事件 | `GET /v1/macro-events?scope=all|upcoming|history` | 事件 ID、来源、观察时间、各数值、修订版本 |
| Run 本体 | `GET /v1/research/runs/{id}` | 任务状态、父关系、失败与重试语义 |
| 用户报告 | `GET /v1/runs/{id}/view` | 方向、周期候选、执行限制、概率状态 |
| 分析链路 | `GET /v1/research/runs/{id}/decision-graph` | 节点和边、Evidence 引用、质量门、主反链 |
| 过程概览 | `GET /v1/research/runs/{id}/observability` | 公开运行摘要与阶段信息 |
| 结构化检查 | `GET /v1/research/runs/{id}/inspector` | 八模块所需结构化数据和来源 |
| 预测复盘 | `GET /v1/research/runs/{id}/forecast-audit` | 预测点、复盘时间、校准状态 |
| 资料列表 | `GET /v1/research-library/documents` | 文档、版本、状态、已关联 Run |
| 上传资料 | `POST /v1/research-library/documents` | 元数据、上传方式、返回文档 / 版本身份 |
| 解析块 | `GET /v1/research-library/versions/{id}/blocks` | 稳定 block_id、页码、原始文本、表格 / 图片 |
| 修正稿 | `GET /v1/research-library/versions/{id}/correction` | 原版本、修正块、风险与待确认项 |

## 不得凭设计稿补造的接口

S1 没有给出“确认修正稿”“重新解析”“启动语义修正”“质量复检”“资料关联 Run”“单独 Evidence 详情”“任务取消”“分享”“导出”“日历订阅”的具体写接口。也没有给出 DSH Session 与 Run 映射合同。本包不冒充已经支持这些后端能力，不编写猜测的 POST 路径。

事件抽屉可先由事件列表已返回对象组成；独立获取详情的合同待确认。Evidence 抽屉先由报告 / inspector 的证据集合定位；只有后端定义了单独 Evidence query 时再拆分请求。资料关联是否通过 observation payload 携带版本 ID 需确认，不能把原型参数直接发送。

## 前端适配层提案

`source/contracts.d.ts` 只是用于组件设计的语义视图类型，不是经核验的服务端 schema。推荐将实际 Query View 映射为组件需要的模型；不要把 DTO 散落到每张卡片里。名称、枚举、分页和时间精度都需在适配层核对。

当前 `source/fixtures.js` 明确只提供演示；`source/app.js` 的 `submitResearch`、`beginDoc`、`confirm-doc` 与计时器承担模拟。生产迁移应替换这些入口，而不是把模拟成功提示当作服务调用的完成结果。

## 联调前必须确认

需要确认 observation 请求体、幂等键载体和有效窗口、返回 ID 与冲突行为；研究状态轮询或事件推送能力；失败重试创建新 Run 还是同 Run attempt；权限 / Gate / 校准各自枚举；字段缺失与数据过期口径；来源权限及脱敏策略；版本并发校验；隐私可见范围；上传格式 / 大小；质量复检和引用版本绑定。

没有官方合同的“取消”和“订阅”不作为原型里的假完成操作。对于异步任务失联、超时或中断，只提示已知状态和恢复方式，不能把未确认的请求当作失败后立刻重建。

## 安全和数据边界

UI 只消费 Hub Query View 与已定义合同，不能直接读 SQLite、DSH JSONL、Worker 原始日志。不得展示 API Key、DSH token、Host secret 或 Session JSONL 全文。原型中 `research_only` 的权限显示不能替代服务端授权检查。

HTTP 请求取消（如 AbortController）不等于取消服务端研究任务。所有写请求应有服务端幂等或版本检查，并基于实际返回确认 UI。来源 URL 应做协议白名单与外链安全处理；没有来源链接时显示“未提供”，不要拼接假地址。
