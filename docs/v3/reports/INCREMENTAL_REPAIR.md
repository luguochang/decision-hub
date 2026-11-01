# 原系统渐进修复：首个本地补丁与交付安排

日期：2026-10-24。依据用户最新授权及 Q-027 自主裁决。项目仍在进行；本报告不代表全部任务或正式运行验收完成。

## 执行决策

以原 `apps/decision-desk` 和原研究链为交付基础，保留原界面、设置、身份与回执。先验证客观缺陷，再按责任局部修复。任务清单继续用于检查需求覆盖；不再把整体重写和更换前端当作先决条件。

原 worker 已有持久化检查点、`has_checkpoint → executor.resume`、租约与审核补证实现，文档中的“完全没有恢复”不符合源码事实；有实现也不代表已通过故障恢复验收。报告读取耦合历史源码、DSH 完整上下文与合法追加之间的竞态，则有具体证据支持。原 PAPER 已有订单、保护、事务和费用链，下一步验证和修补已有实现。

既有 v3 代码与录制保留为研发资产，只按原链缺口选择性复用。停止扩建新工作台和未经闭合的新链，不先增加全 API 翻译层。BTC/ETH × 4h/24h/72h、连续十四天、真实事件、PAPER 完整退出及恢复、真实评分等最终验收要求保留。

## 隔离与本地提交

工作区：`D:/DecisionHub-legacy-incremental`，分支 `repair/incremental-delivery`。

| 提交 | 内容 |
| --- | --- |
| `fd75223acb2f3fc2e8475c78f35481058c014b68` | 保存 Main 最新未提交前端/native/contracts 与根清单，454 个文件、21,783,426 字节逐 SHA 相等 |
| `7e504cdc65feb4ffd31a69829a4139cda05c5dbf` | 按 Main 原字节补齐九个缺失 fixture；修正两个过时测试 mock，保留并加强同 Run 业务断言 |
| `e48321874f3ea8ea3d20d75e5e1c6cf2700a778f` | 原 native 的合法追加竞态补丁及永久回归；生产差分仅 bridge.ts，+20/-2 行 |

其余 Python 后端仍来自旧 Git 基点，不能称此目录为完整当前生产克隆。原 Main 源码、8047/3097 服务、正式数据库和历史原件未在本切片中改写。未部署或发起新的付费研究。

## 本次修复

`readSynthesisReviewContext` 进行鉴真 IO 时，合法子报告可能追加到原生会话。旧快照在替换阶段与完整活跃事件序列不相等，因而被拒绝。补丁仍要求旧前缀逐字相等；发现仅有追加时，在原 controller、link 和绝对截止时间内重新读取完整鉴真上下文，再同步构造和替换。历史改写、取消、截止与旧快照替换仍拒绝。

生产文件 SHA：before `823a119f80f6b80857abf6117810537049a298d85035d9944c18803dd36ed809`；after `d7099bb3ba1aecd5acfe3cbc32c13f80b23c03ab22af6ed3d67ca64f517b3b73`。与已存在的封存补丁精确相同。不能认定它是所有历史失败的唯一根因；原失败抛出点没有完整保留。

永久 fixture 使用一个实际 Run 的三份历史原生 archive 和 39 份 Provider 原件；压缩与原文双 SHA 钉住。测试中的 registry/IO/token meter 有明确的合成接缝，历史 running link 不是当前状态。另行用真实已安装 SDK Session、TokenMeter 和原 helper 做离线接线验证，未冒称完整 Host 或模型验收。

## 验证事实

| 检查 | 原始结果 | 边界 |
| --- | --- | --- |
| 原 UI 七文件初验 | 69 passed / 8 failed | 八项失败来自过时 mock；失败日志保留 |
| 原 UI 修正后及专用进度测试 | 98 passed / 8 files，30.09s，exit 0 | JSdom 与 mock 合同；非实际研究 |
| 原 UI 完整类型检查 | `tsc -b`，exit 0 | 原 include 与 strict 范围保留 |
| 原 UI 完整打包 | `vite build`，1989 modules，11.59s，exit 0 | 29 份产物 SHA 独立复核；现有大 chunk 告警保留 |
| 合法追加竞态 RED | 1 failed / 6 skipped | 原版本实际拒绝该封闭录制重建场景 |
| 最终必要 native 回归 | 15 passed / 1 skipped，14.34s，exit 0 | 七个竞态/负例与八个原鉴真测试；后八项使用临时 loopback server，无外网或原服务 |
| 非作者最终关键回归 | 1 passed / 6 skipped，6.70s，exit 0 | beforeAll 仍执行完整录制，独立核对结果 |
| 真实 SDK 公共行为 | 4 passed | 无 Host、网络或模型 |
| 原 helper＋真实 SDK 接线 | 1 passed | 保留实际 0..592 前缀，SDK end-seed593 与合成 relay594 如实区分 |
| 完整 native strict | exit 2；31 条诊断、六个未修改文件 | 本次 bridge 与新增测试无诊断；未写为通过 |
| 补丁前完整 strict 对照 | 同样31条；逐项比较无新增、无缺失 | 102根文件与所有选项保持一致，仅CompilerHost读桥文件时返回原Git字节；对照工具exit0不等于编译通过 |

