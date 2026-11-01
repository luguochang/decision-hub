# Retained Context Read Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this single slice with TDD. Root owns Main adoption and shared-index scheduling.

**Goal:** 修复 ETH08 已证的 early-detail / later-child-result 读取顺序，使新 producer 在生成完整冻结输入前覆盖其已鉴真的成功 native prefix；保持旧坏输入拒绝。

**Architecture:** 仅将 `bridge.readSynthesisReviewContext` 已有的一次 researchDetail 读取，移到原 child prefix 与 Provider proof 鉴真之后。新 detail 仍使用原 fresh Session 与全部权限/截止检查；不新增读取、循环、交付缓存、receipt、schema 或鉴真成功缓存。完整 canonical verifier 保持逐字不变。

**Tech Stack:** 原 TypeScript Host、Vitest、真实 SDK codec、原 Gateway/ORM DTO mapper；无新依赖。

**Spec:** Root 2026-10-26 当前裁决及 `AGENTS.md` §61；原失败报告 `INCREMENTAL_ETH4H_V6_LIVE_RESULT.md`。本计划仅是修复/验证步骤，非新的业务资格标准。

## Global Constraints

- Main、4511/d4、原 ETH08 包和全部失败原件冻结；只写 D 候选/测试与新 Ddev 私有证据。
- 无 HTTP、SQL、模型、Agent、Host、Worker、新 Run、旧 Run 恢复或付费重试。
- Provider 冻结字节不改写；旧 v5/v6 不改语义，旧 marker348 仍须 FAIL。
- 不忽略 evidence/facts 字段；不删除新增数据；不把状态改名成功；不改变权限、PIT、预算、超时或重试次数。
- 不把纯回放/新 synthetic producer marker 当原历史成功，不计 30 例完整业务回放、六组合或最终交付。
- 不自行 stage/commit/Main 采用；Root 审核后统一安排。

## 已证单因与原件

原 Run 为 `run_7093f1de874e4b409d2d16eab7d36b6b`。

| 原件 | 已核事实 |
|---|---|
| `role-outputs.original.json`，SHA `11fb66bc…eb69b` | 111 份原角色记录；不是补造的 model input |
| `root-session.all-frames.original.jsonl`，SHA `f9c93191…58db7` | SDK 原 codec 展开 370 个连续 events |
| `evidence-and-link.original.json`，SHA `37302dd1…ae296` | 独立当前 RO 的 18 Evidence 完整行与 Link；不冒当时 HTTP 或历史 mutable quality |
| marker348 | v6，原14Evidence/31Facts、1共享 attributes pool、3真实 child prefixes；旧232经原 compaction/replace 替换 |
| child `b229a701-43a7-43d3-9aca-e632a429d300` | marker 已声明 lastSeq94；其 seq92 成功 result 含 EV09+4Facts |
| EV09 | `ev_09a4ad43bbe773b59eab54d788d1d5a3`；accepted_at `23:05:06.862092`，native result92 `23:05:06.885Z`，marker append `23:05:07.854Z` |
| 原 verifier | `retained-context.ts:348` substantive canonical equality 真实 RED；独立 current-RO projection15/35，共同项全等，仅新增 EV09+4Facts；intro 相同 |

EV09 的 call 是 `decision_hub_research`，native seq91 于 `23:04:26.365Z` 开始；原 Provider emission ref `model_emit_7b2580701bb721e3c6fde8f8edfe77ca2187fe71`，recorded_at `23:02:40.322676Z`。完整 call/ref/hash 时间证据保存在 `Ddev/temp/legacy-reuse/eth4h-live-v6-retained-replay/ev09-timing-proof.appendix.json`（SHA `566282f4…6180`）。accepted_at 不是精确 commit 时刻；没有录到 detail SQL 的毫秒时间，不能补造。

原完整 RED 在 `Ddev/temp/legacy-reuse/eth4h-live-v6-retained-verify/verify-result.json` 与 `verify-detail-result.json`。诊断脚本 exit0 表示完成诊断，业务 verifier 明确 FAIL。首次 Windows import URL 装配失败原件保留。Socket connect 尝试被拒绝，未成功网络/SQL/模型调用。

## Happens-before 依据

