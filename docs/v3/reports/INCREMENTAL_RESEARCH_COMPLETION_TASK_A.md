# SDD ledger — plan: docs/v3/reports/INCREMENTAL_RESEARCH_COMPLETION_PLAN.md

Task A：显式独立 child 内容检查。当前为 D 候选，作者校验与非作者独立复核已完成；不表示 Main 已采用、完整研究合格、金融资格或实际执行。

## 物理来源与范围

- Main `E:/project/study/codex/dsh/decision-hub` 全程只读；D `repair/incremental-delivery` 为既有隔离修复区。
- 必要基线提交 `7195747`：当前物理 Main schema、manifest、Python generated、attestation、两份原 generator。补充基线 `84c2463` 保留原 Python canonical reader，供新合同在真实 D 路径读取 D schema；未伪造 `__file__`。
- TS 已有目标逐 SHA 与 Main 相同后才修改。其他 agent 的 commit/report/reader 与原有未跟踪文件未 stage。
- canonical schema 新增 findings.v2 与四项 closed assessment；v1 定义、Python class、TS schema、native Tool schema closure 均逐字保持。原 request.v2/v3、authorization.v2/v3、frozen-review-result.v4、raw 两对象数组不换版。
- 原 generator 的选定 Python 入口和原 TS CLI 仅生成必要两份镜像；TS CLI 在 D 临时输入目录使用 61 份逐字 Main schema 加唯一候选 schema。未重新生成无关 Python 合同。再次生成与候选镜像逐字相同。

## 接口及判定边界

`report_assessment` 必须有 `frozen_information`、`probability_completeness`、`evidence_balance`、`cost_protection_and_conditions`。每项只有 `status`（passed/incomplete/failed）、非空 reason、原 evidence_refs。passed 必须引用非空、实际选定 catalog 中的原件；不认证模型理解或金融真值。

新 producer 明确要求 findings.v2；已鉴真的 actual_prompt 中 FINDINGS_FIXED_FIELDS_JSON 决定预期版本。旧 v1 prompt 沿原 parser，旧无 findings marker 的原路径保留；不从 raw 第二对象自选版本。独立 child 的完整 input/output/events、hash、父子身份和 previous negative review 链保留。

approved + incomplete 合法，不额外拒绝动作、不强迫开仓。内容检查不默认 passed，不把结构数据藏进 summary，不接受额外 PC/restart 检查字段；PC/持久化/重启实际证明仍属 Task C。原权限、费用假设、预算、时钟、工具与 child 限制不变。

## TDD 与校验记录

- 首次 TS RED：15 项中 4 FAIL/11 PASS；实际暴露新 v2 缺失、approved/negative 内容无法保留，以及新 prompt 可被 v1 降级。
- Python 首次导入错误来自 D pytest path 抢先；修正真实 Main 父包优先后，10 项实际 AssertionError RED。该导入失败不算产品 RED。
- 最小 GREEN：TS 46 PASS；Python 13 PASS，含完整 synthetic child proof 的 v2 approved+incomplete、matching transcript 的降级拒绝、wrapper-only 内容篡改拒绝。
- 追加完整 proof 的基线复验：逐字 Main 旧 attestation 在相同夹具下 2 FAIL/1 PASS，新正例缺失与降级绕过确实可复现；保留旧 transcript 篡改检查。
- 原 15 文件相关 TS 首次 95 PASS/1 FAIL：event-native 旧测试调用未传 actual_prompt。只补新参数后事件分支与内容检查 19 PASS；既有断言未放宽。
- 最终相关 TS 15 文件 97 PASS / 84.48s / exit 0；focused 原 strict/noEmit 最终复验 exit 0。Ruff owned Python 三文件零错误。
- Python 新测试与 support 在原 strict flags 下零诊断。legacy attestation 整模块不能声称 strict 零：使用明确物理 Main 来源副本及真实 Main 依赖接缝，原 strict/Python 3.12 对比 baseline 76 → candidate 70，新增诊断 0；不加 ignore、不放宽配置。
- Python 扩大原回归为 38 PASS/1 既有 FAIL。`test_v2_prompt_requires_server_materials_and_never_requests_legacy_horizons` 的旧文本断言，在纯物理 Main、无 D overlay 时独立同样 FAIL；未改该断言凑绿。
- 最终 Python 相关回归 38 PASS / 1 deselected / 5.81s / exit 0；该唯一 deselected 就是上项已在纯 Main 证实的既有 FAIL，原失败日志保留。最终只显式加载现成 pytest_asyncio 插件以识别原 asyncio_mode 配置，未屏蔽测试或警告；新测试/support strict 2 files / 0 errors。
- 原 pytest 配置在禁自动插件时有已记录的 asyncio_mode 警告。没有 HTTP、模型、业务 Run、DB 或服务执行；全部 fixture 仅协议测试。
- 原 Windows 源 CRLF 保留。默认 git diff --check 将新增 CRLF 的 CR 报为 trailing whitespace，未为消除该提示重写全部旧行；新增内容另核无空格/tab尾随。

日志、来源和逐字比较：`D:/DecisionHub-legacy-incremental/temp/research-completion-task-a/`。最终文件 hash/校验结果见该目录的 scope-prepared.json（冻结后生成）。

## 待交接

作者校验完成，18 文件冻结交非作者 peer。`/root/master_status_audit/content_review` 独立核全部 18 pins/13 Main 基线、旧 canonical defs、1347 个旧 TS exports、V1 class/Issue、actual_prompt 版本与完整 child proof 接缝，终评 C0/I0/M0；未重跑大套或执行业务。本文仅补记该结果，17 个代码/测试文件保持冻结 SHA。

Task A：complete（仅本地候选协议切片）。Task B 的 V6 writer/Run 状态由其他执行者负责；本切片不实现、不宣称完整报告提交或真实六条件合格。Main 的新 normal 仅认证此前 content/pooling 来源，未认证或采用本 V2 候选。
