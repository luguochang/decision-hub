# ETH08 当前原系统真实运行：业务未通过

2026-10-26 北京时间。当前 Main 采用后的真实新 ETH/4h 验证，沿原系统和原身份执行；不是 v3 替代前端。本文记录已经观察到的业务终态和一次实际报告读取失败，不把 Worker 返回 0 当作研究成功。outer 控制进程与清理结果另待同一 handle 实测。

## 实际身份与版本

- 私有实例：`D:/DH-eth4h-live08`；Root 唯一 outer session `93659`，父 PowerShell PID18540 / creation FILETIME134374422604422933。
- 原请求：`cda7a166-ea92-464b-9a7f-9ec73e1787f4`；原 command608B，SHA `01aba163e8fb10558446ec2c3314e6239b3850c583860a9f5161c334e963d202`。
- Run：`run_7093f1de874e4b409d2d16eab7d36b6b`；DSH session `dsh_b5dad3bf6f4da7eeb1b3268ed0131a4ad5b88878e2ec8ddd125d16d1d53ec419`，generation1。
- receipt1453B，SHA `28dbb87c148027c91b725286d40993b20827c4066b8faedf252c2b20fc961b63`；真实受理 UTC22:52:02.777667。
- Main normal SHA `4511e6c6c71e639a379f5562dcf6c52665ab4b957bae3ebc568c2a3510b4de4f`；当前实现 `51718c5030e7dbda210b84f75cae6c3401c7b362a524a7a4244da8d8e7de5ae0`；实际 native `d4b1c07c74af4f4d442240800f5c42752138c8da6af529cca80dadffe66de699`。
- 固定 Task/runtime deadline UTC23:52:02.777667；原观察冻结文件与原件没有刷新。首个同一逻辑 POST 已在22:17:50发生，不能把恢复受理时间改作该用户操作的新延迟起点。

## 真实结果

原 Worker preparation begin/complete/commit 已完成，最后 elapsed10.016秒。原 native 实际开展 Provider/工具/子会话活动，不能将这些活动或过程返回 200 当作已完成审核。

Root 在同一私有数据库 `mode=ro`、`query_only`、显式读事务核到：

| 对象 | 实际观察 |
| --- | --- |
| Run | `degraded`，artifact=`art_c096622b2225468fbf9eb6fdacf2f32f`，error_code=null，finished_at UTC23:09:28.462212，lease清空 |
| DSH link | `failed`，error_code=`research_provider_native_context_invalid` |
| publication source | 实际保存的是 `research-publication-source.v3`，不是本轮希望得到的 V5/V6 永续报告 |
| 独审行 | `research-review.automated-check.v1`，bounded，说明没有发布方向性因果主张；不是独立模型审核批准 |
| Worker | 进程返回0，日志业务状态 `research_only`；不等于 Run completed |

元数据原件 `D:/DH-eth4h-live08/artifacts/report-diagnosis01/metadata-readonly.json`，SHA `dcd90a3c41186851bdd530877e756449476d82f1b9a5c2e62199ad1fd0656666`。另一只读事务保存111份同Run角色原行（包含原 payload_json 字符串），`role-outputs.original.json`，12,753,528B，SHA `11fb66bcbb61cb3dcd2cf20c1a56099fc59b179dffd731c84b33e34ec26eb69b`。两次读事务 total_changes 均0；不把这项局部读证据扩大为全部源文件不变或完整数据库回放。

## 实际报告 GET：失败且不重试

Root 按原 owner 配对、同原command/receipt做恰好一次报告 GET。UTC23:10:17.354318→23:10:17.433479，HTTP409，正文为 `perpetual_report_original_unavailable`（50B，SHA `dd5d852d45471bc5ca7960dae7ab9e6bad0be968c2119edbc50519244e04a431`）。前后151个 frozen input pins 全部相同。

原件目录 `D:/DH-eth4h-live08/artifacts/report-readback/terminal-r01`，保存实际请求身份、HTTP元数据、原响应和失败记录。原 query 在 applicable 后鉴权，随后拒绝不属于 V5/V6 的 publication source；当前 v3 原件不满足永续报告读取条件，不能改 schema 或 fallback 伪造通过。没有得到 report.raw.json，没有执行组件 render、实际浏览器下载或重启报告读验。

## 首错定位边界

实际 authenticated terminal 记录：completion.completed_at UTC23:05:43.413，terminal_status=failed，error=`research_provider_native_context_invalid`；该终态原件在23:07:03.188662保存。真实 SDK root 首个失败位于 step5：seq348为正式 v6 protected message，349开始，351为请求header，354以“Model submission blocked”结束。

SDK压缩原件 SHA `de1d826950df6fcc44e835b0afb047bea56c09d687ac9335dd468f184eb3a3cc`。已用项目已装 zstandard 的 `stream_reader(read_across_frames=True)` 解码全部物理帧，保存 `root-session.all-frames.original.jsonl`（1,679,795B/SHA `f9c93191d35a11ce83e362d03ea50f5c60e679f083a532ccdc146bec75b58db7`）。共有144个物理记录，含 compaction/prune，不能冒称恢复了0—369全部历史事件。最初 Node zlib 单帧结果只含218B header，已保留并在 `root-session-decoding.json` 明确标记不完整，禁止据此回放。

