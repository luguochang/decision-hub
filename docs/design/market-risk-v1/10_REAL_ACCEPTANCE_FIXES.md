# 10 正式验收发现与修复任务

2026-10-08；仅 PC。本页是实际失败后的有限实施单，补充33项清单；不是交付通过回执。所有旧失败和原报告保留。

## 实际发现

| 问题 | 原件与实际结果 | 必须解决的结果 |
|---|---|---|
| F10-CORE：研究取证后未提交报告 | ETH `run_816ff098618349fabca7b2baeacb82da`、BTC `run_9279d16bf4484d28889da0e5f487a650`都failed/artifact null，各12条证据、9次网关预约；native首码`dsh_provider_input_transport_timeout`。页面上层错误`research_worker_failed`。与09未进入取证的5秒finish失败区分 | 真实取证、根复核、质量投影和报告提交完整闭合；准确显示失败原因，不能只保证服务在线 |
| F10-READ：标记已读500 | 原revision `revision_925dcf947fa7b1c6dba88f9f842191d81e0bd0c3`；原nonce `report-read:cfc4774a-5155-404b-bc05-d1aa5129aea3`。POST初始`BEGIN IMMEDIATE`遇`database is locked`，原请求尚未入ProductCommandRecord。UI保持未确认，其他标记按钮锁住 | 可恢复的写锁状态；原nonce查询真正只读、可以安全完成原操作；不丢回执或制造重复副作用 |
| F10-PC-01：下载无法确认 | 旧ETH质量已显示且原报告hash不变，实际点击下载后20秒download事件超时，本机Downloads无新文件 | 浏览器从页面下载实际完整JSON，核对下载字节/SHA与该版原报告一致 |
| F10-PC-02：解释仍暴露内部代码 | 旧ETH质量说明含`market_regime_context`等字段、英文stop说明及12条原始JSON | 用户可读的中文缺口/依据/停止原因；完整原文按需查阅，原JSON/hash/资格不变 |

具名原件：[真实终态与provider观察](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh10/failure-observation01.json)、[PC已读失败](../../../artifacts/mr-pc10/read-receipt-unconfirmed01.txt)、[PC旧质量](../../../artifacts/mr-pc10/old-eth-quality01.txt)。

## 已证明的技术瓶颈

一条child model-binding实际写事务持续14.875秒，其中`prepared.verify`的全源重新核验占13.020秒。路径为`model_dispatch.py`的writer开始后调用`original_preparation.py`中的`source.matches()`。当前两个provider input超时需要结合准备截止、当次源核验及写锁竞争进一步归因；不能用这条已经释放的锁冒充后续已读POST的唯一持锁者。已读POST的那个具体holder尚未证明。

设计要求：减少持写锁核验的开销，保持源码SHA、准备原件、权限、版本及提交fence检查。11选择同语义fresh scandir优化，完整SHA仍在writer内；单DEV profile改善不证明正式13.020秒已消除。新的11准入误分类阻断尚未进入DSH，见[11修复单](11_REAL_ACCEPTANCE_FIXES.md)。不能用降低校验、加大全局模型额度、无条件重试来替代修复。把实际锁时间和调用期限写入验证回执。

## 有限执行顺序

1. Core核对两个失败Run的精确provider输入链、准备剩余期限及来源验证路径，给出最窄源改动和篡改/过期/fence负控。
2. 独立修已读命令：初始写锁BUSY可恢复而非500；同nonce GET不写，受理/提交/重读只产生一笔业务结果。复用实际revision和原nonce的隔离副本控制。
3. UI候选已封签：2生产源、2控制源；6项新有限控制和限定类型检查通过。页面原生下载链接有稳定URL生命周期；中文说明、原文和下载JSON分开显示。实际PC仍待验证。
4. 所有候选冻结后合批一次标准stop/保留实际10原件/精确晋升/build/start，不在运行中修改正式源码。实际10 V3闭包249文件已经严格捕获。
5. 新发行仅各资产一次普通真实请求；核完整正文、同资产90日/双平台/衍生品、独立复核效果、实际usage/未知USD、publication/case回链。失败保存首因；已测试未修改模块引用原证据。
6. PC确认原已读请求、下载字节和中文解释、同Run报告与返回；更新33项清单、16具名门、手册和截图。核心门未通过，目标继续active。

## 完成与等待

真实新报告、同nonce已读恢复、实际下载和受影响PC通过后，才能裁定本轮工程范围。固定28日观察、100独立日/30事件、自然告警、宏观vintage/事前共识/仓位与黄金供应商准入，独立保留真实不足；不能作为编造金融效果或无限重构的理由。
