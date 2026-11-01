# 开发验证与正式发布分离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 日常 Python、前端和插件验证不再依赖完整发布存证，提供独立开发运行目录、快速预检和可重复的操作文档。

**Architecture:** 复用现有源码入口和成熟启动/停止机制，显式区分开发会话与正式发布。开发会话不签发或复用正式验证回执，不覆盖正式运行目录；正式发布保留完整来源认证，并在昂贵留存之前增加同等快速预检，留存后仍做最终权威检查。

**Tech Stack:** 现有 Python 3.12、PowerShell 7、Vite/pnpm、DSH；不引入新依赖。

**Spec:** 本会话中用户已确认的开发/发布分离建议；现有 `HANDOFF_CODEX.md` 与 `VERIFICATION.md` 的真实数据、审核、留存和报告要求继续有效。本轮用户要求“按你的说的处理下并写好文档”，授权该范围必要的源码、脚本、测试和文档修改，覆盖此前仅执行/记录的路径限制；不恢复旧全项目目标。

## Global Constraints

- 不运行完整 `build-product.ps1` 发布构建，不删除或修改历史归档、旧失败产物或正式数据库。
- 不提高 256 MiB 限额或通过跳过哈希、来源、审核与资格检查制造通过；先明确运输元数据与实际实现资源的预算契约。
- 保留正式启动默认行为、回执认证和 installed-release 指针规则；开发模式不能签发有效发布回执。
- 开发数据、DSH home、进程状态、日志和端口与正式环境隔离；本机 loopback；不打印凭据或私有 owner 入口 token。
- 当前 Main 含大量真实未提交/未跟踪代码，HEAD 和旧 D 工作区不是完整当前源码。沿用户指定工作区做窄修改，先保存目标文件前像；不新建遗漏当前源的 HEAD worktree，不 git commit/push。
- 仅修改下列任务涉及的文件；发现额外依赖先记录具体理由，禁止顺手重构业务和前端。
- 长日志落盘，只摘结果；测试临时目录使用新短路径，保留旧记录。

## Review Focus

- 开发路径、符号链接、目录穿越或继承环境导致正式库/指针被读写：启动前拒绝，测试覆盖。
- 开发参数误开启交易、学习或正式资格，或正式启动错误接受开发会话：测试两个方向的拒绝。
- 历史绑定文件被篡改、索引哈希不符、单文件/文件数预算超限：修复后仍明确失败；不只覆盖合法大历史。
- 早期预检失败仍触发留存/指针写入，或删除后期预检后遗漏构建自身新增归档：行为测试证明先检且保留最终检查。
- 前端开发代理的 Origin/CSRF 与 owner 会话不一致、停止命令误杀其他进程或复用旧 PID：实际启动验证和受控拒绝测试。

---

### Task 1: 修复不可变绑定清单的累计阻塞

**Files:** `packages/evals/assessment_basis.py`；必要的独立 helper；`tests/evals/test_assessment_basis.py` 或专用新测试。

**Interfaces:** 保持 `loaded_assessment_manifest_hash()` 的来源认证和确定性哈希契约；按有限、认证的资源表示解决历史运输元数据重复累计，具体设计先做独立审查。

- [x] 给出根因与预算语义，记录是否涉及 ABI/已有回执失效。
- [x] 先用缩小预算的真实临时归档复现失败；覆盖篡改和资源超限负例。
- [x] 最小实现后跑相关测试和 scoped 静态检查。
- [x] 单独执行真实仓库 manifest 预检，记录耗时和结果，不启动发布构建。

证据：`artifacts/db01/summary.json`。4 个关键原用例 + 13 个新用例 17 PASS；原 75 条断言 AST 未变，两个夹具仅补真实 gateway 字节。原全批因已证夹具缺失且重复重型扫描而取消，未宣称全批通过。scoped Ruff 4→4 / strict 6→6，新增 0；真实独立 CLI 59.424 秒通过，见 `artifacts/incremental-delivery-20261023/d28030518/`。