API另有23:06:12结束的慢 Provider-input PUT409，持续51.223秒；不能把后发生的 HTTP 错误当作 root 首错。已持久化的11份 Provider input，其 native run/generation/root session/first linked request hash/prompt hash 五项均匹配；这只排除这些已保存行的该五项不匹配，不证明被回滚的请求无错。后续须以原 dispatch/会话/前缀和具体失败谓词串联，不依据相同错误码猜因果。

原 Worker 还有一条 dsh_session_links readset conflict，以及三条 heartbeat storage busy 观察；均保留。单条读集警告对应原有最多一次鉴真重读，不直接证明本轮终态由它造成。

## 后续与未达项

先完成本次旧 outer 的自然清理并保存终态，再用本次真实原件做最窄离线 RED/GREEN。Main/native/现成执行包在当前 outer 关闭前保持冻结，不手工改库、延长旧截止、重复原POST或再投真实模型。候选修复须有非作者复核、精确采用和必要新构建证据后才能释放新的研究。

这次不计合格研究，不计30例完整失败回放通过，不计 PAPER。原 PC/导出/重启、六组合14自然日、两真实事件族、条件通知和合格后继、10笔PAPER退出结算恢复、四周评分、最终strict/Ruff及发布交接均未完成；目标继续 ACTIVE。

## 后续纯恢复证据（不改写上述观察范围）

B 随后使用校验过的原 SDK `decodeStorageRecord`/`decodeSeqRanges`，将上述144个物理records展开为370个真实连续events，逐项seq==index（0—369），没有填洞。先前144是物理记录数，不是逻辑事件数量。原SDK `foldSurface`/`deriveEventMessage` 在seq351恢复15条surface，唯一protected marker为正式seq348。原件在 `D:/DecisionHub-v3-development/temp/legacy-reuse/eth4h-live-v6-retained-replay/surface-probe-result.json`；这不是重新调用Agent或模型。

另一项纯TS探针实际调用当前Main的声明读取与marker复用窄门：seq350/351/361/362的真实恢复surface均通过；仅在marker正文加空格的负例全部拒绝。它排除了这些具体版本识别/marker身份问题，尚未证明完整 `verifyRetainedRootContext` 投影与子会话证明链，也没有修复业务或计入30例通过。首个Windows fileURL探针加载失败另保留。

## outer 实际退出及源保全

Root 于UTC23:25:19再次轮询同一93659，实际观察 outer exit0；原900秒报告窗口自然结束。5个精确owned jobs均已关闭、各root退出已观察、PID/creation FILETIME与登记一致，cleanup errors=[]。151个原输入pins再次逐字核验未变。`root-terminal-observation.json` SHA `88584fe1b5e8766273d1e342656cddb259e0abab37e912f784008cfa08423388`。这是当前尝试的清理，不是全系统进程清查，也不改变业务degraded/报告409。

在这些Actor关闭之后，Root仅按字节把本实例两库的实际存在DB/WAL/SHM及四份SDK压缩日志复制到全新 `D:/DH-eth4h-replay-source01`。共8文件/23,540,781B，源前后与副本SHA逐项相等；源没有被SQLite打开，SQL0/源写0。`copy-receipt.json` SHA `e5cc76020c60621a71316f761fde948c824d29db1e6f1ce19d70960cf351fe0a`。这是后续RO回放所需的静止原件，不是完整可移植灾备，也不计业务回放通过。

## 完整原校验的离线 RED

C 使用原SDK恢复的实际surface/header、同一生产WeakSet认证的三个child source，以及另一只读事务取得的18条实际Evidence、原查询mapper `query_views.research.service._evidence_view` 的内存DTO，实际调用当前Main `verifyRetainedRootContext`。回放采用原失败事件时间，不改变OS时钟或原Task截止。没有用protected marker的证据列表制造expected。

生产verifier明确在 `retained-context.ts:348` 的substantive canonical equality拒绝；introduction与之前身份/marker门已通过。原marker为14Evidence/31Facts，fresh投影为15/35，仅evidence_candidates与facts不同。共同14/31全等，没有删除；新增一条Evidence及四条Facts。该Evidence现存原accepted_at为UTC23:05:06.862092，真实child工具结果seq92/time23:05:06.885Z，均早于marker348 append23:05:07.854Z，但可能晚于此前的detail读快照。当前定位方向是跨异步读取集合不一致及再鉴真吸收新增，不归因于v6字节损坏，仍须串联具体原回执和读取时序，不反推历史可变quality。

实际诊断结果 `D:/DecisionHub-v3-development/temp/legacy-reuse/eth4h-live-v6-retained-verify/verify-result.json`，SHA `e49491124d18f55bbee7db7b3f4ffbbff2978f69700a31e7d5ad4d989564e3ce`。Node exit0表示诊断脚本完成，内部业务verifier结果是FAIL；两次tsx辅助named-pipe连接被硬拒，原拒绝记录保留，没有外部HTTP/SQL/模型/Host。尚未修复、没有GREEN，不升级成完整30例回放通过。
