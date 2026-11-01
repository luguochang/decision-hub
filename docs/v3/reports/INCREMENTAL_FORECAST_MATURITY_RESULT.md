# 第一份真实到期评分与调度边界

2026-10-27，承接已交付的原版ETH4h研究预览。该结果是单份研究的事后评分实证，不是四周评分完成或金融校准资格。

Run为run_be0d276c61d5446b9527f145c0f46969，目标北京时间02:27–06:27。Root通过原owner认证GET `/v1/decision-settings/research/results/{run_id}`，最初HTTP200/status=pending、forecast_result_not_recorded。源码确认结果采集接在原realtime调度器；本预览原角色仅Host/API/MCP/acquisition/research，没有启动realtime。原研究worker保留Study复核政策，不代表结果采集调度器也已运行。

Root在同一私有WORK使用原PerpetualForecastRuntime.process_due(scoring_version=v2)执行一次数据采集。未迁移数据库、重发研究或产生订单；模型关闭，沿原私有写入/网络guard，只允许已有公开数据代理。原组件实际核对唯一已到期Run，通过原capture/archive/parser/immutable scoring链新增collecting→observed experience→recorded；实际returncode0，17.2405039秒。模型/提案/概率原件未改。

采集后同一个原GET实际HTTP200/status=observed。实际分类down，终点一分钟交易价收盘收益率为-0.00515055467511885895404120443740095087163232963550（约-0.52%）；原方向权重0.4/0.2/0.4对应Brier0.56、log loss0.91629073187415506518352721176801107145010121990826。Root用Decimal独立重算三类Brier、log loss及阈值分类一致，target_ref与采集前相同。没有将并列40%的方向权重称为预测准确或获利。

原设置页的“读取到期结果”按钮已实际点击，显示上述真实结果、Brier、log loss和逐样本可靠性记录，未改前端。截图位于D:/DH-eth4h-live14/artifacts/forecast-collection01/forecast-score-original-ui.png。界面保留单样本不代表校准、不是交易盈亏和金融资格未通过的口径。

证据：

- D:/DH-forecast14-live-result-read01/read-receipt.json：采集前200/pending。
- D:/DH-eth4h-live14/artifacts/forecast-collection01/terminal.json、result.json：原组件一次采集真实终态及新增原件。
- D:/DH-forecast14-live-result-read01/after-collection/read-receipt.json、response.body：采集后200/observed，body SHA99844e82caaa77daa8252d8972460803214dc84a8c0e7affa4e1653c9eef2755。
- D:/DH-forecast14-live-result-read01/result-arithmetic-check.json：独立算术与同target验证。

## 未完成与下一步

这次是一次性补采，不是持续评分服务已启用。正式启动方案还须包含原realtime调度角色及相应来源、预算和生命周期；不能拿本次手动tick冒充四周自动报表。没有因预览2/3健康状态而虚标全链路通过。真实PAPER成交仍未发生。

UTC授时再次只读复测：Windows仍Leap3/stratum0/Local CMOS Clock、无成功同步；2026-10-27T00:07:32Z系统时点附近NTP偏差为+5.1518251、+5.1376235、+5.1425937秒。原字节在D:/DH-clock-readiness-20261027/observation.json及四份raw中。没有调整系统时间或给历史known_at补偏移。本次研究/评分事实保留，但不能计入已合格的绝对UTC认证、14自然日或四周目标。

当前独立PCE agenda复核C0/I0/M0；FOMC expected阶段和PAPER全期限仍在隔离实现/复核，尚未Main采用。全部56任务/9阶段、六组合长期运行、真实事件/提醒后继、10笔PAPER退出重启、四周评分、30个完整历史失败回放、最终质量与正式交接仍未完成。目标ACTIVE，不新增ACCEPTED。