### Task 2: 将快速预检前移并提供独立命令

**Files:** `infra/dsh/build-product.ps1`；新增 `infra/dsh/assessment-manifest-preflight.py`；专用 `tests/dsh_native` 测试。

**Interfaces:** 小 JSON、严格退出码、明确 early/final scope、当前源码 SHA；不产生发布回执。

- [x] 为预检失败不产生昂贵副作用写行为测试并观察失败。
- [x] 复用现有检查逻辑，增加独立 CLI；将 early 检查置于留存/生成身份/安装指针之前。
- [x] 保留 SourceBefore 封闭后原有最终检查，验证两次调用语义。
- [x] 验证 PowerShell 7 要求和错误输出路径，不执行真实完整构建。

证据：`artifacts/incremental-delivery-20261023/mpf01/verification.json`，新 15 PASS / scoped Ruff、Pyright 0。旧相关回归 9 FAIL / 14 PASS / 3 SKIP 为原 V1 夹具与当前 V2 helper 失配，原断言未改，不能把此切片写成全套回归通过。

### Task 3: 独立开发运行入口

**Files:** 新增 `infra/dsh/run-dev.ps1`、`infra/dsh/stop-dev.ps1` 和必要的开发路径 helper；窄改 `infra/dsh/run-product.ps1` 复用其现有进程、profile、会话和健康检查；新增专用启动器测试。

**Interfaces:** 显式开发模式、限定开发根目录、独立数据/DSH home/运行目录/端口；Python 源码服务、Vite dev、按需插件局部构建；不依赖正式回执。

- [x] 先核对现有守卫/插件身份，独立审查复用方案。
- [x] 测试正式目录/链接/危险模式拒绝、默认正式行为不变、精确进程停止和开发入口不触发发布构建。
- [x] 实现开发入口及停止方式，保留真实数据与审核检查，开发会话明确无正式认证资格。
- [x] 实际启动独立开发环境，验证 API、DSH、Vite 和 owner/CSRF，记录版本/日志/耗时。
- [x] 使用现有真实 Graph→Commit→V5 reader 的隔离夹具验证保存/读取及 owner/改源拒绝；当前空研究设置、BTC/ETH 真实付费研究未执行，明确记录，不以夹具冒充。

证据：`artifacts/dev3-1028a/final-launcher-tenth.log` 74 PASS / 3 SKIP；实际 `d28030518/start04/ready.json`、`owner-vite-http.json`、`stop-isolation-proof.json`，7/7 online、权限正反例及安全停止通过，正式 6 文件 SHA 未变。停止命令 exit0 与控制进程被正常终止的 native -1 分别记录。最终自动初始化/新代码重启见 Task 6。

### Task 4: 文档与交接

**Files:** `docs/diagnosis-2026-10-23/DEVELOPMENT_WORKFLOW.md`；更新 `HANDOFF_CODEX.md` 的当前入口说明；更新 `docs/runbooks/local-development.md` 的当前入口链接；追加 `CODEX_LOG.md`。

- [x] 写清 Python、前端、插件和正式发布分别何时测试/重启/构建。
- [x] 提供已实测的 Windows pwsh 命令、独立目录、启动/停止/自测/预检、修改后的最短反馈流程。
- [x] 区分已通过、未执行、失败与正式发布资格；保留以前失败历史。
- [x] 非作者审查无剩余 Critical/Important；相关回归和新增静态检查通过，原有诊断/失败切片与 CRLF 检查结果单独保留，交付文档记录剩余限制。

### Task 5: 收口源码启动的旧 V4 发布服务耦合

**Files:** 新增 `packages/runtime_adapters/development_runtime.py`；窄改 `apps/hub_api/main.py`、`apps/hub_worker/main.py`；专用测试。launcher 的环境变量改动仍由 Task 3 负责。

