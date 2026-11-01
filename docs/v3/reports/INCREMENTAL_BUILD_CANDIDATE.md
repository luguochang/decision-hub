# 原plugin构建候选计划与验收记录

状态：本地构建候选已提交，来源/strict/冷加载验证通过，完整功能回归未通过，未发布。依据Q-027继续原系统渐进交付。源码/产物提交 `fd1eef5888e8b6770b1e0e09852335eb287d6894`；配置原件另提交 `c4d09ae`。不得据此把整项目或功能验收标为完成。

目标：生成与当前D修复源、Main配置及当前contracts相符的本地plugin候选，补足已经证明的构建来源缺口。

架构：保留原generator → Vite host build → client wrapper三阶段；局部修复输入清单和生产contracts alias。PLUGIN_BUILD_HASH只表达声明构建输入的指纹，实际产物完整性由独立逐文件SHA及sourcemap/module来源证明。

技术：现有Node、Vite、Vitest、TypeScript；不安装依赖。实施者A改四个构建/测试配置文件，根负责配置基线、生成文件、候选产物与收据，C独立复核A的改动。

## 精确范围

- `extensions/dsh/decision-hub/scripts/generate-build-identity.mjs`：覆盖全部本plugin src（排除自身输出）、contracts_ts/src与实际构建控制输入，保留原path/NUL/length/rawbytes算法。
- `extensions/dsh/decision-hub/vite.host.config.ts`：用现有workspace相对路径显式绑定contracts，保留原4个entry、SDK/Node external、ES2022、sourcemap和lib输出。
- `extensions/dsh/decision-hub/tests/build-identity.spec.ts`：真实CLI与实际resolver的变更/稳定性回归，不复制生产hash算法作为断言。
- `extensions/dsh/decision-hub/vitest.config.ts`：测试与生产采用同workspace contracts，SDK运行值通过现有公开入口和明确sourceRoot环境装配，保留原测试范围和断言。
- `extensions/dsh/decision-hub/tests/task-output-budget-sdk.spec.ts`：仅支持已有SDK sourceRoot环境，默认仍为原repo cache；不改输出预算、真实SDK步骤或任何断言。此单路径由根/A复核，作者C不自审。
- `infra/dsh/presets/decision-research/agent.cordis.yml`：只复制Main当前7320字节原件，SHA `f6de0c3d64b75517aff8beaffb8496ed0a48ef10c5b0498a60369cd602395da8`，不启动该配置。
- `extensions/dsh/decision-hub/src/generated-build-identity.ts`：由真实generator生成；lib由原构建阶段生成。已有D输出先完整备份。

## 步骤与验收

- [x] 只读复现来源缺口：原43输入遗漏9个实际依赖源；D默认contracts链接指向内容不同的ISO生成合同。
- [x] 验证D输出真实绝对路径，无junction；备份旧9个lib文件、旧generated和旧agent，保留原件；同步Main当前agent原字节。
- [x] 新spec真实RED，随后最小修复并GREEN：源码/contracts/构建控制变化改hash，未改重跑及输出自身不改hash；实际resolver返回D合同源。
- [x] 冻结修改，按原三阶段构建；每步保存真正进程exit、stdout、输入来源。只增加观察插件，验证实际module和sourcemap源为D，无ISO合同混入。
- [x] 精确补齐完整native suite缺少的三份封闭原fixture，逐SHA留据。原断言未变；此后发现过期fixture/断言的修订另立范围、保留本轮失败。
- [x] 原完整strict覆盖新增测试后的全部入口，零诊断；完整native范围实际运行，但worker OOM提前终止且有50项断言失败，不能称全套通过。
- [x] 根核产物SHA、四个host入口和client wrapper；在禁止网络的子进程中做冷加载检查，不初始化真实Host/Provider。
- [x] 非作者复核、26个具体文件本地提交、报告实际输出与剩余交付，保留项目ACTIVE。

## 重点复核

不能把index.ts相同当作整个contracts相同；不能让生成文件自引用导致每次hash变化；不能把hash指纹当作产物SHA；不能漏掉client源或本次protocol/helper；不能以本地构建证明实际研究或长期指标。

原输出备份与本轮收据目录：`D:/DecisionHub-v3-development/evidence/legacy-plugin-candidate/53d0c4a6-def3-42c9-b710-b0f72cb8161c/`。

Main原源码、服务、数据库保持不变。后端研究→报告闭包只读审计另行并行，不并入本构建切片。

## 实际命令与结果

构建工作目录均为 `D:/DecisionHub-legacy-incremental/extensions/dsh/decision-hub`，由 `D:/DecisionHub-v3-development/temp/legacy-reuse/build_plugin_candidate.py` 在无密钥、禁止TCP/HTTP/fetch/listen的环境执行；无网络拦截记录。只增只读观察plugin，生产build参数不改。

