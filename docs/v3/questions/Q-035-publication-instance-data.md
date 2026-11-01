# Q-035：为原发布服务显式配置实例数据根

- 状态：DECIDED，2026-10-24。按用户自主决策及保留原系统的授权实施独立小切片；不与 Q034 原页导出共享代码。
- 实证是 source-only 接缝核查：原 factory 固定把 stage 建在 Main 父目录、archive 建在 Main/artifacts；API/Worker 没有 override，`DECISION_HUB_DATA_DIR` 不控制它。隔离环境不应为此放行 Main 写入。当前 API 超时/原生异常根因仍未定位，此处不能冒作其因果修复。
- 依据：[原件与约束](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-next-step/publication-data-paths.md)。低层 preparer 与 BundleArchive 已能分别接收源根、stage、archive；源 Root、registry/issuer 身份继续是真实 Main。same-volume 是现有注释，不能据此声称所有跨卷路径已经验证。

## 精确范围

D 隔离实现只允许三个现有生产文件：

- `packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py`
- `apps/hub_api/main.py`
- `apps/hub_worker/main.py`

新增测试限定 `tests/decision_product/test_publication_data_paths.py`；必要的 ≤400 行拆分 support 须先登记具体文件。先保 D 旧字节和当前 Main SHA，逐字接入这三份 Main 基线并精确提交，不把 D 的旧 Git 后端当当前 Main。不要带入 Q034 未提交 UI 文件。

原 DB/迁移、publication schema、Root/history/recipe 注册与鉴真、issuer、SQL/CAS、归档字节/hash、旧测试断言、前端、正式库只读。不新增依赖；新函数 ≤60 行。既有大模块与既存 strict 诊断如实保留，不扩成旧模块全盘重写。

## 最小行为

1. factory 增加可选 `publication_data_root=None`。默认仍用原 stage/archive 布局，原调用形状及鉴真前置条件保持。
2. API/Worker 只在明确设置 `DECISION_HUB_PUBLICATION_DATA_DIR` 时传实例数据根；未设置时保留原两个关键字参数调用。两入口指同一显式根，不能创建第二默认数据副本或静默迁移历史数据。
3. 显式根必须为绝对路径、位于真实源码 Root 外、拒绝 symlink/reparse/别名与已有非目录；stage 与 archive 位于该实例根内。新 D 实例使用新私有目录；原 Main/registry/issuer Root 不替换为 D，不弱化 source fallback 或放开正式路径写权限。
4. 只配置可变数据位置；不改变 readset、archive 生命周期、提交协议、旧归档读取口径、时钟、quote age 或超时。跨卷实际发布需后续原链路验证，不因构造器接受路径就宣称成功。

## TDD 与验收

先记录真实 RED 再实现，必要四类断言：默认路径/调用兼容；显式新 D 根实际创建与 archive/stage 归属且源 Root 不变；相对/Root 内/链接及错误类型路径拒绝；API/Worker 显式配置一致且缺省不传新关键字。验证不修改 Main 或正式产物，测试边界明示是否使用构造器替身，不冒真实 Root 资格或完整发布。

当前重测试队列归 Q034；本切片先基线、测试和代码准备，得到队列后才运行 focused RED/GREEN/必要静态。非作者复核、精确提交后才考虑 Main 源集成（旧 SHA、备份、受保护服务检查）；不直接部署原服务、profile 或正式库。没有付费模型或业务重试授权，仍按失败原件定位推进，原 45s 和 5s 时限不改。

本配置能力是后续隔离官方 Host 验证的接缝，不替代未定位的 API 原生异常、真实 Host/live-root/Provider、PAPER 或长期指标；目标 ACTIVE，不新增 ACCEPTED。


## 2026-10-24 实施续记

精确三源＋新测试提交 `d299070`；真实 RED18失败/2通过/1跳过→GREEN20通过/1跳过3.65s，最终仅测试断行且 AST相同，新测试 strict/Ruff0，旧三模块 Ruff96/11/8与基线相同；非作者最终 C0/I0。已备份接回 Main 本地源码，未应用新环境或部署。构造器替身、真实 symlink跳过、无完整发布/API启动验收等边界见 [Q035报告](../reports/INCREMENTAL_PUBLICATION_INSTANCE_DATA.md)。旧 Host 输入 pin 是集成前历史，不能原包重跑；此实现不冒为 API 异常根因修复。
