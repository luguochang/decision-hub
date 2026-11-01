# Q-037：采纳已验证的原 UI/native 源码差量

状态：DECIDED，2026-10-24。依据用户保留原系统和自主处理文档冲突的授权，仅把已审已测的 D 原系统修复接回 Main 本地源码，为正常构建准备真实来源；不是新建前端或服务部署。

## 来源与精确范围

71份声明输入在 D 全部仍匹配封存39e候选，指纹 `a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64`，其中 Main仅7项不同。其余384份观察中25项不同：原UI仅2测试，另为native生成身份/测试/fixture/vitest配置。两份完整前后SHA表固定为 [生产差量](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/native-source-integration-preparation/comparison.json) SHA `0503a73227819d418e103a98543e5fe4175f12407a59c9c0fca46b6254729bb9` 和 [辅助差量](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/native-source-integration-preparation/auxiliary-comparison.json) SHA `43cd824133dd2670ceefe60e1c8273aba0a618335a998620f1f96c325db914d1`。

允许 Main 精确32路径如下，D对应生产/测试原件保持只读：

- `extensions/dsh/decision-hub/scripts/generate-build-identity.mjs`
- `extensions/dsh/decision-hub/src/host/bridge.ts`
- `extensions/dsh/decision-hub/src/perpetual-synthesis-protocol.ts`
- `extensions/dsh/decision-hub/src/reviewer-model-view.ts`
- `extensions/dsh/decision-hub/src/synthesis-tool-schema.ts`
- `extensions/dsh/decision-hub/tsconfig.json`
- `extensions/dsh/decision-hub/vite.host.config.ts`
- `apps/decision-desk/src/App.test.tsx`
- `apps/decision-desk/src/research/ResearchPageRouteIdentity.test.tsx`
- `extensions/dsh/decision-hub/src/generated-build-identity.ts`
- `extensions/dsh/decision-hub/tests/build-identity.spec.ts`
- `extensions/dsh/decision-hub/tests/command-cancellation-host.spec.ts`
- `extensions/dsh/decision-hub/tests/decision-synthesis-native.spec.ts`
- `extensions/dsh/decision-hub/tests/fixtures/retained-context-race/fixture.json.gz`
- `extensions/dsh/decision-hub/tests/fixtures/retained-context-race/provenance.json`
- `extensions/dsh/decision-hub/tests/host.spec.ts`
- `extensions/dsh/decision-hub/tests/independent-review-v2.spec.ts`
- `extensions/dsh/decision-hub/tests/lifecycle-harness.ts`
- `extensions/dsh/decision-hub/tests/native-v2-persona.spec.ts`
- `extensions/dsh/decision-hub/tests/perpetual-event-feedback.spec.ts`
- `extensions/dsh/decision-hub/tests/perpetual-preparation.spec.ts`
- `extensions/dsh/decision-hub/tests/perpetual-review-capacity.spec.ts`
- `extensions/dsh/decision-hub/tests/perpetual-unconditioned-market.spec.ts`
- `extensions/dsh/decision-hub/tests/provider-input.spec.ts`
- `extensions/dsh/decision-hub/tests/provider-receipt-timeout.spec.ts`
- `extensions/dsh/decision-hub/tests/research-managed-lifetime.spec.ts`
- `extensions/dsh/decision-hub/tests/retained-context-baseline.spec.ts`
- `extensions/dsh/decision-hub/tests/retained-context-race-support.mts`
- `extensions/dsh/decision-hub/tests/retained-context-race.spec.ts`
- `extensions/dsh/decision-hub/tests/reviewer-repair.spec.ts`
- `extensions/dsh/decision-hub/tests/task-output-budget-sdk.spec.ts`
- `extensions/dsh/decision-hub/vitest.config.ts`

## 操作与验收

沿用既有 INCREMENTAL_TYPE_REPAIR、INCREMENTAL_NATIVE_FULL_REGRESSION 等真实证据：原UI98项/构建、native完整102入口strict0与51文件556项，均不是实际Host/研究/金融验收。本次根完整读7项生产差异，再非作者核来源与必要原件；不因采纳位置改变而重跑无变化整套测试。新上下文或真实正常构建若暴露问题再按原失败处理。

先核旧SHA/新SHA/新增文件真实缺席及所有目标/祖先非链接，检查受保护8047/3097无监听；32份旧文件按相对路径逐字备份至私有D（保留用于真实旧源重建），先完成全部备份才写。复制31份精确候选；generated-build-identity.ts **不手改或假填常量**，由采纳后的原 generate-build-identity.mjs 在Main以实际Node执行一次，要求71输入指纹及生成字节与已测D候选相同。每项回读、来源前后pin留证。

不覆盖 Main的lib/dist、profile/settings、installed-release pointer、已有artifacts或正式数据，不重启/停止旧服务，不运行模型/DB/完整Host。Main原工作副本很脏，禁止在Main宽暂存或提交；D已有代码提交保留，本卡和报告精确本地提交。新的正常build/retention候选要另定新Main/artifacts范围并确认真实prior来源，不借旧receipt当当前guard通过。

本卡只接已验证源码，不能称新的normal receipt、实际部署、完整报告bundle、sameRun5s或长期交付成功；目标ACTIVE，无新增ACCEPTED。


## 实施结果

09:22:20Z实际采纳32文件：27旧文件相对树备份、5原缺席；31逐字复制，1由真实Main标准generator一次生成，exit0、32最终SHA与D一致、71Main输入指纹a925ff98…01ec64。source/helper两独立peer C0I0。此次未构建lib/dist或部署，Main缺失的bce测试原件未在本卡偷补；Q038已另定正常build-only及create-only补料范围。详见 [采纳报告](../reports/INCREMENTAL_REVIEWED_SOURCE_ADOPTION.md)。