```text
node scripts/generate-build-identity.mjs                         exit 0  0.109s
node --max-old-space-size=8192 node_modules/vite/bin/vite.js build
  --config D:/DecisionHub-v3-development/temp/legacy-reuse/plugin-candidate-observer.mts
                                                               exit 0  7.344s
node scripts/build-client.mjs                                   exit 0  0.062s
vite v6.4.3 building for production...
✓ 64 modules transformed.
✓ built in 6.27s
```

新fingerprint为 `e96e30bdfb8901d73bba86f55fd4dea6c73aff2c725e4a0d925ff339478fe392`，71份声明输入均按原framing独立重算一致。该值是来源指纹，非产物SHA或真实运行认证。当前Node v24.18.0、Vite 6.4.3、Rollup 4.62.5、TS 5.9.3、Vitest 2.1.9及其内部Vite 5.4.21，已保存真实解析路径/版本/SHA；无安装或依赖链接变更。

15个输出共27,890,568字节，完整文件集、字节数、SHA见 `output-inventory.json`；旧9文件先完整备份在 `before/lib`，新15文件封存在 `after/lib`。独立复核54个实际解析模块、51份第一方map sourcesContent均匹配当前D源码；7份Zod第三方map内容单独分类。6个必须源（bridge、protocol、model-view、generated identity、contracts index/r2）同时出现在实际module和map。3个被解析但未发射的合同semantics模块未假称均在map。

Client wrapper按原build-client语义逐字重建相等；VM仅捕获一次 `@decision-hub/dsh-plugin` registration，不调用factory。另一个禁网冷加载进程实际import四个编译后公开host入口，核name/inject/apply/各factory/bridge公开形状，Node exit0，1.343s，无被拦截网络尝试。没有调用apply、建立真实Host或证明完整SDK运行ABI。

```text
node --max-old-space-size=8192 node_modules/typescript/bin/tsc
  --project <candidate>/strict/tsconfig.json --pretty false
exit 0; 106.469s; diagnostic log 0 bytes
```

此临时config只重定位既有SDK declarations和D合同，继承原strict、noEmit、skipLibCheck和整个include。`--listFilesOnly`核所有102个当前TS入口确在编译列表，无收窄（不把client JS或合同依赖计作TS入口）。A移交中“103入口”的预估未采用；实际范围以 `strict/scope.json` 和完整编译文件清单为准。

新构建测试强RED为13 failed/6 passed，首次GREEN19 passed；再扩Vitest实际resolver产生1 failed/19 passed真实RED，修复后20 passed、3.95s。来源证据在 `temp/legacy-reuse/plugin-build-identity-repair/`，包含真实CLI执行、非mock resolver及前后SHA。非作者源码复核C0/I0，产物复核C0/I0；SDK单test入口由另一作者独立复核，预算/20秒时限/断言均未改。

## 完整回归失败，不能发布

`run_candidate_checks.py suite` 在plugin目录，以生产Vitest配置加单fork/cache覆盖、原 `tests/*.spec.ts`、51个文件、无name filter执行；环境只给已核SDK路径，清除可选重写fixture变量。仅允许测试自建port0 loopback，明确阻断正式服务端口和外网。

```text
node node_modules/vitest/vitest.mjs run --config <candidate>/suite/vitest.config.mts --reporter=verbose
FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory
Test Files  10 failed | 33 passed (44)
Tests       50 failed | 476 passed (528)
Errors      1 error  (Worker exited unexpectedly)
Duration    98.83s
actual Node exit 1; wrapper elapsed 101.046s
```

原始日志SHA `7a0cb7c75f8f50e12a6b2894a42b1b3fcd28fbdb0002a90f6b013e009411614b`。完整范围被请求执行，但只完成44/51个文件，因此不是“已完整执行完并仅50失败”。预算/超时/原断言在这次运行中均未改。已有UI98项和前一局部native42项通过仍各自成立，不抵消此结果。

后续修复须区分既存失败和新增回归，按原Main生产代码、跨语言合同及真实失败栈判断。已定位的旧断言包括：unavailable/null事件结果现已只派生自身事件identity，Main Python对应测试明确要求成功并保留原稿；Provider所有legacy phase当前wire cap已经是60秒，旧测试仍要求input/emission 30001拒绝。不得为迁就旧断言改回生产逻辑，亦不得将本轮测试修订说成新增长超时。Host mock遗漏fresh Link/终结authority、reviewer缺协议ref/新findings和schema投影布局仍待逐项复现及局部处理；不预判全为测试问题。

本切片没有改Python源，所以未冒报新的Python ruff/pyright结果。原PC入口与sameRun持久化、真实研究、PAPER及真实时间指标仍是交付剩余项。机器UTC仍为Leap3/Local CMOS，未重试提权校时；离线工作继续。
