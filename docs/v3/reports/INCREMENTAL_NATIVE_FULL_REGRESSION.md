# 原native完整回归的后续修复

状态：两处生产修复及其测试已冻结，新候选已构建。最后两份测试夹具修正后的完整strict零诊断，完整51文件、556项测试全部通过。前置构建候选、548通过/8失败的中间结果和第一次完整范围失败均保留；离线GREEN尚不代表实际Host、发布或长期验收通过。

两处生产修复、9份测试/helper、generated identity、对应产物和本报告已本地提交 `6b3cadee0ed343c6f39d2c96d9f6764f21f821ff`。精确暂存20路径；默认diff-check对保留CRLF给390行提示（exit2），显式保留原CRLF的空白检查exit0，没有格式化整文件或改Git全局配置。该提交保留已知8项失败，不是release或正式ACCEPTED。

## 已证实的问题与精确范围

按Q-027自主判断，以当前Main原代码和跨语言契约为据修复测试输入；真实产品缺陷另行最小修复，不能把所有失败都算过期测试。

- 根仅改 `tests/provider-receipt-timeout.spec.ts`、`tests/perpetual-event-feedback.spec.ts`：原Main client/route已统一legacy 60000 cap，保留真实传输默认值/超时/重试；更新边界测试覆盖各phase接受60000、拒绝60001。原TS与Python均已对 unavailable/null 只派生自身事件identity，测试保留foreign拒绝并验证不写入概率、原稿不变及原稿hash。
- A改 `tests/host.spec.ts`、`tests/lifecycle-harness.ts`、`tests/command-cancellation-host.spec.ts`：补充实际生产新增的fresh Link/terminal authority关联夹具，保留权限/取消/恰一次/跨代断言，后台取消按既有异步HTTP契约等待。`tests/command-lifecycle.spec.ts` 原断言纳入回归，文件未改。必须先定位失败，不得给生产默认宽松证明。
- C仅改 `tests/independent-review-v2.spec.ts`、`tests/reviewer-repair.spec.ts`、`tests/native-v2-persona.spec.ts`、`tests/decision-synthesis-native.spec.ts`：明确实际candidate协议ref、给negative reviewer完整当前findings、按完整工具union定位历史协议分支，保持原语义断言。
- C另获唯一生产路径 `src/synthesis-tool-schema.ts`：完整model-visible schema容量probe真实107583字节，超过原64000限额；416个本地refs可解析。保留三动作、全部字段/描述/协议语义/64000阈值，只做JSON Schema共享定义因子化。必须保持完整schema容量断言，不能换成28823字节历史子集冒过门，不改生成合同。
- A定向3spec首次93通过/1失败后证实第二个真实产品缺陷：已acknowledged terminal的cancel/inspect只投影权威状态，却留registry在旧的非终态，使下一合法generation被409拒绝。追加tuple精确RED为1失败/1通过/9未选，实际局部registry是admitted而非静态推测的running；foreign ACK不污染负例已通过。追加授权唯一生产路径 `src/host/bridge.ts`：仅在已有fresh Link/current-binding检查及projectAcknowledgedStatus严格校验之后同步state/lastSeq/errorCode，不改terminal时间、取消/closing准入或跨代检查；保next-generation原断言、加registry三元组与mismatched-link不污染负例。

上述路径相对 `extensions/dsh/decision-hub/`。不改原UI、Main生产代码、服务、数据库、真实模型输入预算或已有时限。生产schema变更后，必须生成新的身份和候选，旧 `fd1eef5` 产物保留为历史候选，不能仍称匹配修改后的源。

## 验收安排

1. 三组定向修复各保存真实失败和修复后输出；非作者复核。
2. 正式生产源有变化时先按原三阶段重建新的候选并保存原件/逐SHA来源，补核实际module、map与公开入口冷加载。
3. 单独运行原完整strict，无收窄；完整51文件Vitest范围以8GiB继承内存配置串行执行，保持原产品超时和断言。上次4GiB OOM与失败日志保留；仅增加测试进程内存不能计作产品逻辑修复。
4. 如有新的失败继续按证据修复；未通过前不发布、不标功能验收完成。受控闭环不计真实Host/14天/PAPER/四周指标。

