# 04 真实验收新缺陷与 05 修复任务

时间：2026-10-08；目标继续 active；范围 PC，BTC/ETH 现货。04 正式进程保持正常运行；修复仅在开发目录，经过标准发布后再验收。

## 已经完成的实际动作

- 04 标准构建及正常启动通过，API/DSH/采集 ready、7 业务服务 online；真实 scoped MCP 目录 HTTP200。
- PC 各提交一次 BTC/ETH 请求，未绕过 30 分钟准入规则。ETH `run_e6545db744fd46538ec870808461cd73`、BTC `run_747187cd527140938901f70a01f25479` 均已真实 degraded/research_only 保存，各9个完成工具，质量资格未过；原请求和Run保留。
- ETH 实际 12 条采纳证据、9 个完成工具调用，主链 4 条/反链 2 条；publication.v4、质量和 Study 原件已保存。终态不是资产正确或预测资格通过。
- PC 已打开 ETH 报告，展开反向审查、独立日线与发布限制；质量读取失败后只重读一次，仍失败。截图：[已保存回执](../../../artifacts/mr-pc04/eth-research-saved02.png)、[报告范围与质量异常](../../../artifacts/mr-pc04/eth-report-scope-quality-failure01.png)。

## 必须修复并自测的项目

| ID | 根因和实际影响 | 实现方案 | 验收条件 | 当前状态 |
|---|---|---|---|---|
| F04-01 | profile 给 ETH 固定 BTC 示例；历史/衍生品/拥挤路线只支持 BTC；Gateway 未核 query 资产，实际取到 BTC 原件 | 提示从冻结原资产生成；Gateway 从验证过的 Factory Task scope 校验同资产查询，跨资产/mixed/未知拒绝并留 audit；不替换参数，不降低双 venue 门 | BTC/ETH 两方向误取在调用 adapter 前拒绝；正确资产合法；通用宏观仍合法；新 ETH 不采纳 BTC 为自身日线/衍生品确认 | profile/Gateway候选封签；ETH日线/衍生/拥挤/双venue现货原生候选13控制P、12公开GET各次200；正式未安装，新同资产报告未验 |
| F04-02 | 报告视图按任意 accepted 日线投影；ETH 页面显示 BTC 的 85561 收盘、SMA60=77236.59、7日偏多 | 确定性日线结果与不可变原 Task 资产绑定；未知或不一致不生成本资产7日观点，保留原报告及证据 | ETH+BTC 日线不投影7日偏多，BTC本资产正常；规范符号别名须精确映射；旧未知资产不补认资格 | 开发4个独立控制P，精确原Task资产/规范别名绑定已封签；正式未安装，新/旧报告PC待验 |
| F04-03 | 发布质量接口重复验证同一 native final 3 遍、selection 23 遍；实际 HTTP200 耗时39.297s，超过 UI30s；离线当前质量与完整报告 schema 实际均通过 | 一个干净物理只读事务内复用已经完整验证的精确 final；不跨请求/Session，不复用当前权限判断；保留原件/hash/源码末端检查、写入和事务失效检查 | 有意义 native 私库回归；正式相同质量原件内容保持，PC30s内显示质量和下载；不可把加大超时当修复 | binding/view候选冻结；4unit scope/失效记账P，不是真实性能P；正式安装后的原件内容与30秒内PC仍待验 |
| F04-04 | 质量上下文 builder 固定 runtime_attestation_ref=null / context_fidelity=false；已存在认证 Host terminal 与 protectedV4 原件却没有接线 | 评估并接入独立可核的实际 Host、请求、PromptIntegrity、protected selection 与结果原件；不能用 completed 直接设 true；实际费用/概率/交易资格未知继续未知 | 缺/坏/错 session/generation/hash 必须不认证；真实匹配才承认对应边界；不声称模型理解、预测有效或仓位安全 | Host原件接线候选冻结，实际04原件候选认证对应边界P；fidelityFalse、provider输入保留未认证、实际费用/概率/交易支持仍未知；正式未安装 |
| F04-05 | V3 selection 强绑定当前安装 receipt/manifest；改 profile 等闭包源后旧04 V3任务和报告可能失去读取资格 | 保存04 selection全部182源及schema/registration/receipt/pointer原SHA；受控 retained V3 原实现读取/序列化桥接，原Run不换新版本 | 05新任务用05；04旧报告/质量/证据继续以04原件读取；损坏旧原件拒绝；不DB恢复或重签旧任务 | retained V3桥候选冻结，private4P；05安装后的旧04真实报告/质量/证据读取未验，仍是发布门 |
| F04-06 | 04 PC 首页研究依据直接拼接 artifact.summary，显示 protected_context_incomplete、challenge_unresolved 等内部原因码；同一路径先深核研究质量，价格/提醒也被阻塞 | 即时摘要只查询同资产、24小时、非未来已保存报告链接，始终 partial，主要反方不从未核原件推断；中文说明展开核验与资格待确认。原有默认折叠研究质量面板展开后完整核验，原机器码/AI正文留在原件审计 | BTC/ETH精确链接、30m/未来/其他资产不混入；即时摘要不调用深质量reader、不授qualified、原摘要/原件未改；05 PC价格/提醒及时可用且展开质量/依据/反方真实核验 | 开发1项有意义边界控制P；候选封签，正式未安装，实际即时流程与冷质量30秒门均待05 PC验收。关联F04-03及MR19 |

