# 已复核原系统源码的精确采纳（Q037）

状态：源码已采纳，尚未正常构建或部署。日期：2026-10-24；范围卡提交 `0d6a4a2`。

本轮沿用原 Main 的 UI、native 及合同，采纳已经冻结并复核的局部修复，不替换原工作台。
Main 为 `E:/project/study/codex/dsh/decision-hub`；来源为 `D:/DecisionHub-legacy-incremental`。

- 原 source peer 与集成 helper peer 均为 C0/I0；本轮 peer 未重跑测试、编译、构建或业务。
- 32 个范围文件全部完成 Main 旧字节、D 新字节及路径 preflight，71 个声明构建输入已核对。
- 写入前保存完整32条旧状态清单；27个已存在旧文件按相对目录树备份并逐字回读，5个原本不存在的文件记为 null。
- 根实际逐字复制31个文件；第32个 `src/generated-build-identity.ts` 未手工复制或修改常量。
- 根在真实 Main cwd 执行原 Node generator 一次，hidden、无 NODE_OPTIONS，实际 exit0。
- 标准生成后，32个 Main 文件全部与D冻结字节一致；71个 Main 声明输入全部匹配。
- 实际完成时间为 `2026-10-24T09:22:20.043604+00:00`；完整指纹为 `a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64`。

实际命令为 `E:/software/system/nodejs/node.exe E:/project/study/codex/dsh/decision-hub/extensions/dsh/decision-hub/scripts/generate-build-identity.mjs`。
该命令只生成构建身份，不是正常 plugin build；30秒仅为集成工具外层超时，不是产品或模型门限。
本轮没有写入 lib/dist/profile/pointer、数据库或服务配置，没有启动、更新或部署服务。

已存在的 D 验证仍为完整102个TS入口 strict零诊断、51文件556项 native通过，以及原UI八文件98项通过；这些是历史D隔离验证，不是此次 Main 路径新运行。
Main 正常 build、原完整回归及真实 Host/live-root retained/admission 尚未在本轮完成，不能把源码指纹或 generator exit0 当成运行验收。
`artifacts/d2-event-scenario-action-plans/native-event-fixture.json` 当前 Main 缺失；已封存D原件为 bceccb…e667、702654字节，待 Q038 明确 create-only 补齐，不在 Q037 偷加。

根已用实际 CIM 命令只读核对：8051端口 PID9624 是 v3 `pc_v3` 的 Vite preview，outdir 为 `D:/DecisionHub-v3-development/temp/t303-activation/pc-preview-release`。
它不是 Main plugin lib 的实际加载证明，也不占用 Main plugin lib；不能由该预览服务推断原 Main 正常 build 已经运行。

原件与精确清单：
- [实际集成收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/reviewed-original-source-main-integration/receipt.json)；同目录 `original-source/` 为相对树旧字节备份，`before.json` 为32条旧新状态。
- [32文件 source peer](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/native-source-integration-preparation/session-integration-peer-final.json)，SHA `001b6bf66f6315c5ecb4b5c8db49dbc98dd56acb459d7f961a7551147976e269`。
- [集成 helper peer](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/native-source-integration-preparation/session-integration-helper-peer.json)，SHA `005fcf930e5bcc50fd1cab9565841c61e642a8e7c7e0dac49821508fcaf5aba1`。
- [D最终完整验证](D:/DecisionHub-v3-development/evidence/legacy-plugin-candidate/39e4360b-bcdb-4dbf-a9ff-2bd848d5ea96/final-fixture-verification/sealed-verification.json)；历史候选、生成身份、中间失败与原日志保留，不覆盖。
