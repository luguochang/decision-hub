# D2 官方 DeepSeek Live 主流程执行清单

版本：`D2-CHECKLIST-2026-09-03.v1`  
状态：`completed / truthful terminal`  
唯一目标：`D2-OFFICIAL-DEEPSEEK-LIVE-CLOSEOUT`  
上级计划：[D2 官方 DeepSeek Live 主流程计划与验收清单](D2_OFFICIAL_DEEPSEEK_LIVE_FLOW_PLAN.md)  
真实证据：[D2 官方 DeepSeek Live 主流程真实验收记录](../evaluations/DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md)

> 本文是 D2 收口期间的唯一执行 checklist。它把已经完成的事实、剩余动作、BDD/TDD 验收门、停止条件和文档记录位置写成可逐项勾选的任务卡。它不授权新增领域、自动交易、Search 扩权、active pointer Promotion 或第二套 Agent Loop。

## 1. 目标和完成边界

### 1.1 本目标要证明什么

在 gitignored 的本机 live profile 中，使用官方 `deepseek-v4-flash` 和官方 DSH Web，完成一次可复核的主流程收口：

```text
DSH Web -> DSH Agent Loop -> Decision Hub research capability
  -> Evidence/PIT/Coverage -> deterministic Gate
  -> Run/Session/Trace 关联 -> DSH 人可读终态
```

本目标的完成定义是“真实主流程到达诚实终态并且页面证据完整”，包括成功、部分成功或安全失败。最终 synthesis 失败必须显示为失败/降级，不能被重写为成功的 `no_trade`。

### 1.2 明确不在本目标内

- 不修复或放宽 Gate 规则来制造方向性结论；
- 不把 `structured_output_invalid`、`evidence_stale` 或 `critical_data_unavailable` 改写成成功；
- 不新增 Search/Official/Market 供应商、外部收费服务或未审计插件；
- 不切换 Fixed active、DSH candidate/shadow，不做自动 Promotion；
- 不接入 ASR、PPT、A 股/美股、自动交易、多用户或远程高可用；
- 不复制 DSH Web 源码，不写第二套 Agent Loop，不以 LangGraph 替代 DSH 内层 loop；
- 不修改或删除历史 Run、Evidence、Artifact、Forecast、Outcome、JSONL、migration；
- 不提交、不 push，除非 owner 另行明确授权。

D2 完成后仍只能写：`engineering flow passed / synthesis failed safely / product direction pending`。这不等于方向预测成立、产品正式可用、盈利或 DSH Promotion。后续 E3 价值观察必须另立目标和 owner gate。

## 2. 当前基线（不可重复解释为待实现）

| 能力 | 当前事实 | 证据 |
|---|---|---|
| 官方 Provider | `/models` 和最小 `/chat/completions` 均 HTTP 200，目标模型存在 | D2 真实验收记录 §2 |
| DSH 主流程 | 官方 Web 建立受管 Session，Agent Loop 执行 3 轮补证 | D2 真实验收记录 §1/§4 |
| capability | `official.macro`、`market.cross_asset`、`market.crypto_derivatives` 成功；失败 provenance 保留 | D2 真实验收记录 §4 |
| 账本 | 12 次工具调用、12 条 Evidence、PIT/coverage/Gate/Artifact 持久化 | Run `run_6ea4b5b7c20140e2b4c0109d36b0179a` |
| 安全终态 | `degraded/reject`，synthesis `structured_output_invalid`，不发布 causal/horizon | D2 真实验收记录 §5 |
| 插件重复渲染 | 已删除 `conversation.composer.dock` 的完整报告注入；源测试 57 passed | `extensions/dsh/decision-hub/src/client/index.js`、client tests |
| 待收口 | 当前 D2 运行专属移动截图、console 结果和最终质量门复跑 | 本清单 §4 |

## 3. 不可违反的工程约束