## 凭据及限制

- [04 正式启动](../../../artifacts/market-risk-implementation-20261007/B2/formal-start04/receipt01.json)、[目录读取](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh04/catalog-read01.json)。
- [ETH 真实终态原回执](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh04/actual01/run_e6545db744fd46538ec870808461cd73/receipt.json)。观察工具有两个诊断断言假设需另版修正，原件保留，不把观察器False直接当产品失败。
- [质量实际HTTP读取](../../../artifacts/market-risk-implementation-20261007/B2/quality-pc04-read01/receipt.json)、[只读性能分析](../../../artifacts/market-risk-implementation-20261007/B2/quality-pc04-read01/profile_read.txt)。性能分析带profiling开销88.9s不能当普通接口耗时。
- 目前 quality 为 research_only/direction blocked：资产覆盖不足、未解决审查、上下文/认证未接线、预算与执行支持未知。真实报告保存成功，完整产品交付未通过。
- 修完后先冻结候选、保留04原件、标准停止/安装/构建/启动，再验真实报告、质量完整原报告下载、旧报告和有限剩余PC项；每一步保留失败与修复回执。无 schema 变更则不重复迁移。
- 28日自然前瞻/成熟标签/金融增益仍独立，不能用工程测试补造；手机延期。

## 05具名封签与准确剩余门

- F04-01：[资产profile](../../../artifacts/market-risk-implementation-20261007/B2/asset-prompt-fix01/source-seal01.json)、[Gateway资产门](../../../artifacts/market-risk-implementation-20261007/B2/gateway-asset-scope01/source-seal01.json)、[ETH原生能力](../../../artifacts/market-risk-implementation-20261007/B2/eth-native05/source-seal05.json)，分别是开发候选，不当正式报告通过。Gateway初次fixture/闭包漂移错误原件保留，不宣称所有未重验节点全P。
- F04-02：[日线投影](../../../artifacts/market-risk-implementation-20261007/B2/daily-scope-fix05/source-seal05.json)保留原报告和BTC证据；unknown原任务不补认资产。
- F04-03/04：[Root候选SHA引用](../../../artifacts/market-risk-implementation-20261007/B2/docs-update05/root-candidate-source-reference01.json)只读核对binding/view/sidecar三个实际源码SHA与Root封签一致，非新性能测试。参考[4unit控制](../../../artifacts/market-risk-implementation-20261007/B2/quality-final-scope-fix01/controls03.xml)和[Host接线控制](../../../artifacts/market-risk-implementation-20261007/B2/quality-terminal-fix01/controls01.xml)，实际04候选认证按Root具名核对；[上下文技术评估](../../../artifacts/market-risk-implementation-20261007/B2/context-fidelity-feasibility05/REVIEW01.md)说明Session递交、provider输入保留与模型理解仍需分别证明。
- F04-05：[旧V3只读桥](../../../artifacts/market-risk-implementation-20261007/B2/retained-v3-read01/source-seal01.json)，损坏/错误边界拒绝私控P；05安装后完整旧报告正式读尚未P。
- F04-06：[即时研究摘要边界](../../../artifacts/market-risk-implementation-20261007/B2/user-summary-fix05/source-seal01.json)仅查询视图与原边界测试调整；深质量核验仍在展开路径。试写真实AI摘要的未封签中间方案已撤回，按Root与Runtime批准的即时风险优先方案冻结；不能把保存链接当完整研究质量通过。
- [两真实04终态](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh04/terminal-reconciliation02/receipt02.json)身份纠正只消除观察器错误：保存报告均为research_only，资产覆盖/审查/预算/上下文未满足继续如实记录。