根的2spec第一次18项中13通过/5失败，日志位于旧candidate的 `bounded-f4eba48b-ca9a-4981-8518-197534136faf/`。事件3项均通过；Provider的新legacy上界测试通过，但另4个旧emission预算断言和1个只推进30001ms虚拟clock的用例仍按旧30秒预期。当前Main/D `provider-input.ts`同SHA `3aac7d7916566cea0b171001cd19e102f704f32152713c95059d419f3b6f4e0f`，既有emission cap为60000。仅将预期对齐现有值、虚拟clock推进至60001ms；真实测试默认5000ms、产品budget/timeout/retry均不改。原失败证据保留，其后重跑结果见下。

## 冻结后的实际结果

- 根2spec后续18/18通过、8.08s、Node0，见旧candidate的 `bounded-d2602769-3425-45a9-9efd-c173ed9aec3c/`。随后为内部defs重排，事件测试描述断言改为本地解引用，沿真正prepare/draft/event字段读取并核draft协议版本；三条原描述检查保留。该最终版本另3/3通过、7.98s、Node0，见 `bounded-908efda7-21f8-4c31-b797-f5c51552699d/`。
- A3spec最终95/95通过、Node0、9.594s。生产仅inspect ack净增4行，严格projection后同步局部三元组。之后恢复bridge/harness原CRLF，仅行尾变化的证明及前后SHA已保存；95GREEN对应恢复前LF字节，最终统一strict/full覆盖最终字节，不冒称同SHA。见 `temp/legacy-reuse/native-host-suite-diagnosis/`。
- C4spec最终45/45通过、Node0、10.13s。完整model-visible schema真实 **63346字节、79个共享定义**，原64000门不变；三动作/完整字段、property-map和enum/default字面数据、并存ref sibling注释与约束均覆盖。仅随后恢复independent-review测试原混合行尾（76CRLF+1LF），语义字节一致收据保留，最终统一检查覆盖。

Schema修复为当前位置感知的JSON Schema投影变换：局部ref归一、共享定义、一次引用的内联与内部短键，实际输入wire和Zod业务校验不改。原107583B数据完整封存在 `schema-fixture-repair/unfactored-schema.json`，SHA `306777261292d09ac391cf5cee90a8af74a5f330fc6fb0cdf9cfb5ee62ed1511`。旧展开器遇ref直接返回，漏掉原outcome字段并列description；已纠正为保留disjoint sibling与同时约束。原完整schema独立正确展开390899字节，SHA `90c98ccc72d3b57305178e495ccba2ace4f5845938335a4497558b82e367ba26`，压缩后匹配；原忽略siblings的390125B/f0de摘要只保留为错误版本，不作最终等价依据。注释没有为了体积删除。

各轮真实中间失败（107583超限、初次88621超限、fixtures缺PIT、归一首次拒ref+sibling）均保留，未改成通过。C五路径、A四路径和根两路径均有非作者只读复核C0/I0；复核者未假称重跑runtime。源与收据位置：

- `evidence/legacy-sdk-review/schema-fixture-repair/scope-final.json`，五路径；`session-schema-peer-final.json` 为独立原schema展开及源码复核。
- `evidence/legacy-sdk-review/native-host-four-path-peer.json`，四路径；A scope在前述diagnosis目录。
- `evidence/legacy-test-contract-review/review.json`，根两test最终SHA与实际TS/Python合同依据。

上述evidence均位于 `D:/DecisionHub-v3-development/`，不在正式Main数据目录。

## 新构建候选与该轮完整验收（历史结果）

新候选目录 `D:/DecisionHub-v3-development/evidence/legacy-plugin-candidate/39e4360b-bcdb-4dbf-a9ff-2bd848d5ea96/`。重建前已校验旧候选15文件全SHA并在新目录 `before/lib` 原样备份；无链接的D/lib绝对路径再次确认。旧39e之前的 `53d0c4a6-...` 完整证据及产物仍保留。