1. DSH 是唯一 Agent Runtime、Session、Trajectory、Tool/Skill/Subagent 和 JSONL 所有者；Hub 只拥有业务账本、Evidence、PIT、Gate、Artifact、Forecast、Outcome 和 Query/View。
2. LangGraph 只管理 Hub 外层 durable 生命周期、checkpoint、lease、恢复和 bounded evidence round；禁止在 Hub 再实现 ReAct/tool loop。
3. Agent、模型和插件只能提交候选；确定性代码 Gate 是唯一发布裁决。
4. 跨模块数据只走 `contracts/schemas/` 的 canonical schema，改 YAML 后运行 codegen；禁止手改生成镜像、裸 `dict/any` 或绕过运行时校验。
5. 三时间戳 PIT 铁律：`cutoff_at`、服务端生成的 `effective_observed_at`、`received_at` 分离；模型自报时间不能通过 PIT。
6. capability 失败按 `origin/cause_code/capability_id/tool_call_id/retryable` 保真投影；一个调用失败不能吞掉其它成功结果。
7. 原始 DSH JSONL、Hub Ledger、LangGraph checkpoint 三份状态分离且只增不改；页面默认展示人可读 Query/View，不倾倒 raw JSON 或 Provider payload。
8. 凭据只存在 `data/dsh-live/.env`（权限 `600`，已被 `.gitignore` 忽略）；Markdown、日志、SQLite、截图、JSONL 和 Git 不得出现密钥。
9. 每次修改必须同步受影响模块 README、阶段状态、`CURRENT_STATE`/`CURRENT_DECISIONS` 和 `CHANGELOG`；临时截图、日志、导出放 `tmp/`，不进入 Git。
10. 发现架构矛盾、需要放宽 Gate、复制 loop、修改历史数据或增加外部授权时，立即停止并记录 ADR，不用局部补丁掩盖。

## 4. 执行任务卡

### D2-F1：新 bundle 桌面页面验收

**目的**：确认修复后的官方 `conversation.view` 只显示一个完整研究报告区域，composer 不再重复注入。

- [x] 以新插件 build 启动隔离 DSH Web（当前临时端口 `52680`，不得与旧 Tab 混淆）。
- [x] 打开本次真实 Run 对应的 DSH Session，等待页面终态。
- [x] 保存 `tmp/d2-dsh-report-new-desktop-20260903.png`。
- [x] DOM 断言：`Decision Hub 研究报告` region 数量为 `1`，`conversation.composer.dock` 数量为 `0`。
- [x] 可读断言：Gate、hard coverage、失败原因、Evidence 数、Tool 调用数和停止状态均显示；无 raw JSON、密钥或完整 Provider payload。

**通过证据**：截图路径、viewport、bundle hash、DOM 计数、console `error/warn` 摘要写入验收记录 §6。

### D2-F2：新 bundle 移动页面验收

**目的**：确认真实终态在窄屏仍可读且不改变业务语义。

- [x] 使用 browser 工具把同一页面切换为 `375x812`；不得新建第二个研究 Run。
- [x] 保存 `tmp/d2-dsh-report-new-mobile-20260903.png`。
- [x] 检查只有一个报告 region，无横向溢出、遮挡或按钮跳动。
- [x] 检查 Gate `拒绝`、`67% hard`、失败原因、Evidence/Tool 数、研究停止状态可见。
- [x] 读取页面 console；`error` 为 0；停止临时实例产生的已知重连 warning 已记录并与新增问题区分。
- [x] 完成后调用 viewport `reset()`，避免污染后续页面验收。

**通过证据**：移动截图、`scrollWidth <= clientWidth`、关键文本断言和 console 摘要写入验收记录 §6。

### D2-F3：运行环境和进程收口

- [x] 通过 `tools.write_stdin` 向临时 `52680` 进程发送 `Ctrl-C`，确认已停止。
- [x] 确认没有残留本次 DSH 临时进程；不停止用户正在使用的其它端口。
- [x] 确认 `data/dsh-live/.env` 权限仍为 `600`，只记录存在性，不打印值。
- [x] 确认 `tmp/` 资产未被 Git 跟踪；不删除历史 `data/`。

### D2-F4：最终质量门复跑

按顺序执行，任何失败都必须保留原始输出并先修根因；不得只改测试期待值：

```bash
pnpm --dir extensions/dsh/decision-hub test -- --run
pnpm --dir extensions/dsh/decision-hub build
perl -pi -e 's/[ \t]+$//' extensions/dsh/decision-hub/lib/client.js
./.venv/bin/pytest -m "not live" -q
./.venv/bin/ruff check .
./.venv/bin/pyright
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/python tools/docs/check_module_docs.py
git diff --check
```

> 插件构建脚本会在生成的 `lib/client.js` 末尾写入空白；清理只作用于生成产物，不是业务逻辑修改。

### D2-F5：文档和状态收口

