# 市场风险研究：设计与交付入口

**最新可开发规格：[可执行产品规格V1](executable-product-v1/README.md)。** 含文件级改造、类型/表/接口/状态机、复用工具、工作包依赖、明确评价协议与可运行参考样例。前序文档继续提供业务和历史依据，开发接续先读新入口和tasks.json；不把参考样例通过算生产或金融验收。

最新产品价值方案（2026-10-13）：[事前操作、动态根因链与效果闭环](DECISION_FIRST_PRODUCT_PLAN_2026-10-13.md)。当前实施优先形成BTC/ETH的方案/条件/退出/调整/结算，多资产与数据底座按策略需求接入。

最新技术补充：[自主研究、技能学习与全球信息接入](AGENT_LEARNING_AND_INFORMATION_DESIGN_2026-10-13.md)。现有技能调用、经验消费断点、四条固定进化规则、TradingView官方MCP/Ideas边界、GitHub复用与A1–A6验收均已记录。最小学习闭环纳入首个交付；未把文档与已有工程成绩算作新增功能。

当前状态（2026-10-13）：83当次正常启动通过；真实BTC/ETH研究、自然30分钟复查与原24小时复查恢复已有保存/独立确认及PC全文证据。核心六门与原33项仍未整体通过。黄金、纳指、恒科与完整宏观/金融效果未完成，详见[多资产核心方案](CROSS_ASSET_CORE_PLAN_2026-10-13.md)和[当前执行入口](CURRENT_FOCUS.md)。

## 历史发布记录

以下62b及更早记录保留当时失败与证据，不代表当前状态。62b曾正常启动，四个接口和七个fresh Worker当次Ready通过；当时新BTC尚无新综合报告，旧ETH降级原件重启/下载通过。详见[62实施记录](CORE_REPAIR62.md)。

**61 历史结果**：61 曾正常安装运行；真实 BTC 失败、ETH 发布降级材料。报告字节保存与读取通过，完整综合研究未通过；原33项判断保持。

**本轮修复与待验**：正式SourceSnapshot端口及正常/冻结接线、取消原因与恢复说明、终态日志去重、最近8项读取已安装。251项纯控制通过，不能代替真实报告。首次最近列表1.408秒；实际并发2.141秒及BTC时间线p95 0.647秒仍未过原性能门，正在定位读取路径。见[58需求增量](CURRENT_REQUIREMENTS_AUDIT58_CHECKPOINT_2026-10-11.md)与[接口实施方案](SOURCE_SNAPSHOT_V2_IMPLEMENTATION.md)。

**剩余完成门**：新报告保存/独立确认、自动触发及自然复查、PC核心矩阵和原性能门。六门尚未全部通过。以下55及更早记录均为历史结果，当前状态见[机器状态](current-core-execution.json)。

历史46验收快照（原文保留；现行状态见[当前任务](CURRENT_FOCUS.md)，技术增量见[54说明](TECHNICAL_DELTA54.md)）：46已正式安装3份源码/测试（1生产文件），migration0/restore0、schema0090；标准UI1/Host1构建P，validated SHA75e0be98…。两既有模型资格一次156.687秒published_confirmed，原180秒门内完成、未重拟/自动重试；独立原生只读2.515秒核对两资格及首次confirmed事件version1，明确serving配置已生成。结果协议一次18.016秒登记P，独立0.141秒精确核对protocol/acceptance/48机会并准备独立授权；窗口2026-10-10 UTC00:00–23:00，原28日不变。Ready46_03在2026-10-10 00:00:09.901155 UTC三200/七fresh在线P；首两次warming F保留，连续健康未证。实际PC首小时刷新2/48按时、46等待、unconfirmed0；首UTC00:00双资产14条原生、90秒、独立ACK及按时publication已核P，后续23小时/成熟标签待，不手动回填。45b旧ETH39正文/原字节浏览器附件恢复P仅为历史验收；quality30.641秒性能F。00:00首个自然双资产原生记录、90秒、独立ACK与按时publication已核P；≤0.5秒持锁/成熟评价未证，详见当前一页纸。自动研究恢复、报告R2完整材料闭包/正式确认、全PC及金融效果未过；全产品/全按钮/金融false。


| 内容 | 文档 |

|---|---|

| 当前要做什么、实际失败及下一道门 | [本次六门范围](CORE_DELIVERY_GOAL_2026-10-11.md)、[本次机器状态](current-core-execution.json)、[一页纸](CURRENT_FOCUS.md)、[原冻结协议](execution-contract.json) |

| 全项目功能与来源/资料/研究/反馈/历史层级 | [全项目PRD](../../product/DECISION_HUB_FULL_PRODUCT_PRD_2026-09-26.md) |

| 用户主线与交互 | [UX PRD](../../product/DECISION_HUB_UX_V3_PRD_2026-09-26.md)、[PC手册](PC_USER_GUIDE.md) |

| 各层输入输出、具体接线、可行性、独立升级 | [八层技术方案](TECHNICAL_DESIGN.md) |

| 全部整改、代码入口、原验收门和最新状态 | [33项清单](IMPLEMENTATION_CHECKLIST.md) |

| 指标、分母及有限试验 | [验收指标](ACCEPTANCE_AND_METRICS.md)、[交付计划](DELIVERY_PLAN.md) |

| 已有主线的真实PC操作 | [PC主线35](PC_MAINLINE35.md) |



日常选择资产查看监测与自动研究；手写问题是补充入口。每一步记录源码、版本、真实回执、失败与截图。私库/fixture、HTTP200和进程在线不能代替完整用户结果或金融资格。过往台账按当时的时点解释。


## 在线预测的实际瓶颈与整改

[冻结资格与短事务具体方案](CORE_SERVING_REDESIGN.md)的46 V4已正式安装并完成真实双模型156.687秒资格发布/独立确认，原V3全部512门保持。48前瞻机会登记、标准三Ready200/七fresh P；首UTC00:00双资产14条原生记录、独立ACK及按时publication2/48已核P；后续23小时、≤0.5秒持锁、1440分钟成熟标签和金融效果待验，见[容量实测](MODEL_FREEZE_CAPACITY_DESIGN.md)及[当前一页纸](CURRENT_FOCUS.md)。44/45容量首失败仍保存。

## 报告输出与读取性能

[研究输出整改](RESEARCH_OUTPUT_REMEDIATION.md)保留真实报告、结构与用量问题；45b恢复旧ETH39完整原件/PC下载，但quality30.641秒不合格。42结构与43资格/中文提示已发布，新真实Run待。[报告服务设计](REPORT_SERVING_REDESIGN.md)已完成R1真实native材料采集，仍有7份runtime资料未attest；闭包/重放/R2正式确认/R3–R5与≤2秒待，43缓存保持HOLD。

## 历史报告版本留存

[完整安装身份留存与精确选路](REPORT_VERSION_RETENTION_REDESIGN.md)已按8份真实capture合入45b：构建冻结838 Runs/122 native选择（45 V3、77 V2），原件覆盖gate P。旧ETH39质量/正文/浏览器同字节下载恢复P；其他报告没有全部实际打开验收，首次45预检F与首候选仍保存。


45b具体小时中文的历史PC结果保留；46实际PC已确认48机会批次、刷新评估以及双资产保存/提交后核验状态，并能打开两资产风险原件数字。自动研究旧ETH请求仍有Host终态unknown，未来正常新请求和自然触发未验收；首小时原生/90秒/ACK/按时2项P，后续小时、持锁、性能和金融门分别核对，详见[当前一页纸](CURRENT_FOCUS.md)。
