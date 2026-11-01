# Q-027：按实际证据修复原系统，保留原产品界面

- 状态：DECIDED，2026-10-24。用户指出前端发生变化，询问为何不能在原代码上修改，并明确要求执行者自主判断：“文档里的内容可能不符合实际情况，但是文档里提出的一些问题是客观存在的，要判断下”。延续Q-022，不再把全重建方案当作已经证明的必要条件。
- 核查结论：保留原 `apps/decision-desk` 导航、白蓝样式、设置、研究回执、报告、资料、事件与PAPER入口。原worker具有 `has_checkpoint → executor.resume`、持久checkpointer、租约恢复与审核补证实现；“完全无断点恢复”是过度概括，存在实现不等于已完成运行验收。原报告依赖历史源码的读链有具体源码依据，另按局部责任修复。
- 第一切片只关闭已证DSH合法追加竞态。当前Main `bridge.ts` SHA为 `823a119f80f6b80857abf6117810537049a298d85035d9944c18803dd36ed809`，与原封包before相同；已存在净18行单文件补丁，完整重读上下文，保持原deadline/current、原件鉴真、Provider实际输入、权限和compaction协议。不能只放宽全等条件、把新裸事件当成已认证证据，也不能称此缺陷为所有历史失败的唯一原因。
- 实施在 `D:/DecisionHub-legacy-incremental`、分支 `repair/incremental-delivery`。基线提交 `fd75223acb2f3fc2e8475c78f35481058c014b68` 保留Main最新未提交的前端/native/contracts及三个根清单，共454工作文件、21,783,426字节，逐文件SHA相等；不是仅从旧HEAD重建后忽略当前未提交改动。其余Python后端仍是旧Git基点，不能称完整生产克隆。缺少的明确旧测试/展示fixture只按原字节补齐，并留独立收据。
- 原Main源码、原服务8047/3097、正式数据库和历史原件不在这次补丁中改写。先从单Run只读查询与三份封存archive生成D闭合fixture，隔离RED/GREEN、实际SDK离线协议检查、影响测试及非作者复核；再形成可审查发布候选。实际付费研究仍须真实UTC健康和明确的单次验收登记，不复用封存T-303批次或回填时间。
- 已完成v3代码、录制、报告和测试保留为研发资产，按原系统的具体缺口选择性复用。停止继续扩建新PC外壳和未闭合的新链；T404-B1保留未完成WIP，T504-A/T405保持冻结，不能冒充原系统已接入或正式完成。也不先搭一个覆盖所有旧API的大翻译层。
- 原有客观业务目标继续保留：研究深度、原件/as-of、独审、费用与资金费、PAPER真实完整退出/恢复、可回读报告，以及真实自然时间的统计。任务清单作为覆盖清单，不能以55个“完成”标记替代用户可用交付，也不能因复用旧代码就默认旧能力均已通过。
- 审查证据：`D:/DecisionHub-v3-development/temp/legacy-reuse/frontend-audit.md`、`backend-audit.md`、`dsh-defect-audit.md`及各自源SHA收据；原native封包9件独立hash核验见 `D:/DecisionHub-v3-development/evidence/legacy-context-review/packet-audit.json`。这些只读结论与尚未执行的部署、模型/真实长期验收分别记录。
