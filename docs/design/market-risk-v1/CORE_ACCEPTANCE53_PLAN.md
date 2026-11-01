# 核心交付53：实现和实际验收计划

更新：2026-10-10本次54验收与正常STOP。本页保留53原计划/失败；54 Ready与PC P历史保持，当前已正常STOP，55必要后继待合并/构建/启动，schema0091。停前同BTC queued/gen18/attempts16/version21、eligiblefalse、Saved/ACK0，无新CAS。原八层33项和指标保持，目标active，手机排除。

## 1. 本批用户结果及具体改动

| 问题 | 实现位置与方案 | 已有证据 | 正式通过条件 |
|---|---|---|---|
| BTC完整报告生成失败 | `research_publication_runtime48.py`：当前注册的Graph、配方、commit、context等有授权依赖的读者由同一私有CurrentLoader装载；保留原ORM/DTO身份和冻结来源边界 | 实际52正常CAS后Worker制作失败，`frozen_io_root_data_fallback`；已定位普通Root模块绕过冻结descriptor。候选5项机制控制和独审通过 | 同一原Run、原publication的正常Worker保存；独立确认ACK；状态、质量、完整下载均真实成功；下载SHA/ETag/PC原字节一致。不得直接SQL改状态、重造旧claim或降低原件认证 |
| 自动复查错误出口反复等待 | `web_runtime.py`、`web_host_client.py`、Host `bridge.ts`/`terminal-quiescence.ts`：本地证明pending/busy不作为RPC重试；原任务租约有效时以服务端完整授权核对并收尾，再抛原错误 | Python6项/Host13项及独审通过；旧30m/24h失败原因已在实际PC显示 | 新的合法自动复查实际DSH执行、有限终态、完整报告及父子回链；原错误/取消路径不丢失；旧失败不自动获得重试授权 |
| 测试原文进入真实研究与770段难定位 | `research_library_provenance.py`/library service/Worker/routes/Panel：依据已核验的原件hash识别测试资料；自动选材在limit前排除，显式新市场研究和Worker复核拒绝；保留历史读取；标题/文字定位原文块 | 原库只读确认101B测试原件和770块资料；后台6项/UI3项/辅助审计2项通过。用途成功后refetch失败的旧cache提示不一致正在做有限修补 | PC默认筛选及全部资料切换可理解；用途失败说明与列表一致；测试资料不进入新真实研究；原文搜索/定位与原block及版本匹配。历史导入来源、旧报告是否用测试资料尚未知，不凭文件名推断 |
| 研究列表长时间空白及失败文案错阶段 | `ProductPages.tsx`加载状态，`technicalPresentation.ts`通用运行身份核验文案去除误导的“准备”阶段 | 精确三处文本改动独审通过；实际52首次研究列表约40秒无加载提示 | PC读取中有提示；失败说当前运行阶段，父子/任务/原报告导航保留；不增加新的retry权限 |
| 曲线有缺口仍显示按时更新 | `RiskDashboard.tsx`：fresh但有真实missing_ranges时优先提示缺口；展示本次曲线as_of和原件数量；父快状态说明以子原件核验结果为准 | 实际52曲线缺口与标签截图；稍后同窗口GET已补齐，不据此补造先前PIT。标签1项控制、静审通过 | 实际53缺口/无缺口解释正确；invalid/stale仍优先；已核验子记录与父说明一致；不插值、不改网格/数值/采样/截止 |

## 2. 顺序及每一步完成记录

| 步骤 | 状态 | 验收原件及下一动作 |
|---|---|---|
| S1 核对当前代码、原失败及原件 | 完成（具名范围） | 52 Ready P；BTC inspect/CAS P但最终unconfirmed gen9/attempt8，Saved/ACK false；所有旧失败保留 |
| S2 有限机制控制与独立审查 | 通过（具名15源） | 资料refetch和父子核验文案的后继修补均有控制及独审；正式15源并集不触碰原65保护源，不包含0092异步audit |
| S3 标准合并发布 | 通过（正式53） | 正常STOP、49选择完整覆盖、15源合并、UI/Host各一次标准构建、完整三映射读回、原授权核对、START及Ready P；3健康200/7新鲜在线，schema0091 |
| S4 BTC同原报告闭环 | 原F保留，55必要后继待 | 53 gen11 usage F不回写；54修复Ready P后已正常STOP。停前inspect queued/gen18/attempts16/version21、eligiblefalse、Saved/ACK0，无新CAS；180仅lease，实际重复全源核验与55 scope后继待验，不称loop/完全fix |
| S5 新研究、资料和自动复查 | 待 | 正常合法DSH canary、批准fetch、测试材料排除、反证和质量门、完整报告、自然合法复查；真实用量/外部结束条件单独记P/F/unknown |
| S6 PC受影响矩阵和用户手册 | 具名54资料范围P，全矩阵待 | 53曲线缺口/资料筛选/749段定位P保持；53质量/加载/URI三个F保留。54对应用途、选中加载、title URI和末页返回实际PC P，完整矩阵仍待 |
| S7 33项收口与剩余能力 | 待 | 工程、实际用户动作、金融效果分别结论。所有F/unknown保留；统计样本及黄金准入不能凭代码通过 |

53原首因和PC P/F见[53结果](CORE_ACCEPTANCE53_RESULT.md)；54启动、queued未确认及对应PC复验见[54实际结果](CORE_ACCEPTANCE54_RESULT.md)。每次只维护一页当前任务、33项状态及对应问题证据，旧F单独保留。

## 3. 本批不混入的已知待办

- C50-03：旧ETH完整audit结果正确但30.541秒，性能门仍F。异步11源候选已封，涉及新增0092两表且与BTC运行模块有重叠，等核心报告闭环后独立合并和实际验收，禁止覆盖53修复。
- MR20：原实际100次HTTP p95 0.6453166秒，高于0.5秒；未改首因前不重复同一压测。53后仅在对应竞争/调用路径确有改变时重新验证。
- MR05/30：宏观PIT/vintage/共识/拥挤度仍有缺口；BLS403及退避保留。缺真实来源不能授予模型资格。
- MR17/27：完整用量、实际记忆增益及自然反事实证据仍待；新收尾私控不等于真实SDK成功。
- MR25/26：原28日协议、固定模型及48机会不变。历史风险频率可读取，不代表已证明预测优势；成熟标签与相对基线成绩按真实时点核验。
- MR29：黄金实际供应商、数据权利和报价尚未准入，不能复用BTC/ETH权限宣称可用。

## 4. 当前入口

[八层技术方案](TECHNICAL_DESIGN.md) · [验收与指标](ACCEPTANCE_AND_METRICS.md) · [33项清单](IMPLEMENTATION_CHECKLIST.md) · [当前进度](CURRENT_FOCUS.md) · [PC手册](PC_USER_GUIDE.md)。

[52BTC最新只读状态](../../../artifacts/market-risk-implementation-20261007/B2/release52/btc-progress52_02/receipt.json) · [本机恢复返回BTC截图](../../../artifacts/mr-pc52/history-return-home52_02.png) · [BTC本小时原件核验](../../../artifacts/mr-pc52/btc-hourly-read52_01.dom.txt)。
