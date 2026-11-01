# 两处实施缺口与核心性能验收合同26

2026-10-09。本页补齐源码审查发现的具体实施缺口，配合[八层设计](TECHNICAL_DESIGN.md)、[33项验收清单](IMPLEMENTATION_CHECKLIST.md)和[当前整改顺序](CORE_RECOVERY_PLAN24.md)。这是待实现合同，不能作为已交付证据。PC优先；旧预测试验及验收指标不变。

## 1. 历史26基线与发布边界

本节26/25的事实和“下一项”仅保留为历史设计依据，不能触发旧请求或覆盖当前31行动。现行版本、已有修复和下一步只读取[当前一页纸](CURRENT_FOCUS.md)及[机器状态](execution-contract.json)。

26仅晋升5个生产文件：`dsh_runtime/cancellation_projection.py`、`web_runtime.py`、`application/research_quality_view.py`、`ResearchPage.tsx`、`DesignStatePage.tsx`。同一次写入pending/首因保留、同快照recipe验证复用及文案已具有限控制；实际报告、质量与上下文仍须真实验收。

25实际普通BTC失败无artifact，ETH仅2证据/AI失败；另两条BTC任务失败。四条选择原件已保留，260个原件源随标准发布保存。来源可追溯、服务Ready、fixture通过分别不能代表完整报告或金融效果。

### K02：回调成本的下一项必要验证

精确入口：`packages/kernel/decision_hub_kernel/persistence/original_preparation.py:prepare_originals`；`application/provider_input_receipts.py:ProviderInputReceiptService._prepare/_prepare_original`；`application/research_graph_binding.py:_ReadSourceGuard.capture`。

- 实际25主要成本是`original_auth`，BTC/ETH所存子集的中位约5.443/6.210秒；source capture中位约1秒，极端10.001秒。不能把“重复完整prepared认证”当已证明原因：已有私控profile中的prepared和boundary各调用一次。
- 现有纯构造对照只证明：同canonical SHA的warm manifest .598秒、完整fresh builder .423秒，差约.176秒。尚未采纳为生产修复，也不能据此宣称15秒超时解决。
- 26真实请求先按现有阶段记录；若仍慢，只对以下六个互不重叠区间补wall/thread CPU计时和安全计数：source capture、payload/linked input、prepared execution、current boundary、permission/model/native context/DTO、recipe scope退出重核。日志不含prompt、正文、凭据或URL；计时不参与授权、事务或ACK。
- 必须依据实际区间选最小修复；保fresh源SHA、原件readset、当前permission/lease、writer前后核验、15秒预算。验证对应正例与源/原件/事务变化负例，再一次标准发布；不连续试盲缓存。
- 真实通过门：同Run/会话/generation的回调在原期限内完成、Host收到且原件确认；正常完整报告另验。`body_sent`或迟到commit仅是诊断，不授客户端收到/Run恢复。

### K09：质量与上下文剩余成本

`research_quality_view.py:consume_published_research_originals_in_session`的26复用只覆盖同物理快照内recipe验证。SAVEPOINT失去即拒绝返回；SQLITE_AUTH拒绝建立marker时保原authorizer，走原完整读取路径。旧严格观察器因此未获得此收益，原45秒限制继续有效。

现有链：`research_graph_binding.py`最终/轮次sidecar→`research_quality_sidecar.py:read_quality_sidecar_in_session`→provider usage/native census完整原件；publication绑定及context消费亦会调用。下一项先用旧ETH quality/context各一次真实GET，记录独立耗时/返回与源哈希。若仍超时，按这一链定位剩余重复原件读取；任何复用须限定同快照、精确Run/reference/ancestry，首读完整、退出fresh重核，不共享跨请求权限/决策缓存。45秒及原严格读取结果单列，不能修改authorizer或提高期限制造通过。

## 2. K04：正式历史采集、训练与未来消费

### 2.1 现有实现与缺项

