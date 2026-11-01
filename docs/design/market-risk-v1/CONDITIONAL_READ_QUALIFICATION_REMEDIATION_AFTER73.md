# 73 后条件性读取资格整改附录

2026-10-11。以下两项**均未实现、未取得真实作用域资格**；仅在 73 正常发布后的实际验收仍出现对应失败时启动。候选、提示或局部控制不授性能、Saved/ACK 或 G1–G6 通过。

## A. selected-private 原工厂 source lease

- 启动条件：同 Run 的新子阶段证据确认仍进入 selected V2 strict reader 并在其内失败；现有 reader 52.797 秒不足以定位内部热点。
- 最小落点：`packages/kernel/decision_hub_kernel/application/research_graph_history_versions.py` 的 `_private_v3_read_scope` / 私有模块读取边界，以及 `research_publication_runtime48.py` 的受管 host adapter。通过 `private.load(...)` 取得**原私有 factory 和原私有 guard**，在同 Session/run/collector、已认证 selected 声明源集上建立作用域。
- 不可替代：外层 current lease 不覆盖另一私有命名空间；ABI3 字节相同不等于 lease 已发行。不得共享/替换 ContextVar 或类，不改变 `research_source_snapshot` persistence/ports 与 `research_graph_binding` 三个 ABI 源，不扩大 Root 路径权限。
- 必要资格：实际原 factory、认证 selected 完整字节集、真实 Windows HANDLE 和干净 native RO snapshot；完整 entry/exit、注册/实现/身份校验。字节/路径/线程/事务/UOW/取消变化必须拒绝。当前缺少这一真实资格，不能直接把桥加入生产。
- 最小真实验收：资格及独审通过后，Root 选择一次正常发布与一次 owner 同 Run 整理；原预算内 capture、frozen replay、全部 fresh 出口及独立 Saved/ACK 身份均闭合。失败停在确切子阶段，不用新模型替代。

## B. RO qualification-consume 独立版本端口

- 启动条件：73 PC recovery/status 的实际读取仍超时，并证实重复完整资格读取需要收敛；收益目前未知。
- 最小落点：`application/research_publication_resources.py`、`application/research_publication_runtime48.py`、`application/research_publication_jobs48.py`，路径均位于上述 kernel 包。新增 server-only `consume(callback)` 版本契约，保留旧接口。
- 不可替代：现 `reader_abi_qualification`（runtime:705）在 `resolve` 的完整 fresh 出口（resources:236–261）之后返回；finally 仅撤 issuer。`retry_authority49` 也没有 callback 后完整 fresh 出口。FrozenDescriptor、material `verify(proof)` 不是正常 metadata 资格端口；先结束 qualifier 再读第二 head 不等价。
- 必要顺序：第一 RO snapshot 内原 resolve/ABI 完整 entry → server callback 独立打开、读取并关闭第二 RO snapshot、比较 head → 全资源字节/registry/registration/current-install/native 与 ABI fresh 出口 → 撤 issuer → 返回。第二 snapshot 不消费第一 Session、lease 或许可；两次 head 的原 tuple/fence/状态检查不缩减。
- 最小真实验收：实际两个独立 RO snapshot 正例；callback 期间 source/registration/head/fence 改变与 callback 异常均不得返回成功，issuer/HANDLE/Session 必须退出。正常 PC 原请求读取回读匹配且满足原时间门；不授重排、保存或金融资格。

两项不新增产品需求，不缓存授权，不提高期限/预算，不以缺证据转 pending。先完成 73 实际验收；无对应失败则不实施。
