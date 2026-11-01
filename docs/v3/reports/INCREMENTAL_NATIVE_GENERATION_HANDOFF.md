# 原生控制消息跨 generation 交接修复

当前仅 D 私有候选，未采用 Main，未运行新研究。原10仍为两次 needs_revision、报告 GET 409，不将控制器退出0视为业务成功。

## 实际断点

原10 Run `run_32303478bf3340399bbdc66fb4a62030` 第二次负面独审后，根 session 在 `2026-10-26T07:36:51.369Z` 正常结束当前 turn。新 preparation 提交后进入 generation2；实际 SDK seq30830 追加原 generation2 RPC prompt，seq30831 以 `surfaceOp:append` 追加新的完整 V6 control。官方 SDK fold 后38条 surface message 中同时存在 generation1、2两份 `decision-hub-protected-context`。随后 seq30834 在 `07:38:02.765Z` 阻断。

`D:/DH-native-generation01/actual-red.mts` 用原 SDK 解码与完整 surface、真实原 prompt 字节和当时已成功 model-binding 的不可变身份字段，调用物理 Main 原 verifier，首个异常精确为 `retained-context.ts:313` 的唯一 protected message 门。此回放只证明首谓词，不冒完整 fresh Link/资料投影重建；未改原 expected、业务时间或原件。结果 `actual-red-result.json` SHA `ab5f955eebee93fa1ce2edc005d4205e584917850d13451d80701a339e220e3d`。

闭源复制在 Root76404实际终态后完成。第一次将可查询的已退出 Windows kernel object 误判为运行中，未复制即失败，原件保留；r02用 exact FILETIME、GetExitCodeProcess非STILL_ACTIVE及原 OwnedJob closed 证明所有10个角色结束，39文件 before/copy/after字节相同。复制证明 SHA `ed8b1c265efe28fe99ac48e1c572884dfb7da46b1caa2174772997ba7375349a`。原10 SQLite 未打开。

## 最小生产变更

白名单只有：

- `extensions/dsh/decision-hub/src/host/bridge.ts`：仅 generation>1、当前 RPC 未 append 的分支，交接前代后重读真实 context，原 deadline/correlation/current fences 保留。
- `extensions/dsh/decision-hub/src/host/retained-context.ts`：仅 export 原 `protectedOriginal`，函数体和整个 Provider verifier 字节不变。
- 新 `extensions/dsh/decision-hub/src/host/retained-context-handoff.ts`：完整新投影先校验；实际 Session/events全等；唯一前代原件逐字匹配；同 run/session/request_hash/build、严格 generation-1 与 deterministic RPC、原节点 seq/prefix均校验。使用原 tokenMeter 估价及官方相邻 `compaction/prune`、`user/message surfaceOp:replace`，把前代控制替换成专用 plugin 的历史交接说明。

历史说明只保留身份和旧 marker 的 seq/hash，不携研究事实、预算或权限。完整旧事件保留。新完整 claimed snapshot 在重读真实 handoff/prune 前缀后生成，仍由原 SDK 按 `step/start → 原完整 RPC prompt → 新 control` 追加。未改313/338/348，没有未来seq、Verifier过滤、截短当前控制、权限缓存或交付真相库。

如果原 SDK 已合法 compaction 移走前代 control，新鲜投影与 Session/events仍逐项核验；没有待退休节点时返回false，不要求无用的 tokenMeter、不改历史。两份以上 control仍拒绝。此兼容边界由一项原完整 projection/verifier正例和一项真实 RED 后最小修复证明。

## 定向验证

测试只新增三文件，全部 repo 相对 imports。官方 SDK 实体由既有 `DSH_SESSION_ENTRY` 或测试自身路径定位；D配置只将三个候选和缺失邻居分别解析到真实 D/Main文件，无模块身份伪造。Hub/selectedSkill边界是明确 synthetic facade；Session/fold/append、Bridge分支、资料鉴真和完整 verifier均是实际源码。

1. 原分支与官方 Session：真实2FAIL，完整 verifier在313拒绝两 control。`red-r02.log`，实际退出1；先前config导入启动错误独立保留，不计RED。
2. 首次小修后21PASS。一个测试计数器因可选callback未自增的装配FAIL保留并修正，不作产品失败。
3. 官方合法 compaction 原路径正例PASS，新helper缺少不必要meter时1FAIL；`red-compacted.log`，实际退出1。
4. 修兼容边界后新23PASS；最终类型补齐后的新23PASS/11.52s/exit0，`focused-final.log`。
5. 原 host60、command-cancellation11、child-attributes22，加新23，共116PASS/19.37s/exit0，`related.log`。原93断言与文件未修改；未重复大 native 或业务链。
6. 类型检查沿原 Main strict/noEmit配置和8192MiB；仅 rootDirs定位真实候选/依赖，未放宽规则。`types-final-r02-terminal.json` 实际退出0/115.594秒，无诊断。先前7个、再1个新测试类型声明诊断均保留，不隐作通过；三个生产候选没有类型诊断。最后一次仅补测试 object→Record 索引窄化，生产源与116PASS时逐字不变。

新用例覆盖 generation1不变、2/3合法交接、官方 Session.fromRestore重建、全旧native bytes保持、真实shadow token定价和实际prefix；foreign/skipped/stale、多control、surface篡改、native前缀变化、无效tokenMeter、新资料变化、漏真实handoff前缀、重建后篡改当前control均拒绝。

## 证据边界

全部新增产物只在 D，未执行 SQL/HTTP/模型/服务/业务状态写入，Main/原10及其失败原件未改。私有验证不替代采用后标准构建或下一次真实研究验收。独立复审/采用由 Root 后续处理。