- [x] 在 `docs/evaluations/DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md` 记录 F1-F4 的命令、结果、截图 hash、DOM/viewport、console 和进程收口。
- [x] 将 D2 计划中的浏览器资产 checkbox 从 `[ ]` 改为 `[x]`。
- [x] 更新 `docs/IMPLEMENTATION_STATUS.md`、`docs/context/CURRENT_STATE.md`、`docs/context/CURRENT_DECISIONS.md` 和 `CHANGELOG.md`，保持 `synthesis failed safely`、`research_only`、`Fixed active`、`DSH candidate/shadow` 原文不变。
- [x] 更新 `INDEX.md` 链接和状态；下一阶段指向 E3 价值观察，而不是自动进入 R3。
- [x] 运行 `git status --short`，确认没有密钥、运行数据库、截图或日志被跟踪。

## 5. SDD/BDD/TDD 执行规范

### SDD（先定规格）

本次只允许修改页面重复投影、验收资产和状态文档。若需要改 canonical 字段、错误语义、Gate、DSH 上游或 capability manifest，先新增/更新 ADR 和 schema，再停止本目标等待 owner gate。

### BDD 退出场景

```text
Feature: D2 真实终态页面
Given 官方 DeepSeek 探针通过且本次 DSH Run 已到 terminal
When owner 打开该 Run 的官方 DSH Session
Then 页面只显示一个 Decision Hub 研究报告区域
And 显示真实 Gate、coverage、Evidence/Tool 数和失败 provenance
And 不显示 raw JSON，不把 synthesis failure 显示为成功 no_trade
```

```text
Feature: 窄屏和失败可读
Given 同一失败 Run 在 375x812 viewport
When 页面加载完成
Then 无横向溢出、console error 为 0、关键停止信息可读
And composer 不重复渲染完整报告
```

### TDD 断言

已有回归必须继续通过：

- `client.spec.ts`：完整报告只由 `conversation.view` 注入，composer dock 不存在；
- DSH/Hub 集成测试：Run/Session/Tool/Evidence/Coverage/Gate 关联一致；
- 失败终态测试：`structured_output_invalid`、`evidence_stale`、`critical_data_unavailable` 保留且不产生 causal/horizon；
- 静态/契约/模块文档检查：防止生成镜像漂移和约束丢失。

本目标不新增“修复 loop”的普通 runtime repair test。若未来要修 synthesis，必须先建立独立 stage、失败样本和 owner gate。

## 6. 停止条件、回滚和人工介入

满足任一条件立即停止当前目标，并在验收记录写清 `blocked_reason`：

- 页面只能显示“证据不足”而无法区分 Provider、capability、PIT、synthesis 或 DSH 错误；
- 浏览器资产来自旧 bundle、旧 Session 或旧 Run，无法证明与本次 D2 关联；
- 需要改写历史账本、放宽 Gate、复制 Agent Loop、修改 DSH 上游源码或新增未授权 Key/域名；
- 质量门失败但原因无法定位到本次改动；
- 临时进程无法安全停止，或发现凭据进入 tracked 文件。

人工只需在以下两类情况介入：

1. 浏览器需要 owner 观察页面是否符合可读性预期；自动化负责截图、DOM、viewport 和 console 证据。
2. 发现架构/授权决策需要改变时，由 owner 决定是否新开 ADR/阶段；本目标不自行扩大授权。

## 7. 目标完成定义和后续路线

### D2 完成定义

```text
[x] F1 桌面新 bundle 资产通过
[x] F2 移动新 bundle 资产通过
[x] F3 临时进程/凭据/临时资产收口
[x] F4 Python、插件、静态、契约、文档、diff 质量门通过
[x] F5 验收记录、状态、索引、变更记录同步
```

完成后产品状态仍为：

```text
research_only / single-owner / read-only
Fixed active / DSH candidate-shadow
synthesis failed safely / product direction pending
```

D2 后唯一允许的下一阶段是 E3 前瞻价值观察（至少 14 天或 20 个高影响事件），并需另立阶段卡，定义事件采样、PIT 对照、延迟、失败率、成本、owner 人工时间、Outcome/Brier、方向性结果和停止门。E3 未通过前，不开发更多领域、插件市场或自动化交易功能。

## 8. 变更记录

| 日期 | 变更 | 原因 | 证据 |
|---|---|---|---|
| 2026-09-03 | 新建并完成本 D2 执行清单，收敛 F1-F5 和 SDD/BDD/TDD 门 | 将“真实主流程已到诚实终态”和“方向性产品输出未通过”分开，防止目标漂移 | 本文、D2 计划、D2 验收记录 |