```text
node scripts/generate-build-identity.mjs             exit0  0.125s
node --max-old-space-size=8192 <vite> build --config <observer>
                                                   exit0  7.032s
node scripts/build-client.mjs                       exit0  0.062s
15 output files, 27,904,552 bytes
source fingerprint a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64
```

71声明输入构建后逐SHA未变，实际module/map来源观察启用，未改生产转换或build参数。四个编译公开入口独立禁网冷加载Node0、0.469s、无网络拦截尝试；不调用apply/Host/Provider。client wrapper与全部输出另由非作者核验，不把冷加载当实际Host成功。

```text
node --max-old-space-size=8192 <typescript>/bin/tsc
  --project <new-candidate>/strict/tsconfig.json --pretty false
actual exit 0; 103.515s; log 0 bytes
```

继承原全部include/strict/noEmit/skipLibCheck，只显式定位既有SDK声明与D合同；完整编译文件清单再次覆盖当前102个TS入口，无收窄。该结果对应最终CRLF/mixed-EOL及根事件描述解引用版本。后续若再修Provider/容量测试，必须单独验证新字节，不能沿用此strict结果冒称已验。

```text
node <vitest>/vitest.mjs run --config <new-candidate>/suite/vitest.config.mts --reporter=verbose
Test Files  2 failed | 49 passed (51)
Tests       8 failed | 548 passed (556)
Duration    85.52s
actual exit 1; wrapper elapsed 86.562s
```

原产品/测试超时未改，worker内存继承8GiB；这次51文件均完成、没有worker OOM。原4GiB中断日志保留。新原始log SHA `ba9ea3872d3d4bb4d99ddbb6d42a2d4de9546b3846793f3747fe82301b1cc4f8`。

该轮剩余两文件：`provider-input.spec.ts` 七项（首轮清点漏掉这一组，随后补入范围）和 `perpetual-review-capacity.spec.ts` 一项（首轮未完成的文件，动态proof与封闭capacity原件发生plan identity mismatch）。当时不能称生产已通过；追加范围只允许这两test的诊断/对齐，禁止降低身份校验、输入容量拒绝、未知提交不重试、完整catalog/180000填充/800000上界。其后修复及最终完整GREEN见下文，本段失败保留为历史结果。

## 原研究到报告的复用判断

只读扩查已找到现成同Run测试 `tests/decision_product/test_perpetual_current_account_consumers.py::test_settled_v3_full_material_prompt_writer_advice_manual_and_frozen_history`。其PC POST receipt带入原request_setup/native/Graph/commit/savedreport，再以receipt.run_id重开冻结报告。之前只选三测试而推断缺接线测试的结论已被更正；无需为此重新创建业务主线或复制637个静态上限文件。

下一步可以只读使用Main当前Python来源、D临时数据库/原件/cache与D修复native作为明确装配，但必须先封闭所有写路径和子进程环境。该现成测试包含受控Provider/市场/时间，不是实际付费Host或真实PAPER资格。

临时装配准备在 `D:/DecisionHub-v3-development/temp/legacy-reuse/same-run-execution/`，原业务fixtures/断言不改、精确重定向原native子进程。独立审阅发现读守卫重入，已用单D文件小探针真实RED RangeError→同步重入标记最小修复→GREEN0，原读/写边界保留。七文件最终scope与非作者复核已封存。该准备阶段尚未运行完整装配，不把守卫探针算业务闭环通过；随后执行的实际结果见末节。

## 最后两份夹具与完整GREEN

最后只修改 `tests/provider-input.spec.ts` 与 `tests/perpetual-review-capacity.spec.ts`，本地提交 `6cfde0fca1944bb40615e23abf1e3caed50fe019`，68行增加/24行删除。没有再修改生产源码、真实模型预算或构建输入，因此继续使用39e候选，不做无必要重建。

