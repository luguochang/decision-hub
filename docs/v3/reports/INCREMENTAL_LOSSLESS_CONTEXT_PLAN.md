# 永续上下文无损共享属性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** 用已有无损共享属性逻辑消除当前永续native backstop中重复的大属性，保留全部原事实、证据、审核、身份与预算。
**Architecture:** 复用host/baseline-projection.ts的projectFactAttributes/restoreFactAttributes，原v1—v5历史wire保持原意。仅新的child-wire且存在可共享大属性时写内部protected-context.v6；无共享对象仍v5，ordinary v4不变。恢复/校验始终与独立原件全字段比较，不以模型引用授予权威。
**Tech Stack:** 原TypeScript/Vitest/SDK，无新依赖。
**Spec:** 01_REQUIREMENTS最终15/30分钟；actual07 timing-recorded-metadata.json与archive01ff6dfe…8ed23：6次compaction共380.517秒，多条历史fact重复20545字符同attributes。

## Global Constraints

- Main/07/旧包只读，既有门限/期限/TLS/Source权限/事实数/证据数/资格flags都不变；只Drepair候选。
- 原retained-context D=Main SHA15ed9bd60f0af1dbf5f2762f83f1c4eb3e973352031e28d8c162574f3bb52aac；先核工作区精确来源/无他人修改。
- 不改已有断言/fixture让测试通过；新case验证新格式，旧大属性v5仍能按旧wire鉴真。
- 不减少数据，不截断，不延迟到另一个请求；shared表包含完整原属性。fact payload_hash/source/time/IDs及child Provider lineage逐项原样。
- MAX_PROVIDER_INPUT_BYTES、child byte/count、调用预算、原None/旧版本分支保持；严禁推定字节减少就是实际时延已达标。

## Review Focus

1. 有/无/单个/小型/唯一属性：旧wire保持，只有可共享重复大对象进入新格式。
2. shared body/hash/ref/unused或重复pool项篡改：独立原件校验拒绝。
3. child prefix/Provider归因/pending delivery：新格式保持原绑定，不把pending算可信。
4. 原v5完整属性快照：按原字段和原intro校验，不被新pool重解释。
5. 同facts/captures/NativeEvents：投影前后源数据不改，restore等原canonical全部字段，财务flag保持false。

## Task 1 — 无损投影与双版本读取

**Files:** Modify extensions/dsh/decision-hub/src/host/{retained-context.ts,child-research-context.ts}; add focused tests/support，baseline-projection.ts只读复用。
**Interfaces:** 原makeRetainedRootContextMessage/verify/canReuse/replace签名保持；projection内部可区分explicit历史version与默认新选择。default先按原逻辑取得v5，认证trusted facts后仅shared pool非空时升v6；显式v5永远完整旧格式。v6保同child-wire及shared属性说明。declaredChildResearchDelivery识别v5/v6。

- [ ] Step 1: 写新RED case：真实原工具结果/child source认证夹具中两条以上相同大attributes，要求一次pool+restore全等+同child/pending元数据，原代码未pool应FAIL。
- [ ] Step 2: 最小改两production文件，复用原projectFactAttributes。旧schema/期限/权限/qualifier不动，不新通用codec/API层。
- [ ] Step 3: 新GREEN加篡改、旧v5完整wire/ordinary v4、无重复仍v5、native source不变负例；跑相关现有child/retained-baseline/race/共享属性回归。
- [ ] Step 4: 原定向TSstrict及非作者review；Root用实际07backstop只读原件做共享投影canonical还原/字节对比。实性能需以后新Run测，不跑07。
- [ ] Step 5: 仅Root精确本地提交，不自行stage/commit/采用Main；与研究内容切片分开报告，可在都验后批次构建，旧normal不能认证新源。
