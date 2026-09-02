# DSH-NATIVE-CORE Replay 与恢复验收记录

日期：2026-08-31
阶段：`DSH-NATIVE-CORE`
范围：`NC-05` 三场景 replay、`NC-06` Host/Hub 恢复与版本 fail-closed
状态：`verified / official Web matrix and recovery complete`

## 1. 验收边界

本记录验证固定的官方 DSH Host bridge 契约、Decision Hub durable Run、LangGraph
证据轮、PIT/Sufficiency/Gate、ResearchResult/Artifact/Ledger 和 query view 的一致性。
测试使用受控 Host seam，不需要外部模型密钥；因此结果是可重复的离线 contract-level
证据，不是实时网络事实、预测准确率、盈利能力或产品 ready 证明。

官方 DSH Web 的固定上游和 authenticated boot 由
`infra/dsh/acceptance.sh` 单独验收；本矩阵已在隔离 DSH Web 中保存三场景的
Session/Trajectory/JSONL、浏览器桌面/移动截图、
console 结果和对应 Hub business-status 查询；这些证据仍只证明工程闭环，不证明实时
网络事实、预测准确率或盈利。

## 2. 三场景矩阵

| 场景 | 输入/执行 | 预期结果 | 已验证事实 |
|---|---|---|---|
| `success` | 全部 hard requirement 有 fresh、满足 `authority_floor` 的 replay Evidence；`macro_transmission` 有两个独立来源 | `completed`、Gate `publish`、30m/24h/72h 保留不同 horizon | 官方 Web Run `run_5ca2d2d7add544828708885b93e49259`，Session `dsh_07fd33632a1ce72014f7f2ad72af87374cbd29f5a94412fe3c5b83392e530119`；8 次工具调用，hard coverage 100% |
| `partial_failure` | 一条 replay Evidence 成功，另一 capability 返回 `research_replay_fixture_missing` | 成功证据保留，失败项带 `origin=gateway`、`capability_id=replay.research`、`retryable=false`；Gate `reject`，不发布方向 | 官方 Web Run `run_696ac0dfd4bd425d92ad4955066cd34e`，Session `dsh_250e43f0a71e6dd8a668bf93642ae45e675ff221174531c250eafc20dcbd8df7`；hard coverage 16.7%，页面显示完整 provenance |
| `insufficient_or_stale` | 只有超过 freshness window 的官方事件资料 | Coverage `insufficient`，停止原因 `critical_data_unavailable`；horizon fail-closed 为空，Gate `reject` | 官方 Web Run `run_d1090cf528fa4a71b62191c9daeabe7c`，Session `dsh_92e50e3934127de61b5de3dd3c3ec2814f8456bf710bc127d713f651ade8840e`；hard coverage 0% |

每个场景都断言：

- `run_id <-> dsh_session_id` 是确定性且唯一的；
- accepted/status/result/terminal 走 canonical contract；
- DSH 结果只接受可信 MCP capability 投影的 Evidence；
- Hub 保存 Evidence、Coverage、Gate、Artifact、Trace 和 query view，不复制 DSH raw JSON；
- 重复 tick/submit 不重复 Artifact、Evidence、child Run 或 terminal commit。

## 3. 恢复与升级矩阵

| 场景 | 预期结果 | 已验证方式 |
|---|---|---|
| Host 在 accepted 后退出 | replacement runtime 复用已持久化 link，继续同一 deterministic Session | `test_web_runtime_recovers_when_host_exits_after_acceptance` |
| terminal callback 丢失 | 后续 status/result reconciliation 补回 terminal，结果 hash 保持一致 | `test_web_runtime_reconciles_a_lost_terminal_callback` |
| DSH upstream/plugin 版本错配 | readiness `version_compatible=false`，runtime fail-closed，不创建 Session/link | `test_web_runtime_fails_closed_on_upstream_version_mismatch` |
| 外层多轮补证 | 动态 evidence/round 字段不改变 Run 级 request identity，继续同一 Session | `test_web_runtime_keeps_session_identity_across_evidence_round_continuation` |
| Hub worker checkpoint 重启 | expired lease + checkpoint 恢复，commit 幂等且第二次 tick 空闲 | `tools/research_recovery_smoke.py` 与 `tests/evolution/test_research_worker.py` |

三场景官方 Web 页面均无 console error/warning，桌面 1280x720 与移动 375x812
均无横向溢出；截图和 hash 见各场景 output 目录。移动窄屏存在上游 DSH 自身的长文本
截断，但未发现重叠或布局溢出，不修改固定上游。

## 4. 实际命令与结果

以下结果以最后一次实际执行为准；后续代码修改后必须重新运行，不能沿用旧结果：

```text
./.venv/bin/pytest tests/dsh_native -q
./.venv/bin/python tools/research_recovery_smoke.py
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/python tools/docs/check_module_docs.py
```

`tests/dsh_native` 必须同时包含三场景参数化测试和恢复/版本测试。任何只通过
`pytest` 的 contract-level 结果都不能替代官方 Web 证据。

本轮实际结果（2026-08-31）：官方三场景 acceptance、recovery/rollback、`tests/dsh_native`、
recovery smoke、contract check 和 module docs 均通过；全量离线 `pytest -m "not live" -q`
为 `327 passed`，Ruff/Pyright、两个前端 test/build、Compose 和官方
`infra/dsh/acceptance.sh` 均通过。浏览器资产如下：

```text
partial_failure desktop sha256 991491a4c77c7c534f583ecdee6b8699d1faf6450a7b992d447fa3c118342c26
partial_failure mobile  sha256 7ffa8288af3016260a8239b13a714f97527fd11cec38b09ab3e34fdb5bed3666
success desktop         sha256 67677ef47a21e3349b89ec23b651ad4e8e56c56df2a9b9708f720928ead3d093
success mobile          sha256 a8d19a35d6874da914b14b0a3ad5d946b64919d18ce805760794d7d6da89eefb
insufficient desktop    sha256 2a12433443f9d11dfefda705d6ebc0b6de5fd0023dcf4d6ebfd6cbca7381612e
insufficient mobile     sha256 291a03d6d529c17191c122ebb81a02c77bae960071e7abb9b5cd6ac921d1049e
```

## 5. 退出判断

`NC-05`：official Web 三场景矩阵、业务状态投影、截图/console 已通过。
`NC-06`：callback recovery、官方 Web restart、旧 lock rollback 和版本 fail-closed 已通过。
`DSH-NATIVE-CORE`：工程验收完成；Fixed 仍 active，DSH 仍 candidate/shadow，不切换 active pointer。