DEV根为`E:/project/study/codex/dsh/.market-risk-b2-b4`；正式根为`E:/project/study/codex/dsh/decision-hub`。

DEV已有：

- `packages/kernel/decision_hub_kernel/ports/history_observed_training.py`：protocol/feature/window/dataset/readiness合同。
- `ports/history_observed_model.py`：source admission、fit、model.v2及prospective forecast合同。
- `application/history_observed_dataset.py`：Archive、Originals及DatasetBuilder。
- `application/history_observed_ledger.py`：register、prepare/freeze、prepare_saved。
- `application/history_observed_acquisition.py`：activate、claim、execute、process_one。
- `application/history_observed_model.py`：prepare_fit、save、model、forecast。
- `persistence/history_observed_records.py`、`migrations/versions/0082_history_observed_training.py`及DEV db/env注册。
- command29候选：`application/history_observed_commands.py:HistoryObservedCommandService`复用register/activate/process_one/freeze/fit/status；`apps/hub_worker/composition.py:build_history_observed_worker`及`main.py`显式角色接线已写，四准备控制尚未执行，不能标接线P。

下一次统一晋升组合为13个生产源，不能只提Worker而缺其imports或迁移。以下kernel路径以`packages/kernel/decision_hub_kernel/`为前缀：

| 组合 | 精确文件 |
|---|---|
| 8个基础业务/迁移源 | `ports/history_observed_training.py`、`ports/history_observed_model.py`、`application/history_observed_dataset.py`、`application/history_observed_ledger.py`、`application/history_observed_acquisition.py`、`application/history_observed_model.py`、`persistence/history_observed_records.py`；另`migrations/versions/0082_history_observed_training.py` |
| 2个元数据注册源 | `persistence/db.py`、`migrations/env.py`（env路径从repo根起） |
| 3个命令/Worker源 | `application/history_observed_commands.py`；另`apps/hub_worker/composition.py`、`apps/hub_worker/main.py` |

正式尚未晋升这组业务文件/0082；DEV db/env已有注册，但不是正式schema已迁移的证据。DEV命令与独立Worker候选已写；显式操作者CLI30已封签，必要私库控制0且未正式安装，可信调用者/API/PC仍缺。CLI原件为B2/history-observed-cli30/source-seal01.json，不能把本机owner声明当调用者认证。合同22/采集23/模型24控制不授正式接线、真实90日或实际保存P。V1 readiness的`ready_for_real_fit`固定false；V2 prospective执行资格不授历史PIT、校准、promotion或旧28日试验资格。

### 2.2 接线与命令合同

| 接线点 | 最小增量 | 不可省略的约束 |
|---|---|---|
| Worker composition | command29已在DEV增加`build_history_observed_worker`及显式`history-observed`角色/registration_id；准备控制未执行 | 默认关闭；唯一owner注入；tick只执行`process_one`；不隐式register/activate/fit，不占实时风险或DSH general lane；标准`run-product.ps1`没有启动新角色 |
| 操作者CLI | DEV `apps/hub_worker/history_observed_cli.py`已封签register/activate/status/freeze/fit，复用CommandService；一必要私库控制尚未执行/未正式安装 | freeze采用min(实际当前时点,登记6小时截止)，实际prepare_saved→同实例/thread的fresh writer.freeze；未知COMMIT不重放。本机owner声明不授公共认证。原main的--once仍仅process_one；显式注册与结果确认后才启动对应R |
| 公共命令与状态 | 新strict `HistoryObservedCommandV1`和`HistoryObservedStatusV1`、owner鉴权、API注册及PC均未实现；建议独立`/v1/market-risk/history-training`命令与registration状态接口 | proposed名字未实现；写operation只允许register/activate/freeze/fit，绑定request_id及实际owner，幂等复读/异payload拒；GET只读，不采集、不拟合；回执须反核已提交原件，不把pending/未知commit写成成功 |
| 受管迁移 | 0081→0082，模型records接正式db与Alembic env | 标准停服/备份/迁移副本验证；0082 downgrade遇非空表拒绝删原件 |
| 未来消费者 | 显式调用`HistoryObservedModelService.forecast`，新增独立不可变forecast原件producer/reader及0083；目前均未写 | 当前records.kind仅protocol/dataset/model，不能冒用model行保存预测；旧`risk_predictions`绑定trial/horizon/target，不能伪造旧试验身份复用；通用原件producer未证明满足注册与原件门，不作为现成持久化 |

