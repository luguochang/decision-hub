# 报告服务具体设计：发布材料、日常读取与完整审计

历史47验收快照（原文保留；现行状态见[当前任务](CURRENT_FOCUS.md)，后继接口见[54技术增量](TECHNICAL_DELTA54.md)）：正式47已安装12文件（7生产/5测试），一次标准UI1/Host1构建P，validated83e74a66…；当前两资格与原48机会协议独立原生RO均P，未重训、未签新grant、未重新注册。47首启动因artifact wrapper StrictMode泄漏F，原件保留；仅清理三已核对所属PID，不改标准源、不重构建。同一构建47b于2026-10-10 01:33:00.140846 UTC三Ready200/七fresh/schema0090单快照P，launch20261010T012740694Z，连续健康未证。 R1完整native材料已保存；R2私有受信冻结消费46.844秒/监督49秒P，336SQL/374原生/5819文件读取，未知依赖0，原report f3c162…/decision0ff9…一致、6边界控P。监督器入口/出口仅pin4个candidate文件，completed seal当前核5个；baseline importer当前67d4…匹配但无inline expected参数。此有限诊断不证明全生产50/61/65来源门，未正式issuer/GET/0091/R3–R5，旧quality30.641秒性能F、≤2秒未交付。 核心研究仍失败：46真实Run run_664096f1f03e43b285f05ce9bddc68f7，usage误将terminal drain827/result snapshot816判为内容变化，掩盖原dsh_provider_input_transport_timeout。DEV usage48必要17控P、旧期望1F保留，未安装；Host输入超时仍未解决。本机完整输入核验已定位34.760秒超原30秒门的诊断观察，不延长预算，不宣称未来Run恢复。 首页47已安装但实际PC入口F：HomeHistoryForecast只接受okx:spot:*:trade，真实API及首页审计scope为crypto:spot:okx:*:trade:1m，导致新小时风险入口return null隐藏。UI48修复与真实API scope交集控制正在DEV准备，未安装；47全PC未过。 全产品/全按钮/金融false。见[当前一页纸](CURRENT_FOCUS.md)；以下39 profile/43 HOLD等保留历史时点。

## 1. 为什么需要调整

正式39的同一ETH报告，质量接口22.343秒、下载45.032秒；两者重新调用 `ResearchQualityQuery.read`，重复遍历graph final、generation、Provider原件、历史版本和质量sidecar。实际只读profile为3611条SQL、51秒。13控通过的形状缓存仅降到49.375秒，已HOLD。

当前读取能够证明完整原件，但用户每次打开或下载都支付完整复核成本。这是服务边界的问题。目标是保留完整认证与可审计性，并让已保存报告可以流畅读取。

## 独立技术可行性审查

[一轮静态审查](../../../artifacts/market-risk-implementation-20261007/B2/report-serving-feasibility44/FEASIBILITY.md)已完成，未访问真实DB/HTTP/模型。方向可行；46已保存实际完整native材料，SQL依赖未解析0；已补完整运行时描述并完成本次ETH39私有受信冻结消费P。正式制作/独立确认/GET/R3–R5及≤2秒仍未实现或未知。已有preparation摘要会剔除或掩码部分字段，不能当完整材料；R1必须保留实际完整原生行、SQLite类型、SQL范围/零行、文件及版本闭包，并用受信原读者重现。制作与确认各有独立未知COMMIT边界。

现有quality/report路由没有owner checker；新设计沿用当前本机报告读取策略，内部制作/确认权限另行绑定，不能拿历史终态租约冒充当前读权限，也不新增用户手填token或登录步骤。同一物理快照不能看见后来提交的撤销；需独立出口只读连接检查当前服务状态及其版本，并明确检查时点。完整审计只调用已注册的受信原读者。

## 2. 用户可见合同

- 首屏读取同一已发布报告，明确发布日期、数据截止、使用资格和关键反方。确认未完成时显示原因，不显示为可用报告。
- 质量页和下载必须来自同一个精确publication；下载字节及ETag与原报告SHA一致。不能回填旧报告用量或重评旧结论。
- 日常读取验证精确发布材料包、确认与撤销状态，并沿用现有本机报告读取策略；独立的“完整原件审计”显示审计时间、结果和当前原库漂移。两种核验名称及范围在PC明确说明。
- bundle尚不存在的旧报告继续走39完整读取，界面显示核验耗时；不自动在GET里制作bundle。
- 不触发新研究、重新拟合、采集数据或自动交易。材料包不是新的金融资格。

```mermaid
flowchart LR
  A[原发布与完整证据] --> B[同一只读快照完整核验]
  B --> C[材料包及精确依赖清单]
  C --> D[短事务保存及独立确认]
  D --> E[日常质量页和原报告下载]
  C --> F[显式完整审计]
  F --> G[冻结材料重核与当前库漂移]
```

## 3. 数据合同与模块

拟新增 `ports/research_publication_bundle.py`、`persistence/research_publication_bundle_records.py`、`application/research_publication_dependencies.py`、`application/research_publication_bundle.py`、`application/research_publication_serving.py`、`application/research_publication_audit.py`。dependencies模块负责实际完整原生行、字段类型、查询范围及零行闭包；迁移号按正式唯一head分配，仅追加表，不改旧publication或sidecar。