当前顺序：标准05发布→旧04两报告完整读→新BTC/ETH各一次真报告→[10项有限PC](../../../artifacts/market-risk-implementation-20261007/B4/remaining-pc04-review01.md)。未安装、正式性能和金融效果均不冒充通过；本轮[文档同步](../../../artifacts/market-risk-implementation-20261007/B2/docs-update05/UPDATE01.md)不重做测试、网络或发布。

## 05失败 / 06构建通过但启动失败 / 07容量修复

- 05真实回执估计2182439字节超过2MiB，UI/Host尝试均0。06改为可还原每个source_before SHA的delta编码，保持原2MiB及标准源码守卫；实际回执1373618字节，标准UI/Host各1次退出0，SHA `cdd006b030ab6b34ebd37d0dca06556f392a7c694e24f40e592df48ec9ae1199`。旧05失败未覆盖。
- **F06-01：06实际API启动失败**。launch `20261008T010901436Z`，`assessment_basis_source_limit`：评估器扫描含历史归档的4113文件，原数量上限4096；实际81508288字节未超128MiB。不是SDK离线。原stderr和失败launch保留。
- 07容量修复：唯一生产源 `packages/evals/assessment_basis.py`，SHA `53d174cd…cece96d3`→`5a7b2e19…ae5a2d3b`，MAX_SOURCE_FILES=8192；不删除历史、不跳过SHA，16MiB单文件/128MiB总量保留。实际4113/边界8192接受、8193拒绝、同大小篡改改变manifest均通过私控；没有正式分析/模型/金融资格。
- 标准停止通过后，06全部8209源/产物按原SHA保存 `artifacts/mr6-src07`，只晋升一个源码并有原字节备份。[实际晋升](../../../artifacts/market-risk-implementation-20261007/B2/assessment-capacity-fix07/promotion07.json)、[有限控制](../../../artifacts/market-risk-implementation-20261007/B2/assessment-capacity-fix07/control01.json)。07标准构建进行中；ready、旧04回读、新BTC/ETH报告及PC是剩余门。
- 历史归档全量扫描的容量增长仍是维护约束：本修复只消除已发生的4113数量阻断，未来达到字节/数量门须明确失败；后续按稳定依赖集合优化，不能通过忽略原件或自动无限放宽门处理。


## F07实测问题（尚未正式修复）

| ID | 正式07实际发现 | DEV最小修复及控制 | 真实剩余验收 |
|---|---|---|---|
| F07-01 | ETH旧04质量GET409/32.765秒；完整报告200/.188秒；原回执保留B2/formal-old04-read07 | 恢复第6个pure逻辑模块名称；新的只读组合桥一次完整原Graph＋原publication/policy/checker重算。4P，ETH22.469/BTC13.391秒，原质量/gate不变；损坏候选及同TX写拒 | 标准08安装、正式冷HTTP30秒门、旧BTC/ETH报告及完整下载 |
| F07-02 | 实际ETH子报告顶部BTC/宏观；记忆部分缺失项裸英文ID | ResearchPage通用市场/宏观标题；MarketMemory按已登记词表中文显示，原标识保留技术原件 | 正式08实际同ETH原报告和记忆回归，不改变分析结论 |
| F07-03 | 管理→更新覆盖里ETH日线显示其他数据集；实际ETH90行/4分区已核 | DataJobsPage补ETH日线登记标签及筛选；数据原件不改 | 正式08数据列表、覆盖和筛选PC回归 |

截图与动作：`artifacts/mr-pc07/`。一次产品缺陷反馈已保存于ETH原子报告，评估记录独立不可改写；不作为预测成绩。provider/复核V2对应MR16/17/18，不因为新纯映射P宣布真实模型验收。