状态响应至少显示：protocol_hash、180任务状态/attempt/deadline、每scope dataset_hash与分母、source admission、model_hash/实际P、执行资格和不合格原因。scope/日期/cap取已注册protocol，不能从PC临时改值。现`HistoryObservedCommandStatus`仅有注册/任务认证和stored row元数据，`dataset_originals_verified`、`model_originals_verified`、`ready_for_real_fit`、`financial_qualified`固定false；存着hash或任务completed不能被UI/API改称完整原件已核、模型Ready或真实训练完成。公共已验证结果须另调用对应原件reader取得证明。

### 2.3 一条真实数据链

1. `ledger.register(session, request_id)`记录实际R，创建两scope×90 UTC的180 held jobs；`acquisition.activate`仅释放这一集合。
2. claim先持久预扣尝试、owner/generation/lease，再允许execute。保持300 cap、6页、每页1重试、每scope600/总1200 attempts、6小时硬截止及1秒间隔。100 cap是另行注册版本，不能运行中扩页或延时。
3. execute使用现有`OKXMarketPathAdapter.candles`及`CaptureBatch(job_id, adapter_version, RawBodyArchive, _capture)`；真实raw bytes/ref/hash与started/completed/recorded落库；同writer进入`MarketPathStore.ingest`。独立jobs不借用DataJob literal或实时98%覆盖门。
4. 原raw引用区分历史保留原件与本次acquisition checkpoint。相同semantic修订去重保最初receipt，不能换成最新页receipt冒充首次已知。
5. `ledger.prepare`在caller物理RO构造；freeze另writer重核metadata/readset/完整raw SHA并保存实际S。窗口close≤D、known≤K；label计算与label_known_at保存实际C。R≤K≤C≤S、admission A≥S；不伪填历史D/K。
6. `model.prepare_fit`先核180 jobs/checkpoint/context/version、selected point及完整raw。joint≥60且low/high各≥30；59或任一29直接拒绝。纯数值fit不塞writer。
7. `model.save`核prepared issuer/thread/HMAC、metadata/readset/fullraw与数值support，保存不可覆盖model.v2和真实P≥fit_end。同Session/thread/native事务与随机SAVEPOINT不能在raw COMMIT→BEGIN后续用准备值。
8. `model.model/forecast`读取原row/payload/archive/admission；要求P≤T≤当前鉴权时刻，known≤T的连续31close且latest距T≤180秒；median tie按low≤、high>，阈值固定0.02/0.05。不得改旧fit或调用旧资格函数补认。
9. 待写`record_history_forecast/read_history_forecast`及0083独立原件：绑定schema/source身份、registration_id、精确scope、request_id、原model引用/model_hash/实际P、T/threshold、完整31close特征原ref/SHA及feature_known_at/latest_close_at、forecast完整payload/hash和实际produced_at/recorded_at。沿原model/source/feature认证及P≤T、known≤T、180秒门；计算在RO准备，fresh writer重核身份/readset/fullSHA后才给已提交回执。相同request与相同身份/payload复读第一次保存原件；异scope/model/T/threshold/feature/body冲突拒绝，不覆写时间/hash。缺原件、错误SHA、错scope、P>T、陈旧/断裂close拒绝；未知COMMIT不自动重放，后续确认必须读真实已提交原件。当前forecast返回DTO并归档feature，不等于已持久消费；GET不调用forecast制造新原件。