原 UI 默认约 4GB Node 堆的构建曾 OOM，exit 134；没有 TypeScript 诊断，未进入 Vite。串行 8GB 堆复验后完整通过，没有缩减检查或改业务超时。

SDK 测试默认从仓库 `.cache/dsh-upstream` 读取，也可通过 `DSH_SESSION_ENTRY` 明确注入安装位置；载入前仍核 SDK 入口 SHA。永久测试代码不含当前机器盘符。竞态子进程和独立 SDK 测试禁止网络；八项既有鉴真回归使用临时 `127.0.0.1` HTTP fixture，不连接外网、原 8047/3097 服务或模型。缺原件、缺 SDK 或 SHA 错误都会失败。

边界更正：作者在最终复读时发现，先前把“跳过一项 Harness”概括成整组没有本地 server 并不准确。原运行输出和旧清单快照保留；最终回归是受控 loopback 测试，而非整组零网络。本报告采用更正后的边界，不通过重跑或改日志覆盖事实。

默认 `git diff --check` 将原 CRLF 字节识别为尾部空白：九个原 fixture 中六个文件共2937处，本次精确原补丁20处。根逐处核实本次补丁只有 CRLF、没有实际行尾空格；保留封存 SHA，没有为格式化重写原件或补丁。相关非零退出如实保留，不称默认 diff 检查通过。

## 证据位置

- `D:/DecisionHub-v3-development/temp/legacy-reuse/ui-baseline-verification/`：原失败、98项结果、构建日志、退出码、fixture 收据、29份产物与根独立核验。
- `D:/DecisionHub-legacy-incremental/evidence/retained-context-race/`：RED/GREEN、最终15项回归、strict原始输出、五路径scope与根精确暂存核验。
- `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/`：真实 SDK 测试与非作者复核，Critical 0 / Important 0；不掩盖全局 strict 未通过。
- `D:/DecisionHub-v3-development/temp/legacy-reuse/native-baseline-verification/`：补丁前完整诊断、当前诊断、逐字段一致对照和 source pins。
- `D:/DecisionHub-v3-development/evidence/legacy-native-fixture/177d5889-6a0a-47c7-abf4-f73ba31530f7/`：实际源闭包、原文 SHA 和只读查询来源。
- `D:/DecisionHub-v3-development/temp/legacy-reuse/incremental-priorities-review.md`：最多六项后续优先级与具体源码依据。

## 后续顺序与未验收项

31条 native诊断已完成补丁前后对照，确认与原基线相同。下一微切片单独修复六个消费文件及一处现有编译别名装配：marginal采用已有强类型schema.parse；显式type-only引入已安装SDK的compaction事件声明；四个旧测试按真实V2/V3 discriminant、SDK constructor与MessageId适配。保持原dispatcher、业务断言和协议检查；不新增runtime依赖，不使用 any、忽略诊断、修改生成契约或缩减 include 来取得通过。修复后重新执行完整strict及必要行为回归，由非作者复核，另行提交。

随后核对生成构建身份所需的当前原系统配置，形成本地发布候选，再进行原界面同 Run 的提交、补证、独审及报告闭环。

UTC 授时仍须取得实际健康原件后才能进行新的真实认证；既有偏差与权限拒绝不伪造为成功。这只限制相应真实验收，报告离线回读、PAPER 临时库回归、故障恢复及必要源码修复继续推进。随后在原 GET/DTO 内处理报告历史源码依赖，并核对现存 PAPER 全期限补丁，逐项验证费用、保护退出与重启恢复。

尚未完成正式部署、浏览器与实际后端集成、新的真实研究、实际 PAPER 完整验收、十四天合格率或四周评分。本地测试和本地提交不计作这些最终指标。

自动审批曾拒绝替换隔离目录依赖链接，原因仅为 `blocked by policy`；没有执行该动作。保留所有链接，通过临时编译器 resolver 显式指向当前 D/Main contracts 与既有 SDK，生产配置与依赖均未修改；原严格检查范围继承不变。
