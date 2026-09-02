# ADR-0015：事件准入、首次同步成本保护与 Run 优先级

日期：`2026-09-01`
状态：`accepted`
决策所有者：`owner`
关联任务：[E2L-01](../stages/E2L_01_EVENT_ADMISSION_COST_PRIORITY.md)

## 背景

全新 live 实例把 Fed 页面固定文案误判为高影响事件，首次同步又自动建立了历史
research backlog。research worker 使用 FIFO，导致从官方 DSH 页面提交的手动任务被
自动任务饿死。继续增加排除词、只清理本地数据库或在 worker 中硬编码 `dsh-web`
来源都不能形成可维护的产品边界。

## 决策

1. 自动 discovery 只使用 Feed 标题或未来 canonical 结构化标题，不使用全文正文；
2. `bootstrap_latest` 首次同步只推进来源 cursor，不建立 Observation 或 Run；历史
   回填必须是显式、可预算的 owner 操作；
3. `bls-calendar` 默认不激活，显式启用也不按标题自动创建昂贵 research；真实到期
   调度另立任务；
4. Run 账本增加不可变 `admission_origin` 与 `priority`：manual=100、automatic=50、
   scheduled_recheck=10、legacy=0；
5. durable claim 顺序为 priority DESC、available_at 到期、created_at ASC、run_id ASC；
6. DSH 和 Query/View 通过 canonical schema 展示人可读来源与优先级；
7. 历史 Run 迁移为 legacy/0，不修改任何历史业务数据。

## 否决项

- 否决通过排除词黑名单持续修补误判；
- 否决 fresh install 自动历史回填；
- 否决 Redis/Celery/第二张队列表；
- 否决由模型、Agent 或插件决定或修改优先级；
- 否决在 SQL 中根据 `source_id` 临时推断优先级；
- 否决删除错误历史 Run 以制造干净验收结果。

## 后果

正面：fresh install 不会隐式烧模型费用，手动任务不会被自动历史任务饿死，事件来源
和调度顺序可审计、可迁移、可在人类页面解释，并能被未来 Domain Pack 复用。

负面：首次启动不主动研究 Feed 中已有条目；需要研究历史事件时必须显式 backfill。
Run schema、migration、Query/View 和 DSH 插件需要一次兼容升级。

## 回滚

应用可回滚到旧 claim 逻辑，但不得删除 0025 已增加的字段或改写已创建 Run。真实数据
不执行破坏式 downgrade；若新顺序异常，停止新任务、保留账本并恢复锁定应用版本。