1. `durable_research_gateway.py:871–961` 在同一 `Database.session` 内写 accepted Evidence/Facts；`:1102–1106` 在事务上下文退出后才 `return persisted`。
2. `persistence/db.py:1759–1768` 上下文出口 commit；失败则 raise/rollback。
3. `research_mcp.py:214–229` await invoke 返回后才 HTTP200；`research-tool.ts:275–302` 完整读取、2xx、typed/identity 校验后才 return。
4. 原 SDK `tool-calls.ts` 等待 outcome/finalize 后才 append `tool/result`。因此本次 durable managed 成功结果可见时，其 Evidence/Facts 提交已经完成。
5. 最后再调用原 detail：`main.py:1256` → `ResearchQueryService.get` 新 Session → `service.py:899–921` 原排序及 `_evidence_view:2943`。它不复用早于成功结果的旧快照。

以上保证只适用于原 durable managed 成功路径。错误/unknown ACK、非 durable 或失去 accepted 的记录不因此获信。prefix 外新增仍由原 byId/声明边界处理，不能作为已送达材料进入本次 frozen projection。

## 白名单

| 路径（相对 D） | 责任 |
|---|---|
| `extensions/dsh/decision-hub/src/host/bridge.ts` | 唯一生产改动：移动原 detail/read/run-id-check/clone/freeze 整块至 childContext 完成之后、最终 link/current 检查之前 |
| `extensions/dsh/decision-hub/tests/retained-context-admission-order.spec.ts` | focused assertions 与子进程原件回放启动 |
| `extensions/dsh/decision-hub/tests/retained-context-admission-order-support.mts` | 原 Bridge 方法与真实 codec/proof 函数；Hub/registry/clock 为明确的离线调度 façades |
| `extensions/dsh/decision-hub/tests/fixtures/retained-context-admission-order/recorded.json.gz` | 真实有限闭包：必要 root/child 原 events、原 provider proof、独立 DTO/refs 与 SHA provenance；不嵌凭据 |
| 本报告 | RED/GREEN、范围、限制、peer 结果 |

生产 baseline：Main 和 D bridge 都为 `e106c3a69a047cf9159996a82a03707fa05ec3d60d2563a7980f0de7e2d6ce0b`。`retained-context.ts`/`final-review-gate.ts`/contracts/SDK/Gateway/DB 均只读，不新增 helper 生产层。

## Review Focus

- 数据在 root events 不动、child/proof await 期间合法提交：一次最后 detail 必须含其完整已鉴真成功 prefix。
- 旧历史14/31 marker 不是新15/35输入：必须继续被完整 verifier 拒绝，不能原地修复。
- prefix 外迟到记录不后吸收；缺失/篡改/失 accepted、错误 Provider/child/root/generation 仍拒绝。
- 新 session/进程重新从原 native/Provider/Core 原件构建，不依赖旧 WeakSet 对象或 pre-step 内存 Map。
- cancel/deadline/owner 变化仍在原 current/link 门拒绝；no-child/baseline/root-child 原路径与一次读取数量保持。

## Task 1: 真实读取顺序回归与唯一移动

**Interfaces:** 原 `DecisionHubHostBridge.readSynthesisReviewContext` 参数/返回类型不变；原 `verifyRetainedRootContext`、`makeRetainedRootContextMessage` 和 SDK append/derive/freeze 不变。

- [x] 从封存原件生成有限 fixture；所有保留对象逐字来源及 SHA 可核。当前 RO 的 quality 明示，不能称为当时 HTTP。
- [x] 写测试：原 Bridge 方法真实执行。只控制 EV09 的 ledger 可见性在已鉴真成功 child prefix/proof await 边界前后切换；其他数据保持，所有字段/时间不改。旧序 producer 的14/31与独立 fresh15/35完整相等断言真实 RED；不是仅断言源码行顺序。
- [x] 同时保留旧 marker348 全 verifier FAIL；新 synthetic producer snapshot 明确另建，使用原 append/derive/freeze/verify，不覆写旧 event。
- [x] 运行 focused 测试存真实 RED，区分装配错误与 AssertionError。
- [x] 仅移动既有 detail 整块；不增调用或其他逻辑，不触原 canonical verifier。
- [x] 运行同 focused 用例 GREEN：新 producer 完整15/35、full canonical equality通过；detail 总调用仍1。
- [x] 同一隔离子进程内重新创建 Bridge/session/source 对象、重新鉴真同 native/Provider 原件仍通过，不依赖前一次 authentic 对象；必要负例及原 child-attributes/baseline/race 回归完成。它不是实际 Host 重启验收，旧业务断言未改。
- [x] 定向 tsc/原配置与代码差异核验完成；最小 scope 保存 Main/D baseline/代码/test/fixture SHA，交非作者 peer 和 Root 独立审查。这里不自动提交或采用 Main。

