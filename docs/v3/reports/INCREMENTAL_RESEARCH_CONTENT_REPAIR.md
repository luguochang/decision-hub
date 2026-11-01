# 原永续研究保护与观望内容微切片

按 `INCREMENTAL_RESEARCH_CONTENT_PLAN.md` Task 1 完成隔离实现与离线验证，非作者复审 C0/I0/M0。没有接 Main、构建部署或真实模型/HTTP/服务/研究运行；不改变 actual07 或任何历史原件，不计真实研究内容/重启可读/长期合格率验收。

## 基线与范围

- D HEAD：`72eb40db81af55420351a7bb688dc1b33f533b95`，开始及结束 index 为空；未 stage/commit。
- 实际 Python 文件为 `packages/runtime_adapters/dsh_runtime/perpetual_profile.py`，不是通用 `profile.py`；D/Main 基线 SHA `6b687ed73f852551b0b2677bdea2fd26d3a89b813b6369fd0a53a42332392462`。
- `extensions/dsh/decision-hub/src/reviewer-tool.ts` D/Main 基线 SHA `2825e4b745318ac96f0915fbf31268010d9235119b5f36db1d4d6a492d9b5c02`。
- 生产改动仅 Python prompt 新增42行和 TS 独审 persona 新增2段；没有生产 helper、API/schema/UI/资金与执行逻辑变更。
- 只新增 Python focused test，扩展最近 TS 完整 review 流测试。原8个 TS `it` 测试 body 的 AST source slice 逐字不变；只扩原 fixture helper 可传 rejected/具体 issues，并新增3项断言。
- 既有 untracked artifacts/evidence/temp/其他计划未整理、提交或当成业务验收证据。原 preparation 测试自带 fixture write，输出仍是 D 的既有未跟踪 synthetic prepared-fixture；未修改该测试或 fixture 原件。

## 行为变化

原 assumptions 的 `exit_policy`/`exit_ref` 与 `basis`/`basis_refs` 现在要求保护和到期计划引用同永续品种的 P_ref 类型、证据、观察/接收时间、新鲜度及真实 tick 原件。缺输入保持未知，不给默认价格、幅度或 tick。

有充分依据时，d_SL/d_TP 仍是显式主观假设：long 的 SL 向下/TP 向上 tick 取整，short 相反；取整后核方向、正值和风险，说明适用域、入场偏离重核、原目标/规则下的到期与失效。保护说明不是成交/订单，不把原终点收益比较冒称首次触达保护策略 EV，不补未来资金费、实际费率或执行可行性。

保留 no_change/hold；原 basis/analysis.thesis 与因果链 confirmation/invalidation 需要明确继续维持、考虑多、考虑空、原观望判断失效的条件。触发后是通知/后继研究与重比较，不自动开仓、反转或改概率。monitor-only 仍使用原 entry_condition codec，不挂普通 open.condition_refs；宏观/OI/funding 不支持的机器条件保留 prose/unknown 与监测缺口。

实际独审子 persona 要对实质缺失保留具体 needs_revision/rejected findings，不能造依据或强批通过。测试验证完整原 packet/真实 producer-consumer 函数能保留注入的负面原回执并拒绝 finalize；它使用离线 SDK double，未证明真实模型已理解或必然发现所有语义遗漏。

## 真实 RED/GREEN

Python 首次 focused：`9 failed, 1 passed in 4.29s`，9个缺失均为 prompt 内容业务断言 AssertionError，非导入失败。完整原 `PerpetualSynthesisDraftV1` 原先即接受既有 exit/basis/analysis 引用通道；未造该接缝的失败。

TS 首次 focused：session56509/exit1，`1 file failed, 3 failed | 8 passed (11), 12.13s`。完整 prepare 成功后，实际传给独立 child 的 persona 缺 P_ref/hold 要求；不是装配失败。