**发现与裁决：** 实际静态调用链表明 API 无条件构造旧 V4 frozen publication 服务，该服务要求 normal registry；单清空发布回执会在 API 初始化时失败。现有 `research_publication_jobs48.py` 对非 V4 源不排旧 bundle；普通永续 V5/V6 已有自己的 `PerpetualSavedReportQuery`，强制 owner、原件、artifact、graph-final 与审核认证。因此 DEV 可以明确停用旧 V4 服务与相关 quality/recovery 路由，保留现有真实 V5/V6 读链，无需伪造 registry、发布回执或另造报告翻译层。正式分支与来源守卫不变。

- [x] DEV helper 在数据库创建/迁移前校验显式 mode、精确 root/data/home/checkpoint/文档/采集路径，以及 source 外的派生 publication 路径；拒绝继承正式私有配置。
- [x] API 在 DEV 不构造旧 V4 factory，关闭/路由按真实可用性分支；普通 confirmed reader 不得退回假确认。增加明确开发状态与 false 正式资格记录。
- [x] worker 在 DEV 不启动旧 V4 publication 队列；一般研究 lane 的真实用量采集与恢复不能因 publisher 关闭而消失。
- [x] 先 RED 后 GREEN，验证正式仍要求原 factory、坏 DEV 配置在数据库写入前失败、现有 V5/V6 读链保持；不改金融资格或审核逻辑。
- [x] 实际启动和 owner/CSRF 通过；保存/读取使用隔离原生 Graph/Commit/reader 夹具通过，未冒称 BTC/ETH 真实研究。

证据：`artifacts/dd01/summary.json`，52 PASS / 3 SKIP，原生 V5 夹具 1 PASS；scoped Ruff 19→19、strict 10→10，新增 0；旧断言 AST 未改。独立审查已核对应正式分支与原认证链。

### Task 6: 收口实际冷启动遗漏与重复遍历

**Files:** 新增 `infra/dsh/development-workspace.ps1`、`infra/dsh/development-settings-session.ps1`，窄改 DEV 调用/权限 helper；`packages/evals/assessment_basis.py` 的候选目录遍历；专用测试。

**依据：** 首次 DEV DSH 的真实阻塞为 `host_workspace_not_registered`；现有官方 `workspace/create` 认证 RPC 已实测成功，无需构造 registry 文件。另一次只读 metadata 对比证实 kernel 旧遍历 121162 项仅选 1393 个文件，遍历及逐项分类约 20 秒；提前排除本来不计入 manifest 的子树后集合与顺序相同。不能通过关闭来源认证、缓存旧 hash 或提高超时解决。

- [x] 把真实官方 workspace 认证/注册顺序收为 DEV 首次及重复启动的幂等步骤；失败保留错误码，正式行为不变。
- [x] 对相同路径集合/顺序/实际 SHA 与链接边界写等价测试，目录提前剪枝仍保留逐实际文件的全部认证/预算。
- [x] fresh 目录 ACL 实际失败后，在真实 Modify-only 小夹具复现并修复为 owner 先验的 DACL-only 设置；DEV 预护登录私有路径后仍使用原会话 factory，正式行为不变。
- [x] 相关测试、非作者审查及最终真实 manifest 预检通过。
- [x] 实际自动启动与正常停止/重启验证通过，记录最终实例入口和限制。

最终证据：`d28030518/start06/ready.json` 自动创建 true，观察验收 157.545 秒含 QA；stop exit0、无端口/PID残留、正式 6 SHA 未变。同实例 `start07/ready.json` 自动创建 false，观察验收 115.347 秒含 QA；插件构建日志/stamp 的 SHA 与 mtime 未变，7/7 online、owner/Vite/API/DSH 再通过、正式 6 SHA 再次未变。默认实例 `data/development/default` 保持运行；最终 source SHA 清单 `d28030518/final-source-sha256.json`。本轮未运行完整发布构建、未发起付费研究，不算全项目或金融资格交付。
