# Q-038：原正常构建候选与旧源补料

状态：DECIDED，2026-10-24。用户已授权自主处理常规文档冲突并持续执行，本卡是执行者对可复核范围的裁决，不另请求例行批准。目标是取得与当前Main实际源码和产物一致、含真实retention来源的正常build-only回执，供后续私有API/Host验证。不会运行完整run-product或部署旧服务。

## 已有依据

Q036类型补全和Q03732源采纳已实际接Main；当前71 native输入指纹a925ff98…01ec64，标准generator结果逐字匹配已审D候选。installed locator仍指真实49c receipt，SHA `2bc144fc4bdc3dcaa11766aea1927d71e9d8dfe32753baaad9a71d7138d567e6`。旧receipt来源已改变，不能当当前安装guard通过。

[可获性清单](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/prior49c-source-availability/availability-final.json) SHA `82578b007317c7b48c40a43b9308c01bd70d10626455453772fe53e9860788d0`：raw sources1790全部可获、缺件/读取错误0；1772当前Main，另外18有真实exact旧SHA原件。此核查未展开63499历史来源，也未通过完整正常guard。

## 精确可变范围

1. 全新 `Main/artifacts/incremental-delivery-20261024/original-sources/`，仅下列18个原相对路径；从上述清单实际来源读旧字节、按49c expected SHA重新核验并create-only复制。不装作完整当前源码克隆，不改写receipt/registry身份：

- `apps/decision-desk/src/App.test.tsx`
- `apps/decision-desk/src/product/PerpetualSavedReport.tsx`
- `apps/decision-desk/src/research/ResearchPageRouteIdentity.test.tsx`
- `apps/hub_api/main.py`
- `apps/hub_worker/main.py`
- `extensions/dsh/decision-hub/scripts/generate-build-identity.mjs`
- `extensions/dsh/decision-hub/src/generated-build-identity.ts`
- `extensions/dsh/decision-hub/src/host/bridge.ts`
- `extensions/dsh/decision-hub/src/perpetual-synthesis-protocol.ts`
- `extensions/dsh/decision-hub/src/reviewer-model-view.ts`
- `extensions/dsh/decision-hub/src/synthesis-tool-schema.ts`
- `extensions/dsh/decision-hub/tsconfig.json`
- `extensions/dsh/decision-hub/vite.host.config.ts`
- `packages/kernel/decision_hub_kernel/application/perpetual_advice_query.py`
- `packages/kernel/decision_hub_kernel/application/perpetual_manual_execution.py`
- `packages/kernel/decision_hub_kernel/application/perpetual_paper.py`
- `packages/kernel/decision_hub_kernel/application/research_graph_recipe.py`
- `packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py`

2. 原正常producer全新输出 `Main/artifacts/incremental-delivery-20261024/normal-build/`；调用 `infra/dsh/build-product.ps1`，传真实49c path/SHA及上述Main/artifacts旧源树，**不传** InstallSpotResearchV3、CapturedV3Preparation或任何DB路径。只允许原producer实算、create-only发布的 `packages/kernel/research_recipe_versions/<computed-version>/release.json,root/*`、`registries/<computed-sha>.json`、`artifacts/product-provenance/{shards,indexes}/<computed-sha>.json`；旧immutable字节如冲突必须失败，不覆盖、不删历史、不猜SHA。
3. 正常构建会重建 `apps/decision-desk/dist/*`、`extensions/dsh/decision-hub/lib/*`，写 `apps/decision-desk/tsconfig.tsbuildinfo`（若存在）及标准 `extensions/dsh/decision-hub/src/generated-build-identity.ts`。先验证两个输出目录及祖先真实路径均在Main且非reparse/link，逐文件完整备份所有既有字节与清单至私有D；Main正常build只各执行一次UI和host，旧输出回滚材料保留。允许构建器常规node_modules下的临时/编译cache，但不安装/更新任何依赖、lock或package元数据。
4. 既有D已审合成native测试原件可create-only补 `Main/artifacts/d2-event-scenario-action-plans/native-event-fixture.json`，精确SHA `bceccbcb05f3b40da1c8ad533ac0ffc099dd4febb3396ee0eb81b086a87fe667`。它只补既有测试材料，不是真实事件/成交/资格数据，不据此称Main已556通过。

## 验收和不变项

执行前核guard/producer关键源码、prior metadata/pointer和当前native71输入；检查8047/3097无监听。8051已实际确认为PID9624的pc_v3静态预览（D私有outDir），不是Main plugin服务；不停止或改它。保留原profile、settings、installed-release与V3 installed pointers、正式DB、旧artifacts内容。

保持正常producer的source-stability、retention union、实际构建、产物/来源guard及2MiB回执边界；不新造通过receipt或放宽阈值。任何失败原日志/stage原样保留，先定位再处理，不盲跑第二次。build-only成功也仅candidate，仍须核新receipt、native实际解析与私有Host业务；不得称已部署、完整V5 bundle/整体strict0/sameRun5s/真实PAPER/14天或四周通过。目标ACTIVE。

## 实际终态

唯一一次原 build-only 于09:35:51Z开始、09:40:41Z以exit1结束，289.2326s，在retention的finite initial声明解析失败；UI/Host各attempt0、无新validated receipt。525旧输出逐SHA未变，18旧来源、71输入、具名producer/pointer等前后同。失败目录保留，不盲重跑。详见 [真实失败报告](../reports/INCREMENTAL_NORMAL_BUILD.md)；Q039按实际契约复现和最小修复，后续只能在全新validated-build目录执行。