预期命令使用 D 现有 `extensions/dsh/decision-hub/vitest.config.ts` 与原 Node/Vitest；SDK decoder 显式选物理 Main 的 `6f7e9ef6…8a7d`。所有 subprocess 的 TEMP/cache/evidence 指向新 D 私有目录；无新依赖、原 Main 写入或任何 paid/live 运行。

## 当前结论

Root 与独立 source reviewer 均已确认 commit-before-success 前提。D 唯一生产 diff 是移动原5行 detail/read/run-id-check/clone/freeze 整块；接口、读取数量、权限/current/link、原 verifier 与旧 wire 不变。候选 Bridge SHA `8fa8a6b188e9760…`，完整 SHA 见私有最终 scope；Main 仍为旧 baseline。

| 核验 | 实际结果与原件 |
|---|---|
| 旧序单因 RED | `order-red-r3.log`：1 FAIL / 12 PASS，AssertionError `expected 14 to be 15`；完整结果保存旧14/31及 fresh verifier 拒绝 |
| 第一次移动后 GREEN | `order-green.log`：13 PASS / 4.13s / exit0；late-admission 新 producer15/35，完整 fresh canonical verifier通过，detail一次，root events读取期间不变 |
| 最终强化回归 | `order-related-r3.log`：5 files / 76 PASS / 12.26s / exit0；新14 + 原 race7 / child24 / baseline9 / child-attributes22 |
| 明确拒绝来源 | 旧 bad348 明确 `HubClientError` constructor、原 native-invalid code及 `retained-context.ts:348` canonical equality；缺失/改写/失accepted 为原 retained gate，错root/generation 为原 link mismatch，错Provider/prefix 为原 child gate，cancel/deadline 为原 current binding-changed |
| prefix 外追加 | 完整原校验实际通过，不将其吸入冻结集合；no-child真实方法仍1 detail / 0 Provider proofs |
| 类型 | 最终77373同handle真实exit0/134.2325232s/0诊断，终态见 `order-types-final-terminal.json`，空log SHA `e3b0c442…b855`；沿原strict/noEmit/skipLibCheck，物理Main SDK类型路径及D contracts source alias，仅新3入口及其依赖，不冒完整Main质量门全部归零 |
| 非作者 | `content_review` 已实际只读5文件/原件/76结果，语义C0/I0/M0；只待最终scope pins核对，不重跑suite |

以上日志均在 `D:/DecisionHub-v3-development/temp/legacy-reuse/eth4h-live-v6-retained-verify/`，最终行为结果为 `order-related-r3/run-H0AZfs/result.json`。fixture gzip SHA `288301fd894cb0f3fbca3ea7fd9774be2e645caf87c21fb9f521c715768fd803`；解压字节 SHA `8b8a81b1b778b57c6ce4af7a6f4f9b238581d034a200d3747a13afd91a1efcb1`。

保留装配与校验前件失败：第一次 Vitest 路径错误、Node 动态加载旧 D2 contracts junction 无V2导出均不是 RED。新测试用与原 Vite 相同的显式 contracts→D source alias；相邻旧 race 的原 child 子进程通过私有 setup 增同一 alias，未改原测试/断言、node_modules junction或产品。首次相关套件68PASS/7skip/1fileFAIL原件 `order-related.log` 保留。错误来源强化首次4FAIL是本测试误把 `.name` 当 constructor（原 HubClientError.name为Error），随后按真实 constructor/code/frame修正；不是产品失败。类型首次可执行路径/typeRoots装配失败也保留，不计类型完成。

回放执行真实 D Bridge/retained/child 模块，同一物理 D 模块创建 WeakSet authentic 身份；未复用 Main helper的 branded对象。SDK原 codec只读选物理Main。有限 fixture、独立 mapper原件与Provider proof逐对象相同；actor权限、可见性调度、token meter是明确离线facade。只控制新 producer 构造前的合法事务可见时序，不修改任何录制值，也不把当前quality当历史quality。新 synthetic snapshot与旧坏348严格分开；这里不声称真实Host重启、完整业务回放或修复后paid Run成功。

Main/4511/d4、旧 ETH08包/运行原件未改；没有HTTP、SQL、模型、Agent或新Run。旧 bad marker、generic publication-source.v3、409读回失败及原93659 exit0/五Jobs关闭全部保持。候选尚未Main采用/normal认证；业务合格、六组合、30完整失败回放及最终交付仍未验收。