最终 Python（含原 prepare/monitor codec）：`28 passed in 5.21s`，exit0；10项新内容/原完整模型通道，18项原 codec/prepare 引用与 unsupported/unmonitored 边界。

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
& 'E:/project/study/codex/dsh/decision-hub/.venv/Scripts/python.exe' -B -m pytest -c 'E:/project/study/codex/dsh/decision-hub/pyproject.toml' --rootdir='E:/project/study/codex/dsh/decision-hub' -p no:cacheprovider 'D:/DecisionHub-legacy-incremental/tests/runtime/test_perpetual_research_content.py' 'E:/project/study/codex/dsh/decision-hub/tests/decision_product/test_perpetual_action_conditions.py' -q
```

Python 测试从物理 D 文件按真实 `__file__` 加载 prompt，当前 Main 完整模型和已有完整 fixture 只读复用，不把 D 的不完整旧 kernel 当完整 Main。最终自查将测试请求的 schema label 改为原 V2_DECISION_OUTPUT_SCHEMA_REF 并完整 model_validate；上述最终复跑通过，未改生产逻辑。未运行全项目 suite。

最终 TS（plugin cwd）：session20077/exit0，`5 files / 21 tests passed, 17.83s`。

```powershell
$env:DSH_UPSTREAM_SOURCE_ROOT='E:/project/study/codex/dsh/decision-hub/.cache/dsh-upstream/source'
node node_modules/vitest/vitest.mjs run --config vitest.config.ts tests/perpetual-review-proof-size.spec.ts tests/perpetual-reviewer-required-fields.spec.ts tests/perpetual-review-capacity.spec.ts tests/perpetual-preparation.spec.ts tests/perpetual-unconditioned-market.spec.ts --reporter=verbose --pool=forks --poolOptions.forks.singleFork
```

## 定向静态与旧基线

新 Python test：Ruff `All checks passed!`、Pyright strict `0 errors, 0 warnings, 0 informations`。两工具命令只指定该 owned test；Ruff 使用 `--no-cache`。

原 Python prompt 模块未全面静态清零：当前与 Main 基线均 Ruff 10个 E501、Pyright 12个既有参数缺注解；诊断 message/代码逐条相同，仅新增提示后旧位置偏移42行。本切片没有重构这些旧代码或改全局配置。最初新增 test 静态问题已修复；最终新 test 无新增诊断。

```powershell
python -B -m ruff check --no-cache '<owned Python file>'
python -B -m pyright --project 'E:/project/study/codex/dsh/decision-hub' '<owned Python file>'
```

TS 本次一次原 strict options、noEmit 检查 session21605/exit0：`{"rootNames":["src/reviewer-tool.ts","tests/perpetual-review-proof-size.spec.ts"],"diagnostics":0,"noEmit":true}`。总2入口（1生产/1测试）及递归依赖，不能称全项目/全部入口通过；未记录总 wall time。保留原既有8GiB参数，没有失败后加堆重试。

```powershell
@'
import ts from './node_modules/typescript/lib/typescript.js'
const project = 'D:/DecisionHub-v3-development/evidence/legacy-plugin-candidate/39e4360b-bcdb-4dbf-a9ff-2bd848d5ea96/final-fixture-verification/strict/tsconfig.json'
const raw = ts.readConfigFile(project, ts.sys.readFile)
if (raw.error) throw new Error(ts.flattenDiagnosticMessageText(raw.error.messageText, '\n'))
const parsed = ts.parseJsonConfigFileContent(raw.config, ts.sys, project.slice(0, project.lastIndexOf('/')))
const roots = ['src/reviewer-tool.ts', 'tests/perpetual-review-proof-size.spec.ts']
const program = ts.createProgram(roots, { ...parsed.options, noEmit: true, incremental: false })
const diagnostics = [...parsed.errors, ...ts.getPreEmitDiagnostics(program)]
console.log(ts.formatDiagnosticsWithColorAndContext(diagnostics, { getCurrentDirectory: ts.sys.getCurrentDirectory, getCanonicalFileName: x => x, getNewLine: () => '\n' }))
console.log(JSON.stringify({ rootNames: roots, diagnostics: diagnostics.length, noEmit: true }))
process.exitCode = diagnostics.length ? 1 : 0
'@ | node --max-old-space-size=8192 --input-type=module -
```

`git -c core.whitespace=cr-at-eol diff --check` exit0（未改 git config）。

## 冻结文件 SHA256

| 文件 | SHA256 |
|---|---|
| packages/runtime_adapters/dsh_runtime/perpetual_profile.py | 91b8db5475bea0c51cd0360382c72729c68a20079b6e06dd9dfb49e61ba6e82c |
| extensions/dsh/decision-hub/src/reviewer-tool.ts | 6f749bdf8822e5a66761ef231ab751dfa0c29f5b4aae8d290c57eb88f51d1a3a |
| tests/runtime/test_perpetual_research_content.py | 7517ae403a33a07a0edccd781ccc46221f650c1ebf3beed542c09c1fe496ac9d |
| extensions/dsh/decision-hub/tests/perpetual-review-proof-size.spec.ts | 8faae76d46825a29289f61ea39b6ab34425b9df7a81424a8cb50e539f1259171 |

Main 两源文件 SHA 仍为上述原基线。非作者 `content_review` 已完成四文件只读复核：C0/I0/M0，四文件 SHA 全匹配；确认方向/来源/未知、hold、codec 与原负面回执边界，无新增启发式 gate。未重复大型静态或构建，没有写文件。

本报告作为 Root 精确本地提交的输入；Main 采用、构建与后续真实新研究仍未执行；所有金融/执行 flags、unknown、hold 和最终验收门不变。
