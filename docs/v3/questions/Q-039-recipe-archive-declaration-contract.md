# Q-039：恢复原 recipe 的归档声明兼容性

状态：DECIDED，2026-10-24。根据用户自主裁决授权执行，不等待例行批准。

## 实际根因

Q038 唯一一次原 build-only 在 retention 阶段失败：`research_recipe_archive_initial_declaration_invalid`。UI/Host 构建均未开始。原归档器用有限 AST 读取 builder 内唯一的 `initial = <literal tuple>`；Q028 缓存优化把六个入口移到模块级，破坏了这个既有声明契约。完整失败见 INCREMENTAL_NORMAL_BUILD.md 与 q038-normal-build/final-receipt.json，不能把准备材料齐全称构建通过。

## 最小范围

- 生产仅 `packages/kernel/decision_hub_kernel/application/research_graph_recipe.py`。先保留 D 未采用的 Q030 实验原件，逐字恢复真实 Main 当前 5ce02ead…0f9d16 基线并单独提交；不是采纳 D 旧实验。
- 新增 `tests/research/test_graph_recipe_archive_contract.py`，可只为运行此测试新增 D 私有执行器/静态配置及证据，不伪造模块来源。
- 先用真实原归档器对实际物理候选源复现 RED，再恢复 builder 本地六入口 literal 声明；正常遍历该声明，global 动态改变时保留原 cold builder 的实际根输入，不改变既有两项 root-rebind 和不合格 root fallback 契约。用源码回归验证静态 global 声明与归档 finite declaration 一致，不加破坏现有契约的运行时强制相等门。保留 memo 失效、readset、完整归档、权限、SQL/CAS、原超时与数量边界。
- 原 `research_recipe_archive.py` 只读；不放宽其 AST/来源/容量校验，不修改旧断言。运行新回归和相关原 memo 26 项、必要局部静态及非作者 review；不重跑未改 UI98/native556 或无新性能理由的 sameRun。
- GREEN/review 后，核 Main 旧 SHA、完整备份及 8047/3097 无监听，仅把已审 recipe 和新测试接回 Main 本地源码。原 profile、installed pointers、正式数据库不改。

## 下一次实际构建

本根因修复后，允许以全新 `Main/artifacts/incremental-delivery-20261024/validated-build/` 执行一次原 `infra/dsh/build-product.ps1` build-only。Q038 的失败目录完整保留；沿用真实 prior49c path/SHA 2bc144fc…567e6 与已补齐 original-sources18，重新核关键 producer/pointer/71 native 输入/旧输出与备份。构建仅使用 Q038 已授权的正常输出/计算得出的不可变归档；不 install/更新依赖、不 Spot/capture/DB、不 run-product 或部署。任何失败先定位，不自动再跑。

## 验收边界

测试通过只证明声明契约和 memo 回归；新构建必须以真实终态和 guard 输出判断，不能自造 receipt。正常 build-only 通过仍不是 API/Host、金融研究、sameRun5s、整体 strict0、真实 PAPER/14 天/四周验收。目标 ACTIVE。

## 实际实施与后续终态

69854fd两源修复已审、28PASS、精确接Main；类型检查旧suppression边界如实记录，Q040另补精确注解。Q039唯一新normal build 791.518s/exit1，retention/assessment真实passed、UI1/134默认4GiB OOM、Host0/no receipt，525/71/18/具名元数据前后同；见 INCREMENTAL_NORMAL_BUILD_AFTER_ARCHIVE_FIX.md。失败目录保留。旧D成功实际曾显式8GiB，不是默认4GiB通过；Q042另定有限工程资源配置，原业务守门不放松。
