# Q-044：当前 plugin 完整输入合同与最终 verifier 对齐

状态：用户已授权按证据自主裁决；本卡授权 D 隔离实施，尚未修复/接 Main/构建成功。

## 实际问题与裁决

Q042 原正常 build 在 2026-10-24 10:40:44Z 退出 1。UI/Host 各 exit 0、builds=2，最终 `product_provenance_plugin_inputs_invalid`；旧 verifier 枚举 43 项，而当前 generator 登记 71 项，新增 28、旧项缺失 0。独立按原 framing 重算 71 项仍为 a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64。245212B 临时收据未通过且已由原失败流程删除，不能据此宣布有效 normal receipt。新 544 产物与旧 525 备份均保留。

依据原系统渐进交付授权，修正当前完整输入合同。不得把当前声明本身当完整性权威，不允许任意额外项/缺项/旧 43 子集，不改历史 retained codec 原件。最终仍必须走原正常 producer 与实际 verifier。

## 精确允许范围

- `packages/kernel/decision_hub_kernel/application/product_release_provenance.py`：Main SHA b0b8c17c1c8eda55a81920d8b7e4fb4baef2c12fe9770078601d0fde1fc8652d，836 行；D 原不存在，先逐字基线提交，再实施。
- 新 `tests/market_risk/test_product_plugin_inputs.py`：聚焦当前完整集及负例，≤300 行。
- 本卡、`docs/v3/reports/INCREMENTAL_BUILD_INPUT_CONTRACT.md` 和具名临时测试/证据目录。
- Main、旧 tests、generator、schema、archive 解析、SQL/CAS、所有原历史源与收据保持只读。Main 集成由 Root 待当前依赖测试终态后单独核 SHA/备份执行。

旧模块/主 verifier 已超过行数上限，仅允许局部输入合同修改及必要小 helper，不重排整个 836 行模块；新增 helper ≤60 行。不新增依赖或抑制诊断。

## 最小实施计划与验收

1. 保存真实 Main 来源、D absence 和基线 SHA；只提交具名基线/本卡。
2. 先写实际 RED：当前 src（排除 generated identity）+ contracts_ts/src 全部真实文件 + generator 固定 13 项构建控制须精确匹配当前输入。测试不能依赖硬编码 71 数字代表未来完整性。
3. 仅修 expected closure 的推导，保留既有 inputs exact-list/string/duplicate 判定、源 membership、相对路径/link/变更检测、name+NUL+byte length+colon+bytes+NUL 哈希 framing、产物 build hash、receipt/transport/owner 验证与所有容量上限。
4. 定向负例覆盖缺控制/contract/源、额外项、重复/非字符串、声明未绑定来源与内容/hash 改变；路径/link 及旧 transport 相关回归按实际影响选取。不得通过 mock 掉完整性 guard 伪称真实 release。
5. 同 runner GREEN；定向 Ruff/真实 strict 对新 helper/test 给实际结果，旧大模块 baseline/candidate 同配置前后比较。非作者 review 后 Root 精确提交。
6. 只有受控 Main 集成后，新目录/当前 544 输出完整备份/8GiB 构建子进程预算下的一次原 producer 与实际 verifier 才能产生新合格收据；不得手写收据、复用失败 tmp、伪造 builds=2 或切换 installed pointer。

Q042 原件见 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q042-normal-build/final-receipt.json` 与 `plugin-input-contract-readonly.json`。本卡不宣布 Host/研究/PAPER/长期金融指标通过，目标 ACTIVE。

## 与 Q043 合并核验的 Main 边界

为避免对同一候选重复执行长 normal build，允许 Root 在两卡定向/strict/非作者 review 后进行一次具名 Main 源码采纳并统一构建。采用前必须核所有旧 SHA/新文件 absence、完整逐字备份，并将 prior49c 仍引用且将被修改的原始 codec/saved-report/API decoder（以及经逐 SHA 实证确需保留的旧测试原件）create-only 补到 `artifacts/incremental-delivery-20261024/original-sources/` 的相同相对路径；现有18原件不覆盖。它们必须确与 prior49c 原 sources 映射一致，不能用新候选重造旧源。

当前544实际产物与2辅助文件（tsbuildinfo、generated identity）已只读备份，共546文件/118989557B；新manifest SHA55554288c463ed62dbf5ae975c70972887ee27e08beb3c9750a70dc912ed62a3。初备份脚本把544误认为包含2辅助，集合断言在创建目录/复制之前拒绝；后按实际库存区分544+2后完成，未改Main输出。新正常构建仅用全新 `guard-build` 目录，旧失败目录/备份保留，编译子进程仍8GiB且启动前free≥10GiB；不复用已失败的tmp收据。
