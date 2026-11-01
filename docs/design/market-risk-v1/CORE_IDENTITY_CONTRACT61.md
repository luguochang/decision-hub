# 研究结果的 ID 绑定与验收

60 版 BTC 真实终态暴露了请求 ID 的维度混用。原生结果本身已完成；review 消费者传入了业务请求 ID，导致 `dsh_terminal_native_metadata_mismatch`，最终未生成报告。

| 字段 | 来源与用途 |
|---|---|
| Run ID | 产品任务及所有父子回执、报告读取的主键 |
| 业务 request_id | 保存的 Research 请求；观察 context 用它绑定用户问题 |
| 原生 RPC request_id | 原 `DshSessionLinkService.deterministic_ids` 由 Run、原 request_hash、generation 派生；用于匹配原生 `user/message.source.rpcId` |
| DSH session_id | 同一派生函数的会话 ID；必须与已认证原件和边界一致 |
| generation | 原任务执行代次，不能用重试的新代次替换旧终态 |
| result_hash | 原 final_response 与 events_json 的完整字节指纹 |

## 最小修复

消费者从已认证边界的 Run、原 request_hash、generation 派生原生 RPC ID，先核对 session_id，再调用既有严格 native metadata validator。业务请求的 context 绑定继续核验。原 terminal reader、事件顺序、时间、哈希、权限、期限和发布 CAS 保持。

现场一次只读对照已证实：业务 ID 与原生 RPC ID 不等；派生 session 与原 Link 相等；派生 RPC 与认证终态相等；使用正确 RPC 的其余十项原生 metadata 谓词均通过。此对照没有执行 review、写库或重开失败任务。

## 完成条件

必要正负控制及独审通过后，正常发布修复。新真实研究须依次通过 review、commit、Saved、独立 ACK；同一 Run 的正文、下载、刷新及重启原字节一致才算通过。原 BTC60 失败记录保留，纯控制及“原生 completed”均不替代产品交付。

## 证据

原终态：`artifacts/core-goal-20261011/publication60_05.json`、`btc-progress60_04/`。候选与闭合字段对照由 DEV 的 `completed-consumer-live60_01/` 封存；当前尚未安装。
