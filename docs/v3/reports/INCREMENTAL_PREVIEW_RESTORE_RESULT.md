# 原前端维护恢复实测

2026-10-27 13:24 +0800 后续维护覆盖：旧r3预览已真实关闭，七个所属Job清理完成且产品端口无监听。新17源已采用、Main49例通过，唯一normal27841运行中；下文11:32及更早在线记录保留为历史。当前以[三族接线进展](INCREMENTAL_THREE_EVENT_FAMILY_MAIN_INTEGRATION.md)为准，原报告可继续离线阅读。

2026-10-27 11:32 北京时间。原 Decision Hub 预览已恢复，沿用原界面、owner、账户、数据库、设置与历史报告。本地试用仍未达到整产品交付条件。

## 运行和操作入口

- 当前原工作台：http://127.0.0.1:39142/。身份过期时从原私有本机快捷方式恢复；带配对口令的地址不写入本文。
- 已验证历史报告：http://127.0.0.1:39142/?run_id=run_be0d276c61d5446b9527f145c0f46969#/report?run=run_be0d276c61d5446b9527f145c0f46969。
- 六角色API/acquisition/MCP/Host/realtime/research由同一受管controller恢复；Root session86756、outer3108、creation FILETIME134375443675345354，实际UTC03:12:47.4966142Z启动。保持运行。
- WORK为D:/DH-eth4h-live14，原workspace 9621f469-bb5b-4a4f-bf1c-1204450183a9、created=false；新profile web-event-paper-20261027，原web17文件和SDK会话保留。
- 精确停止：D:/DH-work14-actual-release-inputs01/Stop-PAPER-Preview-Resume.ps1。此脚本仅匹配本次outer PID及创建时间并写本次停止信号，已语法核对、未执行。勿用旧脚本或全局结束Python/Node。

## 本次真实验证

| 项目 | 实际结果 |
| --- | --- |
| 原normal与postflight | 均实际exit0；normal 1f1d2d3c…3852、native f0025cc8…a6d4、current 159f39ee…f1f1 |
| r3恢复门 | 非作者C0/I0/M0；binding SHA307ededb5205918b2fa4df2600956f1e9b45998550a6dc7a8dd76385f8f4dfe0 |
| 恢复前备份 | 关闭状态27文件before/copy/after SHA一致；owner凭据仅就地hash，未复制或打印 |
| 六角色所有权 | 各PID加创建时间独立确认在线；未重跑initialize/bootstrap/研究POST |
| 历史报告原GET | 200，50.429938秒，1,054,320字节；18不可变字段、source/artifact原文和publication保持 |
| 历史评分原GET | 200，0.319305秒；11不可变字段及真实到期结果保持，仅read_at变化 |
| 原页面 | 实际显示独立审核、40/20/40及原到期评分；页面链路仍2/3 |
| 浏览器JSON/HTML | 两按钮实际下载；和维护前阅读JSON的全部差异仅/report/read_at；HTML内嵌JSON同，外部资源0 |

实际浏览器JSON SHAff3458a3996b4aae8f27f89f848cfcc2f0c2af94201a9b7db989bc92a10e536a，1,905,815字节；HTML SHA82eaea32dc8532e6c26cb1230629ddac30743f521bbc21034c28d5dd517100dc，2,949,570字节。阅读导出不是完整审计bundle；不会回写原worker的导出标记或授予执行权限。

## 使用范围和剩余问题

旧ETH4h窗口北京时间02:27–06:27和提案03:19:12.568623均已结束。40/20/40是原主观研究估计，未校准；条件单位获利/损失比较建立在假定成交与成本上，事件、成交概率仍保留未知。financial_qualified=false、execution_authorized=false，当前账户PAPER执行未配置，实际成交计数0。

首份真实结果仍为down、终点一分钟交易价close收益约-0.52%、Brier0.56/log loss约0.9163，不是长期胜率。realtime现在按Sources0/Market1/LLM0运行，风险/现货/执行关闭；它处理真实到期采集和已保存计划，并非只读。research保留原Study审核策略，未来可能使用模型。Root本次未开启周期研究、改账户或下单。

2/3已由原owner GET /v1/operations 200和前后端源码定位：固定三项为API、普通研究和优先研究，缺的是hub-research-priority-worker的offline空占位，身份/启动/心跳均null；不是source/history缺项，也不是stale/error。普通research min_priority=None，可在空闲时按priority降序领取合格请求，但不能抢占在途长任务。新controller将沿原run-product的MIN_PRIORITY=100/SERVICE_ID配置增加真正owned优先研究角色及readiness/cleanup；当前尚未启动该角色，不改分母或假造心跳。证据D:/DH-sidebar-health-read01/diagnosis.json，SHAa8748a78010e96e494218090ecd2d6dfd01e5147a416d5bf8101debc782bbe13。报告本次读取约50秒也需要后续定位。新研究前还需核输入新鲜度，不能用结束于10月26日的旧资料冒作10月27日新鲜输入。正式installed49c和安装指针未切换，Main业务源/native/lib在本预览期间继续冻结。

用户管理员同步后，09:21事件37/35与三次毫秒级偏差已独立复测。11:37另一次只读三样本为+0.5843056/+0.6070478/+0.6025148秒（D:/DH-clock-continuity-20261027-1140/）；说明之前成功不能外推持续健康，不据无出处门槛断言这0.6秒合格或失败。持续授时方案单独处理，不重试已被OS拒绝的同步/任务、不改系统或旧known_at、不因此停止独立研发。微软也说明工作组的触发机制可能停止时间服务，单改Automatic不能保证持续运行，参见[官方说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/active-directory/w32time-not-start-on-workgroup)。不追认旧未同步报告，不据一次同步宣称14天持续时间健康。六组合14自然日及P50/P95、至少两类真实事件全链、通知与后继、10真实PAPER成交/退出/恢复、四周真实评分、30完整失败业务回放、最终质量和正式交接均未全部完成。全部56任务/9阶段与原八工作包保持，目标ACTIVE，无新增ACCEPTED。

## 具名证据

- D:/DH-work14-preview-resume-preimages01/manifest.json：恢复前关闭字节备份。
- D:/DH-work14-actual-release-inputs01/：实际producer、stage、Root绑定核对、启动元数据与stdout/stderr。
- D:/DH-work14-preview-resume-peer01/r3/review.json：恢复门非作者最终复核，SHA43865c84fc02f87d48b095dd1535c68404e3477aee4a1df33e38061f41681969。
- D:/DH-work14-restored-readback01/：owned-six-live、原GET receipt/compatibility、浏览器JSON/HTML、离线导出核验及原UI截图；没有Source SQLite连接或研究POST。
- D:/DH-eth4h-live14/artifacts/PAPER-RESEARCH-HANDOFF.md：本机用户交接。