Provider测试补齐当前legacy协议、不可变原deadline、确定性root/request身份及当前回调ABI；UTF8超限数据真实超过既有8MiB门，未降低阈值。Harness认证仍单独消费一次，普通input/finish仍留存。Bridge正例明确覆盖真实恢复codec下的managed-child hook及parent lineage，并增加未恢复root不delegate、child不能取得root synthesis权限检查。独立root/call/arguments成功与负例保留；该hook测试不冒称完整live-root retained-context/admission垂直验证。定向26/26通过，7.68s，Node0。

容量失败来自不同run的动态proof和历史catalog混用。原容量生成器从实际提示词提取的是19条selected catalog；新闭合测试使用同一份已封存fixture，经真实prepare和selector从23条中得到完整19条提示目录，保留全部server材料，严格验证assumption/reasoning/main/opposite引用。只在clone的policy添加明确标注的180000字符受控传输填充，保留原字段并计算匹配hash/ref，不把填充当市场证据。原六项容量、完整catalog、padding和超限拒绝断言不变。三份历史原件不改；首次23条中间失败保留，最终1/1通过，10.12s，Node0；catalog556745B、prompt655081B。

两份最终源SHA分别为 `bd1cfdca68aa8ad5338bc40cc7d3f251c9481c1d7a0a7a4cac4eedeff78f1df3` 与 `7b3f0a6fecf893378854beafb482bf6039ee2d25df1168e183ebd5d12d04489a`。两位非作者只读复核分别C0/I0：`evidence/legacy-sdk-review/session-provider-input-peer-final.json`、`capacity-fixture-peer-final.json`，不称复核者另跑runtime。

最终完整验证证据位于39e候选的 `final-fixture-verification/`，未覆盖前一轮失败：

```text
node --max-old-space-size=8192 <typescript>/bin/tsc
  --project <final-fixture-verification>/strict/tsconfig.json --pretty false
actual exit 0; 107.313s; output.log 0 bytes

node <vitest>/vitest.mjs run --config <final-fixture-verification>/suite/vitest.config.mts --reporter=verbose
Test Files  51 passed (51)
Tests       556 passed (556)
Duration    82.79s
actual exit 0; wrapper elapsed 83.859s
```

完整102个TS入口及51文件范围未收窄，没有name filter、skip或产品超时放宽。suite原log SHA `581f611d319bdd020dc98b245ee0f49068429b3133867a26c402ec573bb9d06a`。`sealed-verification.json` 在结束后逐SHA核实51测试源、71构建声明输入、15候选产物全部未变；来源指纹仍 `a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64`。未重跑已通过的UI98项或无变更的构建。

## 同Run实际执行进度

46项输入pin在执行前全部匹配。首次受控执行已实际启动，证据 `D:/DecisionHub-v3-development/evidence/legacy-same-run/attempt-20261024T0350-root/`。pytest在配置LoggingPlugin时尝试打开默认日志目标，被attempt外写入守卫拒绝，实际exit1；业务测试、临时SQL和native子进程尚未到达。完整原stdout、result及Main读集前后SHA均保存。

独立D-only配置探针确认实际目标为Windows `\\.\nul`：默认配置pytest_exit3/body0；仅在临时run.py的pytest argv加一行 `--log-file <attempt>/pytest.log` 后，探针1passed/body1/denied[]。原isolation/plugin/native guard和产品代码未改，旧7文件/旧scope逐字备份；46项新pin匹配。证据 `evidence/same-run-logging-probe/logging-fix-receipt.json`，scope SHA `7fc6e5f114357fafdda5974979bec0b48c08fa1db3bb66b8da1d8b897d70fdd5`。另纠正README：这个单spec子进程实际为4GiB，不是完整51组的8GiB。

第二次受控执行证据 `D:/DecisionHub-v3-development/evidence/legacy-same-run/attempt-20261024T0357-root/`，pytest实际进入原业务、临时SQLite/PAPER夹具，完整测试仍失败：

```text
test_perpetual_current_account_consumers.py:51 -> normal_queue_lifecycle
test_perpetual_manual_execution.py:319 -> executor.process_due()
action_execution.py:969 -> perpetual_execution.execute_intent
MANUAL_EXECUTE_WALL elapsed_seconds=54.865720999994664 status=filled
assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
1 failed in 188.64s (0:03:08)
actual exit 1; denied=[]; native children=[]; research HTTP receipts=[]
```