### 2.4 必要验证与PC完成定义

- 当前command29仅有四准备节点，执行/静态均未开始：`tests/market_risk/test_history_observed_commands29.py`覆盖显式注册/status不授hash已验或fit、默认role及缺R在DB前拒、实际隔离SQLite物理RO prepare→fresh BEGIN IMMEDIATE freeze/fit、未知提交不重试/不假ACK。先按`B2/history-observed-command29/CONTROL_PLAN02.md`一次四节点组保首结果；人工model24 fixture不授真实90日或金融效果。只做三命令源及新控制的定向静态，不重22/23/24数学矩阵。
- 接线及CLI必要控制通过后再统一13源基础加CLI晋升；迁移先在隔离副本验实际0081→0082/metadata一致及非空downgrade拒，正式迁移走标准流程，0081与旧historical/旧fit不改。CLI候选通过并正式安装、真实采集授权满足后，才本机显式注册一次、activate、启动对应R的独立worker；公共owner/API另按可信调用者与已存归属双门实施；不得借默认7服务偷偷采集。
- 仅补受影响接线节点：0081→0082/非空回滚拒、同request复读/异payload拒、exact180、预算/截止/旧lease/重启inflight unknown、31close/180秒/median tie、59/29不足、P>T/错误scope拒。
- 负例保raw/row/checkpoint/source/hash/Prepared篡改、raw事务替换和writer前后mutation拒。未变22/23/24有限证据保留，不重复整套训练控制。
- 正式显式注册一次固定90成熟D；真实采集保存raw+checkpoint，分别冻结、fit并保存实际P。任何分母/来源/期限不足仅partial/insufficient，无Ready、无自动另开或续旧试验。
- PC显示两scope的R/K/C/S/A/fit/P、90/60/30+30实际分母、排除原因和原件SHA；P之后在正常未来T消费一次并保存回执，刷新读取同hash/T。失效或不新鲜显示unknown。
- 页面明确`history_observed_at_fit`、未校准、PIT/promotion=false。它不能替代100独立日/30事件、Q01–04金融效果或旧28日验收。

## 3. K05：让用户核对实际保存的行情/事实原件

### 3.1 已有功能，不能从零重做

| 当前正式入口 | 已有能力与边界 |
|---|---|
| `apps/hub_api/evidence_original_routes.py` | GET `/v1/research/runs/{run}/evidence/{evidence}/original`核run归属、content_ref/source_content_hash/parser_ref；保存文档校验失败409；X-Content-SHA256、private/no-store/nosniff |
| `apps/hub_api/fact_routes.py` | captures沿EvidenceParsedLink→ParsedVersion.capture_ids；raw body由RawBodyArchive校SHA，以text/plain/CSP呈现；call input set可能多来源，不能冒充逐句证据引用 |
| `packages/workbench_adapters/fact_assets.py:FactAssetResolver` | 只解析不可变fact version；支持coverage_manifest/historical_partition/event_window/document；source-registry仅政策定位，不抓今日网站替代旧原件 |
| `ResearchPage.tsx:SavedEvidenceOriginal/EvidenceDrawer`与`EvidenceCapturePanel` | 已有文档/HTTP原响应入口与missing提示。25 ETH的HTTP关联空仅证明missing边界；其具体非HTTP ref本次未读，保持unknown |

### 3.2 新增reader、DTO与权限

DEV已有4源候选（strict DTO、reader、route和main接线）及一控制草稿，已封签于B2/evidence-input-readback30/source-seal01.json；控制0，未正式安装，真实可信resolver及API-PC/TS仍未实现，入口不可用。reader返回完整已存normalized FactVersion JSON/base64及SHA/字节数，沿RunInputRef/Parsed/fact身份及完整codec/物理RO/fresh核验；不能用摘录或今日补抓代替原件。