| 对象 | 必填内容 | 约束 |
|---|---|---|
| Bundle manifest | bundle_id/schema、run/event/owner、精确publication/graph/selection/sidecar refs、原报告SHA/字节数、原published_at、bundle_ref/hash/bytes、全部依赖清单、固定读者版本 | 每个逻辑ref唯一；完整来源与原生类型保留；不能用最新sidecar替换 |
| 冻结材料 | 原质量投影、原报告UTF-8字节、全部必要原生行/schema、Provider与来源原件字节、安装与版本凭据 | 清单必须覆盖原完整读者实际消费集合；不裁掉失败、反证或unknown |
| 保存记录 | owner/run/publication/bundle唯一键、manifest/ref/hash、prepared_at、版本 | prepared不能服务；文件保存成功不等于DB确认 |
| 确认事件 | 独立连接读回原件、真实confirmed_at、确认ref/hash、首次空指针CAS | 未知COMMIT保留fence；禁止盲目重复提交 |
| 状态事件 | confirmed/revoked/unconfirmed及具体原因、原事件ref | 追加保存；撤销后日常读取拒绝；不删除旧材料 |
| 审计事件 | 精确bundle/publication、冻结重核结果、当前漂移、执行版本、开始/结束时间 | 对照结果另存；不能改写原publication |

`as_of`表示本次读取时间；原published_at与原数据截止保持。原用量中的unknown、blocked和review结果按发布原件保存，不能在新bundle里升级。

## 4. 制作、保存和在线读取怎么实现

1. **准入**：只接受服务器实际owner和精确已保存publication。管理员命令制作既有报告包；未来发布完成后通过持久job制作。用户不填写hash、模型、T或权限。
2. **完整核验**：调用现有39完整读者，在一个物理mode=ro/query_only/NullPool快照内取得严格质量投影，并收集它实际消费的全部原生行、schema与文件。收集器遗漏任何依赖、发现跨连接或发生写入则拒绝。入口和出口对全部依赖核验；不以mtime/TTL代替SHA。
3. **保存文件**：内容寻址、流式写入、独立字节SHA、fsync和排他创建；不硬链接可变原件。manifest保存逻辑ref与物理内容映射。完整历史核验和大文件I/O在SQLite写锁之外。
4. **短事务**：重核当前owner、原publication唯一键与精确版本/hash；插入prepared记录。未知提交写持久fence，只有只读对账可以继续。
5. **独立确认**：新物理连接核验保存记录、完整bundle SHA/结构与原报告字节一致，再取真实确认时间并CAS保存确认事件。确认失败不服务。
6. **日常读取**：沿用现有本机报告读取策略，新物理只读快照核对精确publication、确认/撤销状态和版本；流式核完整bundle hash及其结构，返回原投影和原报告。对仍作为当前有效性条件的原生头记录做有界批量核验；完整集合与SQLite类型必须固定，不能只核部分抽样。调用开始时建立只读快照，结束后用独立物理只读连接核对当前服务状态/版本；记录检查时点，不能声称同一旧快照发现了并发撤销。GET零数据写入、零采集、零模型调用。
7. **版本分派**：仅允许明确39旧完整读者及新serving读者。旧模型/报告按原版本保留；不执行材料包中的代码，不通过缺少新记录降级绕过新资格。
8. **审计**：用冻结原生行/schema与原件适配器重跑精确原读者；当前原库改变单独报告drift。漂移是否撤销服务由明确的依赖规则决定，不把当前来源与过去发布材料混成一个时点。

日常读取的依赖规则必须先固定：publication identity、bundle确认/撤销属于在线必核；内部制作、确认和撤销命令另行绑定管理权限，当前路由不存在的用户ACL不能写成已有核验。冻结时点的Provider、训练/研究材料与旧版本源码属于冻结材料完整SHA和显式审计范围。当前原库漂移按固定依赖规则单列；材料包损坏、未确认或已撤销必须拒绝。该边界改变必须在合同及PC说明中可见。

## 5. 有限可行性验证与验收

| 步骤 | 具体检查 | 通过或停止条件 |
|---|---|---|
| R1 清单 | 46真实native材料采集P：336去重SQL/374行，完整原件及运行时存在性/元数据描述已供本次ETH39私有冻结消费；不冒充其他publication完整覆盖 | 集合完整且首尾一致；缺依赖保留首因，不开始serving实现 |
| R2 原读者重现 | 本次ETH39私有受信原读者冻结消费P：46.844秒/监督49秒，336SQL/374原生/5819读取/未知0，report与decision原SHA一致、6边界控P；未正式issuer/GET/0091，峰值内存未知 | 先证明闭包充分，再实现正式保存/独立确认；现有大小门保持，缺材料、超限或结果差异即拒绝，不删材料凑通过 |
| R3 必要控制 | body/hash/schema/原生类型/版本/许可/撤销篡改、未知COMMIT、跨连接、旧39兼容 | 全部拒绝或保留unconfirmed；原字节、反方和unknown均保持 |
| R4 正式PC | 同一publication首次质量页、再次读取、下载；另做一次完整审计 | 核心quality/download各≤2秒；原报告逐字节一致；服务与审计范围可理解 |
| R5 共存 | 一次普通研究和自然小时预测期间读报告 | 不新增SQLite忙或阻塞主线；持锁≤0.5秒；旧O04指标单独验收 |

≤2秒是工程目标，尚未证明可达。第一次真实依赖清单和材料包完整SHA成本决定是否可行；若目标失败，记录实际瓶颈并选择下一次有新证据的设计，不重复同条件缓存或构建。未通过R1–R5不能把“生成了bundle”算作可交付。

## 6. 发布和回滚

依赖顺序：41资格与短事务、40自动结果链正式验收 → R1/R2有限验证 → R3必要控制 → 一次标准发布 → R4/R5真实验收。制作job只使用当前受管进程，不新增常驻服务。

回滚只切回明确39完整读取和关闭新制作job，保留新增记录及原文件。旧publication、原报告、旧失败和成绩分母保持。当前方案、未来代码候选、正式版本与金融效果分别记录在33项清单及机器状态中。