该结果只说明本次带源码观测的受控执行超出原5秒门；尚不能区分临时观测开销与原writer性能，不先认定产品根因。不得改5秒、绕过原timed_execute、跳过资金链或把filled当作全链成功。尚未到研究POST/native/Graph/report保存与重开，不能宣称同Run闭环通过。下一步先以最小测量归因；若再执行，必须为明确诊断保留新的独立证据，不能盲重试。

实际Main读集有1774个唯一文件、323253141字节，451个repo模块来源均为Main；最大的一组是 `packages/kernel/market_component_versions/<dated>/source-binding.json`，每份约5.2–5.9MB。这是本次实际读集，不代表全部发生在54.87秒计时段，也不包含重复读取次数。临时Isolation每次Main open额外执行resolve/is_file/read_bytes/SHA；原writer自身又存在source guard capture/verify。两种开销必须分别测量，不能从唯一读集直接断言谁是根因。原log SHA `a74f671f3f75fe07c7f6528dbce3027dd995b4efbdf7cd7d2a0474953ffc5db7`，Main读集SHA `1bbf643123f45108ffdda3fe83596cf0cff2bde086e1ccbaf43854c52b73a172`。

独立D-only微测使用5.750016MB合成文件读16次，不导入业务或数据库：off25.883ms、原guard105.394ms、计时guard122.509ms；后一组最外audit累计88.637ms。它证明观测放大存在，不能把微测比例外推成54.87秒的责任分配，也不能减去估计值宣称5秒通过。原7文件装配与生产源未改，第三次sameRun未执行。证据 `evidence/same-run-overhead-probe/{analysis.md,receipt.json}`。

进一步只读区分：`_ReadSourceGuard`/`_ConsumerSourceGuard`有当前源码重复校验，但其遍历规则不直接读取上述market巨大JSON；另一条 `product_release_provenance._verify_archive_owners` 会遍历owner并读market source-binding。全process读集还不能把它们定位到计时窗口。旧EXECUTION_LOG曾记normal_queue_lifecycle 14.921s超5秒且有并行tsc干扰，但本轮未找到它的原始wall.log，不据此认定同源码根因。下一次诊断应在真实 `PerpetualManualPaperService.execute_intent` 入口/出口窗口内分别累计audit、source capture/matches、provenance与SQL耗时/调用次数，保持原5秒断言、业务clock、权限、守卫和原返回结果；先做D-only collector探针，再一次具名诊断执行。额外profiling本身也必须计入限制，不能伪装无观测实测。

## 进一步核对文档结论

只读清点发现旧报告路径已有 `confirmed_report_snapshot` 的PC读取/下载与两次当前许可检查，不需要另造导出体系。永续V5的saved GET仍要求full original和graph recipe匹配，但已有retained reader优先路径及source_bundle/frozen body；不能据此概括“升级必坏”。此前audit把frozen_reader源码检查直接归到V5 GET必经点，结论过宽，现更正。限定检索未找到把具体409请求、安装选择与错误栈绑定起来的完整失败录制，不以笼统409叙述授权重写。只读简报与source pin在 `temp/legacy-reuse/report-portability-next-step.md`。

原官方Host的下一接缝可复用现有ScenarioRuntime Web replay，而非启动正式run-product。Main原profile实际仍指向Main插件；D的部分启动器是旧基点，不能盲跑或称已加载39e。只读清点 `temp/legacy-reuse/original-host-next-step/recommendation.md` 已列具体入口/pin；尚未准备或执行新的Host装配。即使后续replay通过，也不覆盖live-root retained/admission分支。先处理同Run前置性能归因，再继续该接缝。

原UI、Main产品源码、8047/3097服务与正式数据库仍未覆盖。真实Host、真实Provider、实际PAPER及连续14天/四周指标继续待验收，目标保持ACTIVE。