reader先核Evidence.run_id、精确EvidenceParsedLink/parsed content身份，再只解析同一已保存content_ref/structured_payload_ref或已认证acquisition receipt。复用现有FactAssetResolver支持类型；额外ledger只加具名decoder。未知scheme返回typed unsupported，不猜URL、同source、最近fact，不网络补抓，不写新原件。

DTO建议字段：

| 类别 | 字段/规则 |
|---|---|
| 身份 | schema_version、run_id、evidence_id、source_id、evidence_content_hash、parsed_version_id、parser_version、kind/ref |
| 结果 | status=verified/not_recorded/unsupported、missing_reason、payload/locator；完整payload缺失不能verified；完整性破坏409与未记录分开 |
| 原件 | raw_sha256或structured_payload_sha256、byte_count；不向旧strict RawCaptureView/ArchivedInputView塞未知字段；生成Py/TS/Zod合同 |
| 时间 | 原published_at/observed_at/received_at，以及有认证的known_at/recorded_at；缺失null+原因，读取时间不替代发布时间 |

现有original/captures/body路由的run归属/capture存在校验不代表私有资料owner授权。新入口须分别通过可信调用者认证与已保存owner–Run归属回执两道独立门。linked command receipt或`owner_root_creation_receipt_in_session`可证明原Run归属，不能认证本次调用者；非空owner header/correlation及本机owner声明均不可信。当前候选无可信resolver即在DB前403/research_evidence_input_owner_authority_unavailable；现有本机cookie authority仅/v1/harness范围，不扩大到研究原件。缺真实调用者认证或原owner回执均拒绝。library资料维持专有权限，通用reader拒跨Run/ref/path/capture及private/library fallback。

### 3.3 SHA、时点、UI与验收

- 证据语义content_hash、excerpt SHA、source_content_hash、raw body_hash、归档payload SHA分别表示，不能互相替代。完整raw SHA须同时等于ref digest与RawCapture.body_hash，长度等byte_count。
- 同物理snapshot核row/link/version/readset、parser/source/structured ref并退出重核；缺完整SHA/time绑定的ledger只能显示未核验摘录。合法parsed复用以既有content身份相同判断，不误要求origin evidence一定为当前。
- 沿原Evidence published/observed/received、FactVersion known、RawCapture started/completed/recorded。Run已采纳事实须绑定其RunInputRef fact_version/as_of/adopted；不能拿current latest替旧Run补资料。
- 既有文档/HTTP入口继续兼容；无文档且HTTP captures空时增加“已保存结构化输入”入口，展示类型、原端点/时点/hash及缺口。原bytes不编辑成所谓脱敏原件；若权限不允许含敏感内容的原bytes，拒绝显示/导出，保留原件身份。
- 控制覆盖：正确document/HTTP set/fact各类型、合法parsed reuse；跨Run/owner/library/capture/ref拒；缺link/ref/时点/parser或错SHA/字节/归档按403/404/409/typed missing区分；GET无SQL写/adapter HTTP；刷新切Run/迟到结果不串资料；下载与原SHA一致。
- PC真实正例须分别核一条实际获准document或HTTP关联证据，以及一条非HTTP ledger证据：同Run/source/selected ref、真实时点/hash、刷新同hash、下载核原bytes。真实Run/evidence/ref ID须验后记录，fixture和25 missing不能代替该正例。

## 4. 执行记录与停止条件

每步保存before/after源、唯一版本、首次失败、实际请求/原件哈希、验证范围、截图和下一步。当前阶段只从CURRENT_FOCUS/execution-contract读取，不把长台账作为执行指令。具体接口未定时标proposed/unknown，代码存在不等于接线或真实资格。

工程、正常PC流程、数据资格和金融效果分开验收。不能为交付提前回填旧试验、改变统计分母、增加回调期限或重试同一次未知COMMIT。目标继续覆盖原33项，本页不删减后续主线。
