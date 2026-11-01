# 研究运行可靠性整改方案

历史48/49可靠性提案（原文保留；现行状态见[当前任务](CURRENT_FOCUS.md)，未安装诊断的证据分级见[54技术增量](TECHNICAL_DELTA54.md)）。日期：2026-10-10。状态：**48正常启动已验收；新BTC准入失败未产生Run。49的R2诊断候选与有限控制已封存，尚待正常发布；R1/R3和真实研究终态仍未完成。**

范围仅限实际47自然研究 `run_85aa4a3278d94caba7e697bb8801eca4` 暴露的投影容量、Host 提交诊断和会话存在性判断问题。保留原失败记录，不增加预算、并发槽位或授权，不补写历史接受、取消、结果回执。

## 1. 已知事实与证据

| 证据 | 已确认内容 | 边界 |
| --- | --- | --- |
| 完整原生回读，10行 | Run 为自然 `scheduled_recheck`；UTC 02:27:45.125611 保存 `dsh_host_unavailable/http_500`，02:29:15.358921 失败为 `research_method_binding_failed/cancellation_projection_busy`，间隔90.23331秒 | 没有 `risk_research_requests` 行；不是一次手动风险研究 |
| 原 Task、Link | generation=1，共同截止02:57:22.756428；失败时尚余1687.397507秒；Link 的 accepted、last_seen、terminal、result 均空 | 空回执不能证明 Host 从未建立会话，也不能允许重新生产或伪清理 |
| 同 Run 工作者日志 | 清理使用正的默认0.25秒读取预算，约15毫秒直接返回 busy，证明此时两个真实 outstanding 槽均未释放；后续 transport 确认的 operation_id/latest_stage 为空 | 哪两个操作占槽、何时开始、最终完成或提交状态未知；没有 Host 内部异常堆栈 |

原件与诊断：

- [原生完整回读](E:/project/study/codex/dsh/decision-hub/artifacts/market-risk-implementation-20261007/B2/release48/research47-auto-native48_01.private.json)，SHA256 `25b20529178c8f51a4da26a8bd4b4e136263bb1b89eb1b3e5a843e61b80f7f04`。
- [有限诊断](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/cancel-projection-natural47-48/diagnosis48_01.json)，SHA256 `808c3224ab15cea92af1e9a994b199ed91bc913ea3b11b8341c9232bc91bd06f`。
- [诊断封存](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/cancel-projection-natural47-48/diagnosis-seal48_01.json)，SHA256 `8c3f7992371297ee0e89eb2f9fc1929390737b2939842042367c3a4001427054`；其中绑定匹配日志原件及源码 SHA。

诊断中的四个 runtime 方法已按完整 AST 源码段与47备份核对，48未改变其语义。本次只确认“先有 Host 500，后有槽位忙”，**没有确认500的内部首因，也没有确认槽位长期占用的具体原生操作**。terminal 的 `provider_attempts=[]` 不能否认已保存的 Host HTTP 调用。

## 2. R1：区分容量与预算，并定位占槽操作

**代码位置**：[cancellation_projection.py:107](E:/project/study/codex/dsh/decision-hub/packages/runtime_adapters/dsh_runtime/cancellation_projection.py:107) `_call`、`:174` `read`、`:186` `write`；[web_runtime.py:410](E:/project/study/codex/dsh/decision-hub/packages/runtime_adapters/dsh_runtime/web_runtime.py:410) `_assert_task_binding`、`:808` `_record_transport`、`:1812` `_cancel_active_owner_best_effort`。

**现状**：`timeout_seconds <= 0` 与两槽占满共用 `cancellation_projection_busy`。write 有有限 stage，read 缺少操作阶段；等待超时后原线程仍运行并占槽，这是正确的安全边界。当前证据只能定位清理时容量满，不能量化两个持槽操作。

**实现边界**：

- 增加封闭元数据：operation ID、读/写种类、同 Run/session/generation 绑定、单调时钟起止、输入等待预算、queued/start/native-read-or-BEGIN/operation-completed/commit/late-finish、实际 inflight 数量。日志不得含正文、原生 payload 或凭据。
- 在诊断中分开 budget elapsed 与 capacity full；若要变更公开错误协议，另行版本兼容审核，不能修改已保存原件来适配新分类。
- 保持两槽和真实完成后释放；取消等待不能提前释放槽、关闭仍在运行的原生资源或扩大并发。pending write 不重投，不根据晚到线程异常推定回滚。
- 获得真实慢阶段后才优化完整校验；每次调用仍核同物理快照、完整原件身份及出口 CAS。48 provider 输入 byte lease 属于另一个入口，不能用其私测替代这里的性能证明。

**单次验收**：一组有限控制同时覆盖“正预算且两槽满”“预算已尽”“超时后晚到读/写”。要求分类准确、第三个操作不执行、每个槽仅在自己的线程完成后释放一次、未知 COMMIT 不获重试或取消许可。真实48只观察一次新 Run 的上述阶段；完整验证仍须在原预算内完成。若失败，保存所有占槽阶段，保持 failed/pending，不伪造清理成功。

