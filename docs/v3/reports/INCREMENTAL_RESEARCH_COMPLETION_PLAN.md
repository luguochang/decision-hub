# 原永续完整研究提交 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在原产品中使有原件支持的完整研究可以完成，同时保留仅材料、有限结果及金融/执行资格的独立含义。
**Architecture:** 保留 publication-source.v5 历史 writer/read/replay。新独审内容核验使用显式 findings.v2；新 publication-source.v6 保存其鉴真结果，由完整报告提交事实决定新 Run 的 completed/degraded。使用现成存储、提交、原PC及阅读导出入口，不新建工作台或总控框架。
**Tech Stack:** 现有 Python、TypeScript、JSON Schema、原合同生成器、pytest/Vitest。
**Spec:** Main docs/v3/01_REQUIREMENTS.md §4/§5；AGENTS.md §55；原产品 perpetual_research_report.py:332/413、commit.py:223/562/1363、perpetual_saved_report.py:168。用户已授权自主处理文档与实际冲突并保留原系统。

## 决策与边界

- 当前V5固定 research_complete=False / research_only，read_source硬校Run=degraded。单加统计bool或sidecar无法完成研究；不得把旧07改成成功，也不能把所有approved直接计合格。
- 新的完整研究状态独立于金融发布门：GateStatus.research_only、ExecutionPermission.allowed=False、financial_qualified=False保留；不借publish门改变金融/交易权限。
- 独立child只核其实际可见的冻结内容，不自报PC或重启结果。PC读取与重启读取仍由实际产品取证。
- 原证据、反证、数据量、来源权限、600s模型/120s工具等既有预算、3600s运行/5400s候选有效期、UTC及单调时钟均不改变。
- Main正在完整normal构建，期间只读。实现先在D修复区，所需旧模块先与当前物理Main逐字对齐并保留baseline；不得用D旧Python依赖或假__file__冒充Main。
- 不在summary字符串中暗藏新协议。v1 findings及全部旧原件按旧版本解释；新字段使用显式typed版本。

## Review Focus

1. approved但缺保护、观望换选、证据或时点：动作审核与完整研究分开，完整性保持incomplete，不能强迫开仓。
2. 改判定、改catalog或串Run/hash/child：原鉴真拒绝；正面check不能自授来源权威。
3. V5旧报告、旧findings、负面独审：历史读取原样保持，不重新补字段或翻状态。
4. 新Run completed但未实际PC/重启回读：仅表示提交完成，最终六条件仍未全部验证，不入合格分子。
5. 24h/72h及事件：原实际target/horizon和完整p/q保持；评分runtime当前仅4h是另一个待处理缺口，不以新提交代替。

## Task A：显式独审内容检查

**Files:** contracts/schemas/decision_product_v2.schema.yaml 及其现有生成产物；extensions/dsh/decision-hub/src/perpetual-synthesis-protocol.ts、reviewer-tool.ts、final-review-gate.ts、reviewer-model-view.ts；packages/runtime_adapters/dsh_runtime/perpetual_attestation.py；对应定向合同/协议测试。具体生成器及全部直接消费者清单先核后封存。

**Interfaces:** 新 perpetual-review-findings.v2 保留 review_request_ref、summary、issues，增加 report_assessment。四个必填命名检查为 frozen_information、probability_completeness、evidence_balance、cost_protection_and_conditions。每项使用 status=passed|incomplete|failed、非空reason及原evidence_refs；passed须有可回读依据。沿现有native双对象raw输出及完整child/input/output proof鉴真。approved不自动填任何check，旧v1绝不补默认值。

实际鉴真actual_prompt中的FINDINGS_FIXED_FIELDS_JSON决定预期findings版本。新producer固定要求v2；旧v1 prompt沿旧parser。不得从未信任的raw第二对象自选v1|v2，以免新调用降级绕过四项。request.v2/v3、authorization.v2/v3、frozen-review-result.v4及双对象数组保持原版本/形状，findings全文仍绑定原child/Provider proof。

- [ ] 写RED：旧v1往返不变；v2缺项/误绑/伪造引用拒绝；真实负面和approved+incomplete均按实际含义保留；child不能声称PC/重启已验证。
- [ ] 扩展显式合同并用原生成器只生成D候选；修改直接消费者与prompt，保留旧协议分支。
- [ ] 验证必要Python/TS协议及strict/Ruff，保存实际输出、来源和局限；独立复核后本地精确提交。

## Task B：完整报告版本与原提交/阅读接缝

**Files:** packages/kernel/decision_hub_kernel/application/perpetual_research_report.py、commit.py、perpetual_saved_report.py；必要独立小helper perpetual_report_delivery.py；原报告/提交测试与新的定向测试。原 PC 仅修改 apps/decision-desk/src/api/perpetualSavedReport.ts，新增同目录 perpetualReportDelivery.ts、perpetualSavedReportDelivery.test.ts、perpetualSavedReportDelivery.test-support.ts，以及 src/product/__fixtures__/saved-report-delivery-native-incomplete.json、saved-report-delivery-native-complete.json 两份原服务合成原生保存/回读原件。原页面组件、布局及导出生产实现不变；原 financial/permission producer不扩权。

**Interfaces:** helper消费经过原native鉴真的synthesis/review/findings/catalog及冻结身份，输出版本化的完整性事实、原因、原件refs/hash及提交是否完整。结构校验包括真实known_at严格早于decision_at、匹配期限与完整概率、可追溯且去重的3关键证据+1反证；成本/保护/条件还必须有独审内容检查依据。缺证为incomplete，不能靠非空文字、exit_ref或approved补真。

- [ ] 写RED：V5整包读取不变；07不合格；新完整/不完整内容分别落completed/degraded；金融gate/permission仍原false；篡改任一判定原件或跨版本引用拒绝。
- [ ] 新写source.v6并在同原事务保存完整性判定；Run终态只在该版本依据非金融完整报告提交结果选择。V5分支不全局放宽。
- [ ] 原SavedReportQuery按源版本分派，沿原完整原件/概率/行动读取及JSON/HTML通道；不得新建API翻译层或替代前端。
- [ ] 必要整合、类型/格式、历史负例与独立复核通过后，按精确旧SHA备份采用Main；由原normal流程认证新来源。

## Task C：实际六条件与稳定性登记

**Files:** 复用现成T-303登记、原报告回读/重启探针和当前六组合caller/observer；实际所需最小接线在新normal及上述协议冻结后列明，不先填合格记录。

- [ ] 新独立Run只在新normal、typed独审及V6读取闭包通过后启动；冻结选择与真实数据前件，保留每次失败分母。
- [ ] 用实际原PC展示、实际下载及同报告重启后回读取证；绑定Run/source/artifact/hash，实际未执行的一律pending。
- [ ] 六条件全部有原件且没有失败/有限/仅材料状态才计合格；completed单独不计。保留14自然日90%、P50/P95 15/30及全部最终目标。

本计划按切片报告和原件判断实际状态，不以勾选替代验收。2026-10-26接续：31源normal已终态exit0，仅认证此前content/pooling来源；Task A已完成D候选提交327b6eb，Task B及原PC读取候选仍未采用Main。原PC定向77PASS、原UI tsc exit0，不是新真实业务或重启验收。另已实际确认Main+新normal缺7b7d历史实现，旧07原SavedReportQuery读取失败；须由合法归档producer补齐并真实回读，不能将旧版本schema分支保留冒充历史报告兼容通过。ETH历史恢复及六组合caller/observer仍是前件，不是新研究验收。