## 3. R2：保留 Host 500 的安全阶段与首个错误

**代码位置**：[bridge.ts:856](E:/project/study/codex/dsh/decision-hub/extensions/dsh/decision-hub/src/host/bridge.ts:856) `submit`、`:1607` `normalizeError`；[web_host_client.py:236](E:/project/study/codex/dsh/decision-hub/packages/runtime_adapters/dsh_runtime/web_host_client.py:236) `_request`、`:287` `_http_error`；[web_runtime.py:1732](E:/project/study/codex/dsh/decision-hub/packages/runtime_adapters/dsh_runtime/web_runtime.py:1732) `_retry_host_call`。

**现状**：Host 将未知异常映射为 `host_internal_error/500`；客户端只按状态映射 `dsh_host_unavailable/http_500`，没有保存安全 Host 阶段。500已落库；随后绑定失败成为 terminal，不能因此覆盖或忽略较早失败。本次 Host stderr为空、stdout仅82字节且无匹配身份，内部阶段未知。

**实现边界**：

- Host 提交阶段记录 inspect、workspace、create、accepted-callback、prompt；绑定完整 Run/session/generation/request 身份及服务器侧关联 ID，使用封闭错误码，不写异常正文或输入文本。
- 客户端仅接受有长度限制、结构和封闭码白名单验证的 Host 错误元数据；保留原 HTTP 状态，未知、错身份或畸形内容保持通用500分类。关联 ID 仅用于诊断，不授予执行权限。
- 保留“最早已保存 Host 错误”和“最终阻塞原因”两项。继续使用原 transport append-only 确认；无 operation_id/ACK 不算保存成功，不能为了补诊断重放 Host RPC、写入或模型执行。

**单次验收**：一组有限 Host/client 控制验证受控内部失败可定位阶段、任意错误正文不泄漏/不获信任、后续绑定失败不覆盖首个原件。正式48一次真实 Run 按同身份串起提交与回执；若无 ACK 或出现500，只给失败阶段/未知说明，不能以“进程在线”判通过。

## 4. R3：inspect 失败不能一律当作不存在

**代码位置**：[bridge.ts:880](E:/project/study/codex/dsh/decision-hub/extensions/dsh/decision-hub/src/host/bridge.ts:880) `submit` 中 `sessionController.inspect` 的 catch 与后续 create；`:927` `resolveWorkspace`。

**现状**：当前 `catch { /* absent is create */ }` 把超时、存储/内部错误与明确不存在混为 inspection=null，可能进入 create。该分支是已确认的静态缺口；**本次没有证据证明它实际触发了500**。

**实现边界**：

- 先核上游当前正式 sessionController 的“不存在”原生合同及类型；只有其明确、可验证的 not-found 可进入创建，不能匹配任意异常消息或发明码值。
- inspect 超时、权限、存储故障、身份不一致及未知异常失败关闭；保留原 deadline、workspace 和 session 身份检查。存在会话与 workspace 列表冲突需明确处理，不能把冲突推定为可重建。
- 不清除 correlation、Link 或 Host 未知活动来强制成功；不改历史 failed Run。创建/接受是否已完成不明时，不重放整条提交，不伪造取消或 accepted。

**单次验收**：一组有限控制覆盖明确不存在、已有同身份会话、inspect 超时/内部失败、workspace 身份冲突。仅明确不存在创建一次；其它异常不 create、不 accepted、不 prompt；已有合法会话不重复创建。失败保留原异常类别和当前未知状态。

## 5. 执行与放行顺序

1. **48迁移、标准启动已通过，新BTC未进入Run；当前按[本次核心修复计划](PC_ACCEPTANCE48_AND_FIX49.md)完成一次49组合发布**。先消除准入和报告阻塞，再保存一个新的合法意图并验真实Run；原失败请求不自动第四次，也不回填旧47结果。
2. 实际日志不足时先做 R1/R2 最小诊断候选并封存。R3可同一候选处理，但必须先确认上游 not-found 合同；性能改动仅针对已证明的慢阶段。
3. 各项有限控制各执行一组，记录源码/证据 SHA、耗时和实际 P/F；不重跑无关矩阵，不把候选控制算成生产修复。正式推广与标准构建由Root一次执行。
4. **一次正式真实 Run 的通过门**：同身份提交 ACK；完整原件验证在原预算内；transport 有独立提交确认；真实终态和报告可回读；实际读/写槽正常释放。若该 Run 未达到终态，标为未完成；未知提交、取消或结果继续保持未知，绝不替换成成功。

本文最初为诊断文档。当前49的R2 Host/client诊断已封存：末版lineage fence的Host2项、client2项控制，以及新增4种错误码的client4项控制通过；原失败回执保留。R1占槽操作诊断和R3真实not-found集成未完成，不以R2控制代替。正式Run、报告与监测共存仍待49验收。
